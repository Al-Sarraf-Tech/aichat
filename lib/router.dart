import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:logging/logging.dart';
import 'package:path/path.dart' as p;
import 'package:shelf/shelf.dart'
    show Cascade, Handler, Middleware, Pipeline, Request, Response;
import 'package:shelf_router/shelf_router.dart' show Router;

import 'compaction.dart';
import 'config.dart';
import 'database.dart';
import 'llm_client.dart';
import 'mcp_client.dart';
import 'model_profiles.dart';
import 'models.dart';
import 'personalities.dart';
import 'api_client.dart';
import 'image_handler.dart';
import 'model_handler.dart';
import 'router_helpers.dart' as helpers;
import 'sanitizer.dart' as sanitizer;
import 'tool_router.dart' as tool_router;

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
    _router = Router()
      ..get('/health', _health)
      ..get('/api/conversations', _listConversations)
      ..post('/api/conversations', _createConversation)
      ..get('/api/conversations/<id>', _getConversation)
      ..delete('/api/conversations/<id>', _deleteConversation)
      ..patch('/api/conversations/<id>', _updateConversation)
      ..post('/api/conversations/<id>/messages', _sendMessage)
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
  Future<T> _withKeepalive<T>(
    StreamController<List<int>> controller, Future<T> Function() work,
  ) => helpers.withKeepalive(controller, work);
  void _sseEvent(
    StreamController<List<int>> controller, String event,
    Map<String, dynamic> data,
  ) => helpers.sseEvent(controller, event, data);
  String _sanitizeToolResult(String text) =>
      sanitizer.sanitizeToolResult(text);
  List<String> _extractImageUrls(String text) =>
      sanitizer.extractImageUrls(text);
  Map<String, dynamic> _inferToolArgs(String toolName, String userText) =>
      sanitizer.inferToolArgs(toolName, userText);

  // ── API Handlers ───────────────────────────────────────────────────

  Response _health(Request request) {
    return _json({
      'ok': true,
      'service': 'dartboard',
      'version': '1.0.0',
      'lm_studio': config.lmStudioUrl,
      'mcp': config.mcpUrl,
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

  Future<Response> _createConversation(Request request) async {
    final body = await _readJson(request);
    final customPrompt = body?['system_prompt'] as String?;
    final personalityId = body?['personality_id'] as String?;

    // Determine model first (needed for prompt sizing)
    var requestedModel = body?['model'] as String?;
    if (requestedModel == null || requestedModel.isEmpty) {
      final loaded = await llm.listLoadedModels();
      if (loaded.isNotEmpty) {
        requestedModel = loaded.first;
        _log.info(
          'No model specified — defaulting to loaded model: $requestedModel',
        );
      } else {
        requestedModel = config.model;
      }
    }

    // Build system prompt — use condensed version for small-context models
    String systemPrompt;
    if (customPrompt != null && customPrompt.isNotEmpty) {
      systemPrompt = customPrompt;
    } else {
      final modelProfile = getProfile(requestedModel);
      systemPrompt = buildSystemPrompt(
        personalityId ?? 'general',
        condensed: modelProfile.promptSize == 'condensed',
      );
      // Optimize prompt on Arc A380 for condensed models
      if (modelProfile.promptSize == 'condensed' &&
          config.toolRouterUrl.isNotEmpty) {
        systemPrompt = await tool_router.optimizePrompt(
          systemPrompt,
          promptSize: modelProfile.promptSize,
          personalityId: personalityId ?? 'general',
          routerUrl: config.toolRouterUrl,
        );
      }
    }

    final userId = _getUserId(request);
    final conv = db.createConversation(
      userId: userId,
      title: body?['title'] as String? ?? 'New Chat',
      model: requestedModel,
      systemPrompt: systemPrompt,
    );
    // Add system prompt as first message
    db.addMessage(
      conversationId: conv.id,
      role: 'system',
      content: conv.systemPrompt.isNotEmpty
          ? conv.systemPrompt
          : config.systemPrompt,
    );
    return _json(conv.toJson(), status: 201);
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

  Future<Response> _sendMessage(Request request, String id) async {
    final userId = _getUserId(request);
    final conv = db.getConversation(id, userId: userId);
    if (conv == null) return _json({'error': 'Not found'}, status: 404);

    final body = await _readJson(request);
    var userContent = body?['content'] as String? ?? '';

    // Process file attachments
    final rawFiles = body?['files'] as List?;
    List<Map<String, dynamic>>? imageAttachments;

    if (rawFiles != null && rawFiles.isNotEmpty) {
      imageAttachments = [];
      final textParts = StringBuffer();

      for (final f in rawFiles) {
        final file = Map<String, dynamic>.from(f as Map);
        final name = file['name'] as String? ?? 'file';
        final type = file['type'] as String? ?? '';
        final data = file['data'] as String? ?? '';

        if (type.startsWith('image/')) {
          imageAttachments.add({'type': 'image', 'data': data, 'name': name});
          textParts.writeln('[Attached image: $name]');
        } else {
          // Text file — inline content into message
          textParts.write('\n\n---\nFile: $name\n```\n$data\n```');
        }
      }

      if (textParts.isNotEmpty) {
        userContent = '$userContent\n${textParts.toString()}'.trim();
      }
      if (imageAttachments.isEmpty) imageAttachments = null;
    }

    if (userContent.isEmpty) {
      return _json({'error': 'content is required'}, status: 400);
    }

    // Store user message
    db.addMessage(conversationId: id, role: 'user', content: userContent);
    db.updateTokenCount(id);

    // Auto-generate title from first user message
    if (conv.title == 'New Chat') {
      final title = userContent.length > 50
          ? '${userContent.substring(0, 50)}...'
          : userContent;
      db.updateConversation(id, title: title);
    }

    // Determine which model to use for this conversation.
    // If the conversation has an explicit model (user picked it), use it.
    // LM Studio JIST will auto-load/swap as needed.
    var effectiveModel = conv.model.isNotEmpty ? conv.model : config.model;

    // Only fall back to the loaded model when NO model is specified at all.
    if (effectiveModel.isEmpty) {
      final loaded = await llm.listLoadedModels();
      if (loaded.isNotEmpty) {
        effectiveModel = loaded.first;
        _log.info('No model specified — using loaded model: $effectiveModel');
        db.updateConversation(id, model: effectiveModel);
      }
    }

    // ── Standalone API routing ──────────────────────────────────────
    // api:* models route directly to cloud providers (Anthropic/OpenAI/Google).
    if (effectiveModel.startsWith('api:')) {
      return _json({'error': 'Cloud API models are disabled (no API keys configured)'},
          status: 400);
    }

    // ── CLI/OAuth model routing ──────────────────────────────────────
    // Cloud models (claude:*, codex:*, gemini:*, qwen) bypass LM Studio
    // and route through MCP to their respective CLI agents.
    if (_isCliModel(effectiveModel)) {
      final controller = StreamController<List<int>>();
      _runCliChat(
        id,
        effectiveModel,
        userContent,
        controller,
        imageCount: imageAttachments?.length ?? 0,
      ).catchError((e) {
        _log.severe('CLI chat error: $e');
        _sseEvent(controller, 'error', {'message': '$e'});
        if (!controller.isClosed) controller.close();
      });
      return Response.ok(
        controller.stream,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
          ..._corsHeaders,
        },
      );
    }

    // Capacity guard: if the model is not loaded and slots are full, reject
    final busyMsg = await llm.ensureModelOrBusy(
      effectiveModel,
      maxLoaded: config.maxLoadedModels,
    );
    if (busyMsg != null) {
      final busyController = StreamController<List<int>>();
      _sseEvent(busyController, 'error', {'message': busyMsg});
      busyController.close();
      return Response.ok(
        busyController.stream,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
          ..._corsHeaders,
        },
      );
    }

    // Check compaction
    await compactor.compactIfNeeded(id, model: effectiveModel);

    // Check if tools are enabled for this request
    final useTools = body?['tools_enabled'] as bool? ?? true;

    // Build SSE stream
    final controller = StreamController<List<int>>();

    // Let JIST handle model loading transparently.
    // No preflight — it races with JIST swaps and causes false rejections.
    if (!(await llm.isModelLoaded(effectiveModel))) {
      _sseEvent(controller, 'status', {
        'text': 'Loading $effectiveModel...',
      });
    }

    // Run the LLM loop asynchronously
    _runChatLoop(
      id,
      effectiveModel,
      controller,
      imageAttachments: imageAttachments,
      useTools: useTools,
    ).catchError((e) {
      _log.severe('Chat loop error: $e');
      _sseEvent(controller, 'error', {'message': '$e'});
      if (!controller.isClosed) controller.close();
    });

    return Response.ok(
      controller.stream,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        ..._corsHeaders,
      },
    );
  }

  /// Check if a model ID routes to a CLI/OAuth agent via MCP.
  static bool _isCliModel(String model) {
    return model.startsWith('claude:') ||
        model.startsWith('codex:') ||
        model.startsWith('gemini:') ||
        model == 'qwen';
  }

  /// Route a message to a CLI agent via MCP chat tool.
  Future<void> _runCliChat(
    String conversationId,
    String cliModel,
    String userContent,
    StreamController<List<int>> controller, {
    int imageCount = 0,
  }) async {
    // Parse model ID: "claude:opus:max" or "codex::high" or "gemini:gemini-2.5-pro" or "qwen"
    final parts = cliModel.split(':');
    final agent = parts[0]; // claude, codex, gemini, qwen
    final modelVersion = parts.length > 1 ? parts[1] : '';
    final effort = parts.length > 2 ? parts[2] : '';
    _log.info('CLI chat: agent=$agent model=$modelVersion effort=$effort');

    _sseEvent(controller, 'status',
        {'text': 'Routing to $agent${modelVersion.isNotEmpty ? ' ($modelVersion)' : ''}...'});

    String context = '';
    if (imageCount > 0) {
      context =
          'User attached $imageCount image(s) but chat does not yet support image input.';
    }

    try {
      // Stream tokens directly from MCP → SSE to frontend
      final accumulator = StringBuffer();
      await for (final chunk in mcp.callToolStreamingWithRecovery('chat', {
        'message': userContent,
        'agent': agent,
        if (context.isNotEmpty) 'context': context,
        if (modelVersion.isNotEmpty) 'model': modelVersion,
        if (effort.isNotEmpty) 'effort': effort,
      })) {
        accumulator.write(chunk);
        _sseEvent(controller, 'token', {'text': chunk});
      }

      final fullText = accumulator.toString();
      if (fullText.isEmpty) {
        _sseEvent(controller, 'token', {'text': 'No response from $agent.'});
      }

      // Check for error markers in accumulated text
      if (RegExp(r'(^|\n\n)❌ Error:').hasMatch(fullText)) {
        _sseEvent(controller, 'error', {'message': fullText});
      }

      // Store assistant response in DB
      db.addMessage(
        conversationId: conversationId,
        role: 'assistant',
        content: fullText,
      );
      db.updateTokenCount(conversationId);

      _sseEvent(controller, 'done', {});
    } on McpStreamException catch (e) {
      _sseEvent(controller, 'error', {'message': 'Chat error ($agent): ${e.message}'});
    } catch (e) {
      _sseEvent(controller, 'error', {'message': 'Chat error ($agent): $e'});
    } finally {
      if (!controller.isClosed) controller.close();
    }
  }

  Future<void> _runChatLoop(
    String conversationId,
    String model,
    StreamController<List<int>> controller, {
    List<Map<String, dynamic>>? imageAttachments,
    bool useTools = true,
  }) async {
    // Apply per-model optimization profile
    final profile = getProfile(model);
    final effectiveTemp = profile.temperature;
    final effectiveMaxTokens = profile.maxTokens;

    // enforceTools overrides the client toggle — tools are always on
    final effectiveUseTools = profile.enforceTools || useTools;

    List<Map<String, dynamic>> openAiTools = [];
    if (effectiveUseTools && profile.supportsTools) {
      var tools = await mcp.getTools();
      // Step 1: filter to model's allowed tools
      if (profile.allowedTools != null) {
        tools = tools
            .where((t) => profile.allowedTools!.contains(t.name))
            .toList();
      }
      // Step 2: route to only the 1-3 tools this message needs
      final userMsgs = db.getMessages(conversationId)
          .where((m) => m.role == 'user');
      if (userMsgs.isNotEmpty) {
        tools = await tool_router.selectTools(
          userMsgs.last.content,
          tools,
          routerUrl: config.toolRouterUrl,
        );
      }
      openAiTools = tools.map((t) => t.toOpenAiFormat()).toList();
    }

    // Session-level image dedup — tracks ALL emitted image URLs
    final emittedImageUrls = <String>{}; // base URLs (no query params, lowercased)
    var totalImagesEmitted = 0;
    const maxImagesPerResponse = 4;

    for (var iteration = 0; iteration < config.maxToolIterations; iteration++) {
      final messages = db.getMessages(conversationId);
      final llmMessages = messages.map((m) => m.toLlmDict()).toList();

      // Truncate system prompt if model has a character limit
      if (profile.systemPromptMaxChars != null && llmMessages.isNotEmpty) {
        final first = llmMessages[0];
        if (first['role'] == 'system') {
          final content = first['content'] as String? ?? '';
          if (content.length > profile.systemPromptMaxChars!) {
            llmMessages[0] = {
              ...first,
              'content': content.substring(0, profile.systemPromptMaxChars!),
            };
          }
        }
      }

      // On first iteration, inject image attachments into the last user message
      if (iteration == 0 &&
          imageAttachments != null &&
          imageAttachments.isNotEmpty) {
        final lastUserIdx = llmMessages.lastIndexWhere(
          (m) => m['role'] == 'user',
        );
        if (lastUserIdx >= 0) {
          final userMsg = llmMessages[lastUserIdx];
          final contentParts = <Map<String, dynamic>>[
            {'type': 'text', 'text': userMsg['content'] as String? ?? ''},
          ];
          for (final att in imageAttachments) {
            contentParts.add({
              'type': 'image_url',
              'image_url': {'url': att['data'] as String},
            });
          }
          llmMessages[lastUserIdx] = {'role': 'user', 'content': contentParts};
        }
      }

      final fullContent = StringBuffer();
      final thinkingContent = StringBuffer();
      var pendingToolCalls = <ToolCallData>[];

      // Track whether this iteration will call tools — if so, suppress
      // intermediate narration tokens ("I'll search for...") from the UI.
      // The user only wants the final answer, not the play-by-play.
      var iterationHasToolCalls = false;
      final iterationTokens = StringBuffer();

      // enforceTools models use 'required' for first 3 iterations to ensure
      // tool usage, then switch to 'auto' so the model can synthesize text.
      final toolChoice = (profile.enforceTools && iteration < 3)
          ? 'required'
          : 'auto';

      await for (final event in llm.chatStream(
        model: model,
        messages: llmMessages,
        tools: openAiTools,
        toolChoice: toolChoice,
        maxTokens: effectiveMaxTokens,
        temperature: effectiveTemp,
      )) {
        switch (event) {
          case ReasoningTokenEvent(:final text):
            thinkingContent.write(text);
            _sseEvent(controller, 'thinking', {'text': text});
          case TokenEvent(:final text):
            iterationTokens.write(text);
            fullContent.write(text);
            // Don't emit token events yet — wait to see if tools are called
          case ToolCallsEvent(:final toolCalls):
            pendingToolCalls = toolCalls;
            iterationHasToolCalls = true;
          case UsageEvent():
            break; // LM Studio doesn't emit usage; ignore if seen
          case DoneEvent(:final finishReason):
            if (finishReason == 'tool_calls' && pendingToolCalls.isNotEmpty) {
              // Store assistant message with tool calls
              db.addMessage(
                conversationId: conversationId,
                role: 'assistant',
                content: fullContent.toString(),
                toolCalls: pendingToolCalls,
              );

              // Execute each tool
              for (final tc in pendingToolCalls) {
                _sseEvent(controller, 'tool_start', {
                  'id': tc.id,
                  'name': tc.name,
                  'arguments': tc.arguments,
                });

                Map<String, dynamic> args;
                try {
                  args = jsonDecode(tc.arguments) as Map<String, dynamic>;
                } catch (_) {
                  args = {};
                }

                // Smart fallback: when LLM produces empty/incomplete arguments
                // (streaming gap, weak model), infer from tool name + user message.
                if (!args.containsKey('action') && tc.name != 'think') {
                  _log.warning(
                    'Tool "${tc.name}" called with empty/missing action: '
                    '${tc.arguments}',
                  );
                  final userMsgs = db.getMessages(conversationId)
                      .where((m) => m.role == 'user')
                      .toList();
                  final lastUserText = userMsgs.isNotEmpty
                      ? userMsgs.last.content
                          .replaceAll(RegExp(r'\[.*?\]'), '')
                          .trim()
                      : '';
                  if (lastUserText.isNotEmpty) {
                    args = _inferToolArgs(tc.name, lastUserText);
                    _log.info('Inferred args for "${tc.name}": $args');
                  }
                }

                final result = await _withKeepalive(
                  controller,
                  () => mcp.callTool(tc.name, args),
                );
                final resultText = McpClient.extractText(result);
                final images = McpClient.extractImages(result);

                // Extract + validate + dedup image URLs (image tool only)
                final isImageTool = tc.name == 'image';
                var validatedUrls = <String>[];
                if (isImageTool && totalImagesEmitted < maxImagesPerResponse) {
                  final raw = _extractImageUrls(resultText);
                  // Dedup against session
                  final fresh = <String>[];
                  for (final url in raw) {
                    final base = url.split('?').first.toLowerCase();
                    if (emittedImageUrls.add(base)) fresh.add(url);
                    if (fresh.length + totalImagesEmitted >= maxImagesPerResponse) break;
                  }
                  // Trust image tool URLs directly — the MCP image tool
                  // already validates them. HEAD requests fail on many CDNs
                  // that block HEAD or require specific referrer headers.
                  validatedUrls = fresh;
                  totalImagesEmitted += validatedUrls.length;
                }

                _sseEvent(controller, 'tool_result', {
                  'id': tc.id,
                  'name': tc.name,
                  'text': resultText,
                  'images': isImageTool ? images : <Map<String, dynamic>>[],
                  'imageUrls': validatedUrls,
                  'isError': result['isError'] ?? false,
                });

                // Sanitize before storing — LLM sees clean text, not raw data
                final cleanedResult = _sanitizeToolResult(resultText);
                db.addMessage(
                  conversationId: conversationId,
                  role: 'tool',
                  content: cleanedResult,
                  toolCallId: tc.id,
                );
              }
              db.updateTokenCount(conversationId);
              // Continue loop for next LLM iteration —
              // reset fullContent so next iteration starts fresh
              fullContent.clear();
              continue;
            }

            // This is the FINAL iteration (no tool calls) — now emit
            // the buffered tokens so the frontend renders the answer.
            if (!iterationHasToolCalls && iterationTokens.isNotEmpty) {
              _sseEvent(controller, 'token', {'text': iterationTokens.toString()});
            }

            // Determine final content — reasoning models (Qwen 3.5, etc.)
            // often put the real answer in reasoning_content and leave
            // content empty or trivially short (e.g. "Let me search...").
            // Use thinking content when it's substantially longer.
            var finalContent = fullContent.toString();
            final thinkingStr = thinkingContent.toString();
            if (thinkingStr.length > finalContent.length * 3 &&
                thinkingStr.length > 100) {
              finalContent = thinkingStr;
              _log.info(
                'Using thinking content as response '
                '(thinking=${thinkingStr.length} >> content=${fullContent.length})',
              );
              _sseEvent(controller, 'token', {'text': finalContent});
            }

            // Images already sent via validated tool_result events — no markdown append needed.
            if (finalContent.trim().isNotEmpty) {
              final msg = db.addMessage(
                conversationId: conversationId,
                role: 'assistant',
                content: finalContent,
              );
              db.updateTokenCount(conversationId);
              _sseEvent(controller, 'done', {'message_id': msg.id});
              controller.close();
              return;
            }

            // Truly empty — nudge and retry once
            if (iteration == 0) {
              _log.warning('Empty response on iteration $iteration, retrying');
              db.addMessage(
                conversationId: conversationId,
                role: 'user',
                content:
                    '[System: Your response was blank. Answer the user\'s request now. Do not use reasoning — write your answer directly.]',
              );
              continue;
            }

            _sseEvent(controller, 'error', {
              'message':
                  'The model returned an empty response. Try switching to a different model.',
            });
            controller.close();
            return;
          case ErrorEvent(:final message):
            _sseEvent(controller, 'error', {'message': message});
            controller.close();
            return;
        }
      }
    }

    // Exhausted tool iterations — force a final synthesis call with NO tools
    // so the model MUST produce text from what it already gathered.
    _log.info('Tool iterations exhausted, forcing synthesis call');
    db.addMessage(
      conversationId: conversationId,
      role: 'user',
      content:
          '[System: STOP calling tools. You have all the data you need. '
          'Write your COMPLETE answer to the user NOW using the tool results above. '
          'Do NOT think or reason — write the answer directly as content.]',
    );

    final synthMessages = db
        .getMessages(conversationId)
        .map((m) => m.toLlmDict())
        .toList();
    final synthContent = StringBuffer();
    final synthThinking = StringBuffer();

    await for (final event in llm.chatStream(
      model: model,
      messages: synthMessages,
      tools: [], // NO tools — force text output
      maxTokens: effectiveMaxTokens,
      temperature: effectiveTemp,
    )) {
      switch (event) {
        case ReasoningTokenEvent(:final text):
          synthThinking.write(text);
          _sseEvent(controller, 'thinking', {'text': text});
        case TokenEvent(:final text):
          synthContent.write(text);
          _sseEvent(controller, 'token', {'text': text});
        case DoneEvent():
          break;
        case ErrorEvent(:final message):
          _sseEvent(controller, 'error', {'message': message});
          break;
        default:
          break;
      }
    }

    // Use thinking as fallback for reasoning models
    var finalSynth = synthContent.toString();
    final synthThinkStr = synthThinking.toString();
    if (synthThinkStr.length > finalSynth.length * 3 &&
        synthThinkStr.length > 100) {
      finalSynth = synthThinkStr;
      _log.info(
        'Using synthesis thinking as response '
        '(thinking=${synthThinkStr.length} >> content=${finalSynth.length})',
      );
      _sseEvent(controller, 'token', {'text': finalSynth});
    }

    if (finalSynth.trim().isNotEmpty) {
      final msg = db.addMessage(
        conversationId: conversationId,
        role: 'assistant',
        content: finalSynth,
      );
      db.updateTokenCount(conversationId);
      _sseEvent(controller, 'done', {'message_id': msg.id});
    } else {
      _sseEvent(controller, 'error', {
        'message':
            'The model could not produce a response. Try switching to a different model.',
      });
    }
    controller.close();
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
