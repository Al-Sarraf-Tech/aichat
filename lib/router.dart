import 'dart:async';
import 'dart:io';

import 'package:logging/logging.dart';
import 'package:path/path.dart' as p;
import 'package:shelf/shelf.dart'
    show Cascade, Handler, Middleware, Pipeline, Request, Response;
import 'package:shelf_router/shelf_router.dart' show Router;

import 'api_client.dart';
import 'chat_handler.dart';
import 'compaction.dart';
import 'config.dart';
import 'database.dart';
import 'image_handler.dart';
import 'llm_client.dart';
import 'mcp_client.dart';
import 'model_handler.dart';
import 'personalities.dart';
import 'router_helpers.dart' as helpers;

final _log = Logger('Router');

class AppRouter {
  final Config config;
  final AppDatabase db;
  final LlmClient llm;
  final McpClient mcp;
  final Compactor compactor;
  final ApiClient apiClient;
  late final ModelHandler _models;
  late final ImageHandler _images;
  late final ChatHandler _chat;

  late final Router _router;

  AppRouter({
    required this.config,
    required this.db,
    required this.llm,
    required this.mcp,
    required this.compactor,
    ApiClient? apiClient,
  }) : apiClient = apiClient ?? ApiClient() {
    _models = ModelHandler(config: config, llm: llm, mcp: mcp);
    _images = ImageHandler(config: config, mcp: mcp);
    _chat = ChatHandler(
      config: config,
      db: db,
      llm: llm,
      mcp: mcp,
      compactor: compactor,
    );
    _router = Router()
      ..get('/health', _health)
      ..get('/api/stack-health', _stackHealth)
      ..get('/api/conversations', _listConversations)
      ..post('/api/conversations', _chat.createConversation)
      ..get('/api/conversations/<id>', _getConversation)
      ..delete('/api/conversations/<id>', _deleteConversation)
      ..patch('/api/conversations/<id>', _updateConversation)
      ..post('/api/conversations/<id>/messages', _chat.sendMessage)
      ..get('/api/tools', _listTools)
      ..post('/api/tools/refresh', _refreshTools)
      ..get('/api/models', _models.listModels)
      ..get('/api/model-status', _models.modelStatus)
      ..get('/api/personalities', _listPersonalities)
      ..post('/api/warmup', _models.warmupModel)
      ..post('/api/unload', _models.unloadModel)
      ..get('/api/image/status', _images.imageStatus)
      ..get('/api/image/models', _images.imageModels)
      ..post('/api/image/generate', _images.imageGenerate)
      ..get('/api/image/job/<jobId>', _images.imageJobStatus)
      ..get('/api/image/download/<filename>', _images.imageDownload)
      ..get('/api/search', _searchMessages)
      ..get('/api/providers', _listProviders)
      ..post('/api/image/search-reference', _images.imageSearchReference);
  }

  Handler get handler {
    final cascade = Cascade().add(_router.call).add(_staticHandler);
    return const Pipeline()
        .addMiddleware(_cors())
        .addMiddleware(_authGuard())
        .addHandler(cascade.handler);
  }

  // ── Static file handler ────────────────────────────────────────────

  FutureOr<Response> _staticHandler(Request request) {
    var filePath = request.url.path;
    if (filePath.isEmpty || filePath == '/') filePath = 'index.html';

    // Resolve the absolute path and verify it stays within webDir
    // to prevent path traversal attacks (e.g. ../../etc/passwd).
    final webRoot = p.canonicalize(config.webDir);
    final resolved = p.canonicalize(p.join(config.webDir, filePath));
    if (!p.isWithin(webRoot, resolved) && resolved != webRoot) {
      return Response.forbidden('Forbidden');
    }

    final file = File(resolved);
    if (!file.existsSync()) {
      // SPA fallback — exclude API paths so they get proper 404 JSON
      final index = File(p.join(config.webDir, 'index.html'));
      if (!filePath.startsWith('api/') && index.existsSync()) {
        return Response.ok(
          index.openRead(),
          headers: {'Content-Type': 'text/html; charset=utf-8'},
        );
      }
      return Response.notFound('Not found');
    }

    final ext = p.extension(filePath).toLowerCase();
    final contentType = _mimeType(ext);
    return Response.ok(
      file.openRead(),
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'no-cache, no-store, must-revalidate',
      },
    );
  }

  String _mimeType(String ext) {
    switch (ext) {
      case '.html':
        return 'text/html; charset=utf-8';
      case '.css':
        return 'text/css; charset=utf-8';
      case '.js':
        return 'application/javascript; charset=utf-8';
      case '.json':
        return 'application/json; charset=utf-8';
      case '.png':
        return 'image/png';
      case '.jpg':
      case '.jpeg':
        return 'image/jpeg';
      case '.svg':
        return 'image/svg+xml';
      case '.ico':
        return 'image/x-icon';
      default:
        return 'application/octet-stream';
    }
  }

  // ── CORS middleware ─────────────────────────────────────────────────

  Middleware _cors() {
    return (Handler innerHandler) {
      return (Request request) async {
        if (request.method == 'OPTIONS') {
          return Response.ok('', headers: _corsHeaders);
        }
        final response = await innerHandler(request);
        return response.change(headers: _corsHeaders);
      };
    };
  }

  static final Map<String, String> _corsHeaders = helpers.corsHeaders;

  // ── Auth Guard ─────────────────────────────────────────────────────
  // Defense-in-depth: reject /api/* requests without X-Auth-User header.
  // The upstream auth proxy (aichat-auth) sets this header after JWT validation.
  // /health is exempt (used by Docker healthcheck).

  Middleware _authGuard() {
    return (Handler innerHandler) {
      return (Request request) {
        final path = request.url.path;
        if (path.startsWith('api/') && !path.startsWith('api/health')) {
          final user = request.headers['x-auth-user'];
          if (user == null || user.isEmpty) {
            return Response(401,
                body: '{"error":"Unauthorized"}',
                headers: {'Content-Type': 'application/json', ..._corsHeaders});
          }
        }
        return innerHandler(request);
      };
    };
  }

  // ── Delegate helpers to extracted modules ────────────────────────

  String _getUserId(Request request) => helpers.getUserId(request);
  Response _json(Map<String, dynamic> data, {int status = 200}) =>
      helpers.jsonResponse(data, status: status);
  Future<Map<String, dynamic>?> _readJson(Request request) =>
      helpers.readJson(request);
  // ── API Handlers ───────────────────────────────────────────────────

  Response _health(Request request) {
    return _json({
      'ok': true,
      'service': 'dartboard',
      'version': '2.0.0',
      'lm_studio': config.lmStudioUrl,
      'mcp': config.mcpUrl,
    });
  }

  /// Aggregated health check — probes all backend services from a single call.
  /// Returns per-service status so the frontend or monitoring can show a dashboard.
  Future<Response> _stackHealth(Request request) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 3);
    final results = <String, dynamic>{};

    Future<String> probe(String name, String url) async {
      try {
        final req = await client.getUrl(Uri.parse(url));
        final resp = await req.close().timeout(const Duration(seconds: 3));
        if (resp.statusCode == 200) return 'ok';
        return 'error:${resp.statusCode}';
      } catch (e) {
        return 'unreachable';
      }
    }

    // Probe all services in parallel
    final probes = await Future.wait([
      probe('mcp', '${config.mcpUrl}/health'),
      probe('lm_studio', '${config.lmStudioUrl}/v1/models'),
      probe('data', 'http://aichat-data:8091/health'),
      probe('vision', 'http://aichat-vision:8099/health'),
      probe('docs', 'http://aichat-docs:8101/health'),
      probe('sandbox', 'http://aichat-sandbox:8095/health'),
      probe('browser', 'http://aichat-browser:8104/health'),
      probe('inference', 'http://aichat-inference:8105/health'),
      probe('jupyter', 'http://aichat-jupyter:8098/health'),
    ]);

    final names = ['mcp', 'lm_studio', 'data', 'vision', 'docs', 'sandbox', 'browser', 'inference', 'jupyter'];
    for (var i = 0; i < names.length; i++) {
      results[names[i]] = probes[i];
    }
    results['web'] = 'ok'; // we're responding, so we're alive

    final allOk = probes.every((s) => s == 'ok');
    return _json({
      'ok': allOk,
      'services': results,
      'checked_at': DateTime.now().toUtc().toIso8601String(),
    });
  }

  Response _listConversations(Request request) {
    final userId = _getUserId(request);
    final limit =
        (int.tryParse(request.url.queryParameters['limit'] ?? '') ?? 50).clamp(1, 100);
    final offset =
        (int.tryParse(request.url.queryParameters['offset'] ?? '') ?? 0).clamp(0, 10000);
    final convs = db.listConversations(
        userId: userId, limit: limit, offset: offset);
    return _json({'conversations': convs.map((c) => c.toJson()).toList()});
  }

  Response _getConversation(Request request, String id) {
    final userId = _getUserId(request);
    final conv = db.getConversation(id, userId: userId);
    if (conv == null) return _json({'error': 'Not found'}, status: 404);
    final messages = db.getMessages(id);
    return _json({
      ...conv.toJson(),
      'messages': messages.map((m) => m.toJson()).toList(),
    });
  }

  Response _deleteConversation(Request request, String id) {
    final userId = _getUserId(request);
    db.deleteConversation(id, userId: userId);
    return _json({'status': 'deleted'});
  }

  Future<Response> _updateConversation(Request request, String id) async {
    final userId = _getUserId(request);
    // Verify ownership before updating
    final existing = db.getConversation(id, userId: userId);
    if (existing == null) return _json({'error': 'Not found'}, status: 404);
    final body = await _readJson(request);
    if (body == null) return _json({'error': 'Invalid JSON'}, status: 400);
    db.updateConversation(
      id,
      title: body['title'] as String?,
      model: body['model'] as String?,
      systemPrompt: body['system_prompt'] as String?,
    );
    final conv = db.getConversation(id, userId: userId);
    if (conv == null) return _json({'error': 'Not found'}, status: 404);
    return _json(conv.toJson());
  }

  Response _listPersonalities(Request request) {
    final model = request.url.queryParameters['model'];
    return _json({'personalities': personalityIndex(model: model)});
  }

  Future<Response> _listTools(Request request) async {
    final tools = await mcp.getTools();
    return _json({
      'tools': tools.map((t) => t.toJson()).toList(),
      'count': tools.length,
    });
  }

  /// Force re-initialize MCP connection and refresh all tools.
  /// Useful after MCP container restarts or stack changes.
  Future<Response> _refreshTools(Request request) async {
    _log.info('Manual tool refresh requested');
    final tools = await mcp.reinitialize();
    return _json({
      'status': tools.isNotEmpty ? 'ok' : 'error',
      'tools': tools.length,
      'initialized': mcp.isInitialized,
    });
  }

  // ── Search Messages ───────────────────────────────────────────────

  Response _searchMessages(Request request) {
    final userId = _getUserId(request);
    var query = request.url.queryParameters['q'] ?? '';
    if (query.length > 200) query = query.substring(0, 200);
    final limit =
        (int.tryParse(request.url.queryParameters['limit'] ?? '') ?? 20).clamp(1, 50);

    if (query.length < 2) {
      return _json({'results': [], 'error': 'Query too short'});
    }

    final results = db.searchMessages(
      query: query,
      userId: userId,
      limit: limit,
    );
    return _json({'results': results});
  }

  // ── Providers ───────────────────────────────────────────────────

  Response _listProviders(Request request) {
    return _json({
      'anthropic': false,
      'openai': false,
      'google': false,
    });
  }
}
