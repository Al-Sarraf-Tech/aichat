import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:logging/logging.dart';
import 'package:path/path.dart' as p;
import 'package:shelf/shelf.dart'
    show Cascade, Handler, Middleware, Pipeline, Request, Response;
import 'package:shelf_router/shelf_router.dart' show Router;
import 'package:uuid/uuid.dart' show Uuid;

import 'compaction.dart';
import 'config.dart';
import 'database.dart';
import 'llm_client.dart';
import 'mcp_client.dart';
import 'model_profiles.dart';
import 'models.dart';
import 'personalities.dart';
import 'api_client.dart';
import 'tool_router.dart' as tool_router;

final _log = Logger('Router');

class AppRouter {
  final Config config;
  final AppDatabase db;
  final LlmClient llm;
  final McpClient mcp;
  final Compactor compactor;
  final ApiClient apiClient;

  late final Router _router;

  AppRouter({
    required this.config,
    required this.db,
    required this.llm,
    required this.mcp,
    required this.compactor,
    ApiClient? apiClient,
  }) : apiClient = apiClient ?? ApiClient() {
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
      ..get('/api/models', _listModels)
      ..get('/api/model-status', _modelStatus)
      ..get('/api/personalities', _listPersonalities)
      ..post('/api/warmup', _warmupModel)
      ..post('/api/unload', _unloadModel)
      ..get('/api/image/status', _imageStatus)
      ..get('/api/image/models', _imageModels)
      ..post('/api/image/generate', _imageGenerate)
      ..get('/api/image/job/<jobId>', _imageJobStatus)
      ..get('/api/image/download/<filename>', _imageDownload)
      ..get('/api/search', _searchMessages)
      ..get('/api/providers', _listProviders)
      ..post('/api/image/search-reference', _imageSearchReference);
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

  static const _corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PATCH, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  };

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

  // ── User Identity ──────────────────────────────────────────────────

  /// Extract authenticated user ID from X-Auth-User header (set by auth proxy).
  String _getUserId(Request request) {
    return request.headers['x-auth-user'] ?? '';
  }

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
      final resolved = ApiClient.resolve(effectiveModel);
      if (resolved == null) {
        return _json({'error': 'Unknown API model: $effectiveModel'},
            status: 400);
      }
      final (provider, realModel) = resolved;
      final apiKey = switch (provider) {
        ApiProvider.anthropic => config.anthropicApiKey,
        ApiProvider.openai => config.openaiApiKey,
        ApiProvider.google => config.googleApiKey,
      };
      if (apiKey.isEmpty) {
        return _json({'error': 'API key not configured for provider'},
            status: 400);
      }

      final controller = StreamController<List<int>>();
      _runApiChat(id, provider, realModel, apiKey, controller)
          .catchError((e) {
        _log.severe('API chat error: $e');
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

  // Model validation cache: model_id → capabilities
  final _modelCaps = <String, Map<String, dynamic>>{};

  Future<Response> _listModels(Request request) async {
    final models = await llm.listModels();

    // Fetch load-state info from v0 API and index by model ID
    final v0Models = await llm.listModelsV0();
    final v0ById = <String, Map<String, dynamic>>{};
    for (final m in v0Models) {
      final id = m['id'] as String? ?? '';
      if (id.isNotEmpty) v0ById[id] = m;
    }

    // Annotate each model with cached validation results, load state,
    // and per-model optimization profile.
    final annotated = models.map((m) {
      final id = m['id'] as String? ?? '';
      final caps = _modelCaps[id];
      final v0 = v0ById[id];
      final profile = getProfile(id);
      return {
        ...m,
        'validated': caps != null,
        if (caps != null) ...caps,
        if (v0 != null) 'state': v0['state'],
        if (v0 != null && v0.containsKey('type')) 'model_type': v0['type'],
        if (v0 != null && v0.containsKey('quantization'))
          'quantization': v0['quantization'],
        'profile': profile.toJson(),
      };
    }).toList();
    return _json({'models': annotated});
  }

  /// Report which model providers are reachable and configured.
  Future<Response> _modelStatus(Request request) async {
    final status = <String, String>{};

    // OAuth CLI agents — always available if SSH is up
    status['claude'] = 'ok';
    status['codex'] = 'ok';
    status['gemini'] = 'ok';
    status['qwen'] = 'ok';

    // API providers — check if API keys are configured
    status['anthropic'] = config.anthropicApiKey.isNotEmpty ? 'ok' : 'no_key';
    status['openai'] = config.openaiApiKey.isNotEmpty ? 'ok' : 'no_key';
    status['google'] = config.googleApiKey.isNotEmpty ? 'ok' : 'no_key';

    // LM Studio — check if reachable
    try {
      final r = await llm.listModels();
      status['lmstudio'] = r.isNotEmpty ? 'ok' : 'empty';
    } catch (e) {
      status['lmstudio'] = 'unreachable';
    }

    return _json({'status': status});
  }

  /// Warmup + validate a model by running e2e tests.
  /// Tests: (1) chat response, (2) tool calling ability.
  /// Results are cached so each model is only validated once per session.
  Future<Response> _warmupModel(Request request) async {
    final body = await _readJson(request);
    final model = body?['model'] as String?;
    if (model == null || model.isEmpty) {
      return _json({'error': 'model is required'}, status: 400);
    }

    // Skip if already validated
    if (_modelCaps.containsKey(model)) {
      _log.info('Model $model already validated');
      return _json({'status': 'ready', 'model': model, ..._modelCaps[model]!});
    }

    // Capacity guard: don't trigger warmup if loading this model would
    // evict another (and it's not already loaded).
    final warmupBusy = await llm.ensureModelOrBusy(
      model,
      maxLoaded: config.maxLoadedModels,
    );
    if (warmupBusy != null) {
      _log.info('Warmup skipped for $model — at capacity');
      return _json({
        'status': 'busy',
        'model': model,
        'message': warmupBusy,
      }, status: 503);
    }

    // Skip embedding models entirely
    if (model.toLowerCase().contains('embed')) {
      final caps = {
        'chat': false,
        'tools': false,
        'reasoning': false,
        'embedding': true,
        'limitation': 'Embedding model — not a chat model',
      };
      _modelCaps[model] = caps;
      return _json({'status': 'limited', 'model': model, ...caps});
    }

    _log.info('Validating model: $model');
    var chatOk = false;
    var toolsOk = false;
    var reasoning = false;
    String? limitation;

    // Test 1: Chat — can it produce a response?
    try {
      final result = await llm.chatOnce(
        model: model,
        messages: [
          {'role': 'user', 'content': 'What is 2+2? Answer in one word.'},
        ],
        maxTokens: 100,
        temperature: 0,
      );
      chatOk = result.isNotEmpty && !result.startsWith('[');
      if (result.contains('reasoning') || result.contains('think')) {
        reasoning = true;
      }
      _log.info(
        'Model $model chat test: ${chatOk ? "PASS" : "FAIL"} ($result)',
      );
    } catch (e) {
      _log.warning('Model $model chat test failed: $e');
      limitation = 'Cannot produce chat responses: $e';
    }

    // Test 2: Tools — can it make tool calls?
    if (chatOk) {
      try {
        final tools = await mcp.getTools();
        final openAiTools = tools.map((t) => t.toOpenAiFormat()).toList();

        var foundToolCall = false;
        await for (final event in llm.chatStream(
          model: model,
          messages: [
            {
              'role': 'system',
              'content': 'You have tools. Use the web tool to search.',
            },
            {'role': 'user', 'content': 'Search the web for "test query"'},
          ],
          tools: openAiTools,
          maxTokens: 200,
          temperature: 0,
        )) {
          if (event is ToolCallsEvent) {
            foundToolCall = true;
            break;
          }
          if (event is DoneEvent) break;
        }
        toolsOk = foundToolCall;
        if (!toolsOk) {
          limitation = 'This model cannot use tools — text-only responses';
        }
        _log.info('Model $model tool test: ${toolsOk ? "PASS" : "FAIL"}');
      } catch (e) {
        _log.warning('Model $model tool test failed: $e');
        limitation ??= 'Tool calling not supported';
      }
    }

    // Check if this is a reasoning model by name patterns
    final lm = model.toLowerCase();
    if (lm.contains('qwen3') ||
        lm.contains('magistral') ||
        lm.contains('reasoning') ||
        lm.contains('think') ||
        lm.contains('phi-4')) {
      reasoning = true;
    }

    final caps = <String, dynamic>{
      'chat': chatOk,
      'tools': toolsOk,
      'reasoning': reasoning,
      'embedding': false,
      if (limitation != null) 'limitation': limitation,
    };
    _modelCaps[model] = caps;

    // Store detected capabilities as a runtime profile override so future
    // requests use the auto-detected settings instead of heuristic defaults.
    final baseProfile = getProfile(model);
    setRuntimeProfile(
      model,
      ModelProfile(
        temperature: baseProfile.temperature,
        maxTokens: baseProfile.maxTokens,
        supportsTools: toolsOk,
        supportsReasoning: reasoning,
        systemPromptMaxChars: baseProfile.systemPromptMaxChars,
        notes: 'Auto-detected during warmup',
      ),
    );

    final status = chatOk ? 'ready' : 'error';
    _log.info('Model $model validated: $caps');
    return _json({'status': status, 'model': model, ...caps});
  }

  /// Best-effort model unload — LM Studio JIST handles this automatically,
  /// but we attempt to free resources when the user leaves the page.
  Future<Response> _unloadModel(Request request) async {
    final body = await _readJson(request);
    final model = body?['model'] as String?;
    if (model == null || model.isEmpty) {
      return _json({'status': 'skipped'});
    }
    _log.info('Unload requested for: $model (JIST auto-manages)');
    // LM Studio doesn't expose a public unload API —
    // JIST automatically unloads idle models. Log the intent.
    return _json({'status': 'acknowledged', 'model': model});
  }

  // ── Image Generation (Async Job System) ───────────────────────────
  // Jobs are stored in memory. Each job has a status and result.
  final Map<String, Map<String, dynamic>> _imageJobs = {};

  Future<Response> _imageStatus(Request request) async {
    if (config.comfyuiUrl.isEmpty) {
      return _json({'ok': false, 'error': 'ComfyUI not configured'});
    }
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final req = await client.getUrl(Uri.parse('${config.comfyuiUrl}/system_stats'));
      final resp = await req.close().timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final body = await resp.transform(utf8.decoder).join();
        final data = jsonDecode(body);
        final devices = (data is Map ? data['devices'] as List? : null) ?? [];
        final gpu = devices.isNotEmpty ? (devices[0]['name'] ?? 'GPU') as String : '';
        return _json({'ok': true, 'gpu': gpu});
      }
      return _json({'ok': false, 'error': 'ComfyUI returned ${resp.statusCode}'});
    } catch (e) {
      return _json({'ok': false, 'error': 'ComfyUI unreachable: $e'});
    } finally {
      client.close();
    }
  }

  /// Query ComfyUI for installed model files — used by frontend to enable/disable buttons.
  Future<Response> _imageModels(Request request) async {
    if (config.comfyuiUrl.isEmpty) {
      return _json({'checkpoints': [], 'unets': []});
    }
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    try {
      final checkpoints = <String>[];
      final unets = <String>[];
      // Fetch checkpoint list
      final ckptReq = await client.getUrl(Uri.parse('${config.comfyuiUrl}/object_info/CheckpointLoaderSimple'));
      final ckptResp = await ckptReq.close().timeout(const Duration(seconds: 5));
      if (ckptResp.statusCode == 200) {
        final body = await ckptResp.transform(utf8.decoder).join();
        final data = jsonDecode(body) as Map<String, dynamic>?;
        final node = data?['CheckpointLoaderSimple'] as Map?;
        final inp = (node?['input'] as Map?)?['required'] as Map?;
        final ckptList = inp?['ckpt_name'];
        if (ckptList is List && ckptList.isNotEmpty && ckptList.first is List) {
          checkpoints.addAll((ckptList.first as List).cast<String>());
        }
      }
      // Fetch UNet list
      final unetReq = await client.getUrl(Uri.parse('${config.comfyuiUrl}/object_info/UNETLoader'));
      final unetResp = await unetReq.close().timeout(const Duration(seconds: 5));
      if (unetResp.statusCode == 200) {
        final body = await unetResp.transform(utf8.decoder).join();
        final data = jsonDecode(body) as Map<String, dynamic>?;
        final node = data?['UNETLoader'] as Map?;
        final inp = (node?['input'] as Map?)?['required'] as Map?;
        final unetList = inp?['unet_name'];
        if (unetList is List && unetList.isNotEmpty && unetList.first is List) {
          unets.addAll((unetList.first as List).cast<String>());
        }
      }
      return _json({'checkpoints': checkpoints, 'unets': unets});
    } catch (e) {
      return _json({'checkpoints': [], 'unets': [], 'error': '$e'});
    } finally {
      client.close();
    }
  }

  Future<Response> _imageGenerate(Request request) async {
    if (config.comfyuiUrl.isEmpty) {
      return _json({'error': 'ComfyUI not configured'}, status: 503);
    }
    final body = await _readJson(request);
    if (body == null) return _json({'error': 'Invalid JSON'}, status: 400);
    final prompt = (body['prompt'] as String?)?.trim() ?? '';
    if (prompt.isEmpty) return _json({'error': 'prompt is required'}, status: 400);
    final model = (body['model'] as String?) ?? 'flux_schnell';
    final width = _toInt(body['width'], 1024).clamp(64, 4096);
    final height = _toInt(body['height'], 1024).clamp(64, 4096);
    final negPrompt = (body['negative_prompt'] as String?) ?? '';
    final steps = body['steps'] != null ? _toInt(body['steps'], 0) : null;
    final seed = body['seed'] != null ? _toInt(body['seed'], -1) : null;
    final effectiveSeed = seed ?? DateTime.now().millisecondsSinceEpoch % (1 << 32);
    // Img2img parameters
    final referenceImage = body['reference_image'] as String?; // base64 data URI
    final denoise = ((body['denoise'] as num?)?.toDouble() ?? 0.65).clamp(0.05, 1.0);
    final upscaleTo = body['upscale_to'] != null ? _toInt(body['upscale_to'], 2048).clamp(1024, 4096) : null;
    // Backend routing: comfyui (default), openai, gemini
    final backend = (body['backend'] as String?) ?? 'comfyui';
    // Batch count
    final count = _toInt(body['count'], 1).clamp(1, 4);
    // Inpainting mask
    final mask = body['mask'] as String?;
    // ControlNet
    final controlnetType = body['controlnet_type'] as String?;
    final controlnetImage = body['controlnet_image'] as String?;
    final controlnetStrength = ((body['controlnet_strength'] as num?)?.toDouble() ?? 0.8).clamp(0.1, 1.0);

    // Create job with UUID and user binding
    final userId = _getUserId(request);
    final jobId = const Uuid().v4();
    _imageJobs[jobId] = {
      'status': 'submitted',
      'model': model,
      'seed': effectiveSeed,
      'prompt': prompt,
      'user_id': userId,
    };
    _log.info('Image job $jobId: backend=$backend model=$model ${width}x$height count=$count${referenceImage != null ? " img2img" : ""}${mask != null ? " inpaint" : ""}${upscaleTo != null ? " upscale→$upscaleTo" : ""}');

    // Route to appropriate backend
    if (backend == 'openai' || backend == 'gemini') {
      // Cloud backends — standalone MCP tools (direct API)
      _runApiImageJob(jobId, backend: backend, prompt: prompt, negPrompt: negPrompt,
          width: width, height: height, count: count, upscaleTo: upscaleTo);
    } else {
      // ComfyUI backend — local generation
      for (var i = 0; i < count; i++) {
        final batchSeed = effectiveSeed + i;
        _runImageJob('${jobId}_$i' == '${jobId}_0' ? jobId : '${jobId}_$i',
            model: model, prompt: prompt, negPrompt: negPrompt,
            width: width, height: height, steps: steps, seed: batchSeed,
            referenceImage: referenceImage, denoise: denoise, upscaleTo: upscaleTo,
            mask: mask, controlnetType: controlnetType, controlnetImage: controlnetImage,
            controlnetStrength: controlnetStrength);
      }
    }

    // Return instantly — client polls /api/image/job/<jobId>
    return _json({'jobId': jobId, 'status': 'submitted'});
  }

  /// Set job to error state while preserving user_id for ownership checks.
  void _failJob(String jobId, String error) {
    final existing = _imageJobs[jobId];
    _imageJobs[jobId] = {
      'status': 'error',
      'error': error,
      if (existing != null && existing['user_id'] != null) 'user_id': existing['user_id'],
    };
  }

  /// Background image generation — updates _imageJobs[jobId] when done.
  Future<void> _runImageJob(String jobId, {
    required String model, required String prompt, required String negPrompt,
    required int width, required int height, int? steps, required int seed,
    String? referenceImage, double denoise = 1.0, int? upscaleTo,
    String? mask, String? controlnetType, String? controlnetImage,
    double controlnetStrength = 0.8,
  }) async {
    _imageJobs[jobId]!['status'] = 'generating';
    final client = HttpClient();
    try {
      // --- Upload reference image if img2img ---
      String? refFilename;
      if (referenceImage != null && referenceImage.isNotEmpty) {
        refFilename = await _uploadToComfyUI(client, referenceImage);
        if (refFilename == null) {
          _failJob(jobId, 'Failed to upload reference image');
          return;
        }
        _log.info('Job $jobId: uploaded reference → $refFilename');
      }

      // --- Upload mask image if inpainting ---
      String? maskFilename;
      if (mask != null && mask.isNotEmpty && refFilename != null) {
        maskFilename = await _uploadToComfyUI(client, mask);
        if (maskFilename == null) {
          _failJob(jobId, 'Failed to upload mask image');
          return;
        }
        _log.info('Job $jobId: uploaded mask → $maskFilename');
      }

      // --- Upload ControlNet image if provided ---
      String? cnImageFilename;
      if (controlnetType != null && controlnetType != 'none' &&
          controlnetImage != null && controlnetImage.isNotEmpty) {
        cnImageFilename = await _uploadToComfyUI(client, controlnetImage);
        if (cnImageFilename == null) {
          _failJob(jobId, 'Failed to upload ControlNet image');
          return;
        }
        _log.info('Job $jobId: uploaded controlnet ($controlnetType) → $cnImageFilename');
      }

      // --- Build workflow ---
      final Map<String, dynamic> workflow;
      if (maskFilename != null && refFilename != null) {
        // Inpainting: source + mask
        workflow = _buildComfyInpaintWorkflow(
          model: model, prompt: prompt, negPrompt: negPrompt,
          width: width, height: height, steps: steps, seed: seed,
          refFilename: refFilename, maskFilename: maskFilename, denoise: denoise,
        );
      } else if (cnImageFilename != null && controlnetType != null) {
        // ControlNet: guided generation
        workflow = _buildComfyControlNetWorkflow(
          model: model, prompt: prompt, negPrompt: negPrompt,
          width: width, height: height, steps: steps, seed: seed,
          controlType: controlnetType, controlFilename: cnImageFilename,
          strength: controlnetStrength,
          refFilename: refFilename, denoise: denoise,
        );
      } else if (refFilename != null) {
        workflow = _buildComfyImg2ImgWorkflow(
          model: model, prompt: prompt, negPrompt: negPrompt,
          width: width, height: height, steps: steps, seed: seed,
          refFilename: refFilename, denoise: denoise,
        );
      } else {
        workflow = _buildComfyWorkflow(
          model: model, prompt: prompt, negPrompt: negPrompt,
          width: width, height: height, steps: steps, seed: seed,
        );
      }

      // Submit prompt to ComfyUI
      final submitReq = await client.postUrl(Uri.parse('${config.comfyuiUrl}/prompt'));
      submitReq.headers.contentType = ContentType.json;
      submitReq.write(jsonEncode({'prompt': workflow}));
      final submitResp = await submitReq.close().timeout(const Duration(seconds: 30));
      final submitBody = await submitResp.transform(utf8.decoder).join();
      if (submitResp.statusCode != 200) {
        _failJob(jobId, 'ComfyUI rejected workflow');
        return;
      }
      final submitData = jsonDecode(submitBody);
      final promptId = submitData is Map ? submitData['prompt_id'] as String? : null;
      if (promptId == null || promptId.isEmpty) {
        _failJob(jobId, 'No prompt_id returned');
        return;
      }

      // Poll ComfyUI for completion (up to 600s for cold FLUX loads)
      Map<String, dynamic>? outputs;
      for (var i = 0; i < 1200; i++) {
        await Future.delayed(const Duration(milliseconds: 500));
        try {
          final histReq = await client.getUrl(Uri.parse('${config.comfyuiUrl}/history/$promptId'));
          final histResp = await histReq.close().timeout(const Duration(seconds: 10));
          if (histResp.statusCode != 200) continue;
          final histBody = await histResp.transform(utf8.decoder).join();
          final hist = jsonDecode(histBody);
          if (hist is! Map || !hist.containsKey(promptId)) continue;
          final entry = hist[promptId];
          if (entry is! Map) continue;
          final statusStr = (entry['status'] as Map?)?['status_str'] as String?;
          if (statusStr == 'error' || statusStr == 'failed' || statusStr == 'cancelled') {
            _failJob(jobId, 'ComfyUI: $statusStr');
            return;
          }
          final outs = entry['outputs'];
          if (outs is Map<String, dynamic> && outs.containsKey('9')) {
            outputs = outs;
            break;
          }
        } on FormatException { continue; }
        on TimeoutException { continue; }
      }
      if (outputs == null) {
        _failJob(jobId, 'Timed out (600s)');
        return;
      }

      // Fetch and save images
      final outputNode = outputs['9'];
      if (outputNode is! Map || outputNode['images'] is! List ||
          (outputNode['images'] as List).isEmpty) {
        _failJob(jobId, 'No images in output');
        return;
      }
      final imageList = outputNode['images'] as List;
      final images = <Map<String, dynamic>>[];
      for (final imgInfo in imageList) {
        if (imgInfo is! Map) continue;
        final fname = imgInfo['filename'] as String?;
        if (fname == null) continue;
        final subfolder = (imgInfo['subfolder'] as String?) ?? '';
        final viewUrl = Uri.parse('${config.comfyuiUrl}/view').replace(
          queryParameters: {'filename': fname, 'subfolder': subfolder, 'type': 'output'},
        );
        final imgReq = await client.getUrl(viewUrl);
        final imgResp = await imgReq.close().timeout(const Duration(seconds: 30));
        final builder = BytesBuilder(copy: false);
        await imgResp.forEach(builder.add);
        final imgBytes = builder.takeBytes();
        if (imgBytes.length > 50 * 1024 * 1024) continue;
        // Save to /app/pictures
        String? savedAs;
        try {
          final picDir = Directory('/app/pictures');
          if (picDir.existsSync()) {
            final ts = DateTime.now().toIso8601String().replaceAll(RegExp(r'[:\-T]'), '').substring(0, 15);
            final safePrompt = prompt.length > 30 ? prompt.substring(0, 30) : prompt;
            final cleanPrompt = safePrompt.replaceAll(RegExp(r'[^a-zA-Z0-9 ]'), '').trim().replaceAll(' ', '_');
            savedAs = '${model}_${ts}_$cleanPrompt.jpg';
            File('${picDir.path}/$savedAs').writeAsBytesSync(imgBytes);
            _log.info('Job $jobId: saved /app/pictures/$savedAs');
          }
        } catch (e) {
          _log.warning('Job $jobId: save failed: $e');
        }
        images.add({
          'filename': fname,
          if (savedAs != null) 'savedAs': savedAs,
          if (savedAs != null) 'url': '/api/image/download/$savedAs',
        });
      }
      // --- Upscale step (generate at base res, then upscale via ComfyUI) ---
      if (upscaleTo != null && upscaleTo > width && images.isNotEmpty) {
        _imageJobs[jobId]!['status'] = 'upscaling';
        _log.info('Job $jobId: upscaling to ${upscaleTo}px');
        final upscaled = await _upscaleImages(client, images, upscaleTo, width, height, model, prompt);
        if (upscaled.isNotEmpty) {
          images.clear();
          images.addAll(upscaled);
        }
      }

      final doneUserId = _imageJobs[jobId]?['user_id'];
      _imageJobs[jobId] = {
        'status': 'done',
        'images': images,
        'model': model,
        'seed': seed,
        if (doneUserId != null) 'user_id': doneUserId,
      };
      _log.info('Job $jobId: complete, ${images.length} images');
      // Clean up old jobs (keep last 50)
      if (_imageJobs.length > 50) {
        final keys = _imageJobs.keys.toList();
        for (var i = 0; i < keys.length - 50; i++) {
          _imageJobs.remove(keys[i]);
        }
      }
    } catch (e) {
      _log.severe('Job $jobId error: $e');
      _failJob(jobId, '$e');
    } finally {
      client.close();
    }
  }

  Future<Response> _imageJobStatus(Request request, String jobId) async {
    final job = _imageJobs[jobId];
    if (job == null) {
      return _json({'status': 'not_found', 'error': 'Job not found'}, status: 404);
    }
    // Enforce user ownership
    final userId = _getUserId(request);
    if (userId.isNotEmpty && job['user_id'] != userId) {
      return _json({'status': 'not_found', 'error': 'Job not found'}, status: 404);
    }
    return _json(job);
  }

  Future<Response> _imageDownload(Request request, String filename) async {
    final userId = _getUserId(request);
    // Sanitize filename to prevent path traversal
    final safe = filename.replaceAll(RegExp(r'[^a-zA-Z0-9_.\-]'), '');
    if (safe.isEmpty || safe.contains('..')) {
      return _json({'error': 'Invalid filename'}, status: 400);
    }
    final picDir = '/app/pictures';
    // User-scoped: check user subdirectory first, fall back to shared dir
    final userFile = File('$picDir/$userId/$safe');
    final sharedFile = File('$picDir/$safe');
    final file = userFile.existsSync() ? userFile : sharedFile;
    if (!file.existsSync()) {
      return _json({'error': 'File not found'}, status: 404);
    }
    // Verify file is within pictures directory (use p.isWithin for safe prefix check)
    try {
      final resolved = file.resolveSymbolicLinksSync();
      final resolvedDir = p.canonicalize(picDir);
      if (!p.isWithin(resolvedDir, resolved) && resolved != resolvedDir) {
        return Response.forbidden('Forbidden');
      }
    } catch (e) {
      return _json({'error': 'File access error'}, status: 500);
    }
    // Determine content type from extension
    final ext = safe.split('.').last.toLowerCase();
    final contentType = switch (ext) {
      'png' => 'image/png',
      'webp' => 'image/webp',
      'gif' => 'image/gif',
      _ => 'image/jpeg',
    };
    return Response.ok(
      file.openRead(),
      headers: {
        'Content-Type': contentType,
        'Content-Disposition': 'attachment; filename="$safe"',
        'Cache-Control': 'public, max-age=86400',
      },
    );
  }

  Map<String, dynamic> _buildComfyWorkflow({
    required String model,
    required String prompt,
    required String negPrompt,
    required int width,
    required int height,
    int? steps,
    int? seed,
  }) {
    final rng = seed ?? 0; // seed should always be provided by caller now
    // Model configs
    const models = {
      'flux_schnell': {'unet': 'flux1-schnell.safetensors', 'steps': 4, 'cfg': 1.0, 'type': 'flux'},
      'flux_dev':     {'unet': 'flux1-dev.safetensors',     'steps': 25, 'cfg': 1.0, 'type': 'flux'},
      'sdxl_lightning': {'ckpt': 'sd_xl_base_1.0.safetensors', 'unet': 'sdxl_lightning_4step.safetensors', 'steps': 4, 'cfg': 1.5, 'type': 'sdxl_lightning'},
      'sdxl_turbo':   {'ckpt': 'sdxl_turbo.safetensors', 'steps': 1, 'cfg': 1.0, 'type': 'sdxl_turbo'},
      // Community SD 1.5 models (single checkpoint, includes CLIP+VAE)
      'dreamshaper':      {'ckpt': 'dreamshaper_8.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sd15'},
      'realistic_vision': {'ckpt': 'realistic_vision_v5.safetensors',  'steps': 30, 'cfg': 7.0, 'type': 'sd15'},
      'deliberate':       {'ckpt': 'deliberate_v3.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sd15'},
    };
    final cfg = models[model] ?? models['sdxl_lightning']!;
    final s = steps ?? (cfg['steps'] as int);
    final c = cfg['cfg'] as double;
    final type = cfg['type'] as String;

    if (type == 'flux') {
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['5', 0]}},
        '4': {'class_type': 'UNETLoader', 'inputs': {'unet_name': cfg['unet'], 'weight_dtype': 'default'}},
        '5': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['11', 0]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['11', 0]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['10', 0]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_$model', 'images': ['8', 0]}},
        '10': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
        '11': {'class_type': 'DualCLIPLoader', 'inputs': {'clip_name1': 'clip_l.safetensors', 'clip_name2': 't5xxl_fp16.safetensors', 'type': 'flux'}},
      };
    } else if (type == 'sdxl_turbo') {
      final w = width > 512 ? 512 : width;
      final h = height > 512 ? 512 : height;
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': 1.0,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['5', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {'width': w, 'height': h, 'batch_size': 1}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_$model', 'images': ['8', 0]}},
      };
    } else if (type == 'sd15') {
      // SD 1.5 community models — native 512x512, supports up to 768x768
      final w = width > 768 ? 512 : width;
      final h = height > 768 ? 512 : height;
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler_ancestral', 'scheduler': 'normal', 'denoise': 1.0,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['5', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {'width': w, 'height': h, 'batch_size': 1}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_$model', 'images': ['8', 0]}},
      };
    } else {
      // sdxl_lightning
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'sgm_uniform', 'denoise': 1.0,
          'model': ['4c', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['5', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '4b': {'class_type': 'UNETLoader', 'inputs': {'unet_name': cfg['unet'], 'weight_dtype': 'default'}},
        '4c': {'class_type': 'ModelMergeSimple', 'inputs': {'model1': ['4', 0], 'model2': ['4b', 0], 'ratio': 1.0}},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_$model', 'images': ['8', 0]}},
      };
    }
  }

  // ── API Image Generation (OpenAI/Gemini via MCP) ─────────────────

  /// Direct HTTP API calls to cloud image providers (no MCP, direct API).
  Future<void> _runApiImageJob(String jobId, {
    required String backend,
    required String prompt,
    required String negPrompt,
    required int width,
    required int height,
    required int count,
    int? upscaleTo,
  }) async {
    _imageJobs[jobId]!['status'] = 'generating';
    try {
      final fullPrompt = negPrompt.isNotEmpty ? '$prompt. Avoid: $negPrompt' : prompt;
      String imageB64 = '';
      int outW = width, outH = height;

      if (backend == 'openai') {
        final key = config.openaiApiKey;
        if (key.isEmpty || key == 'ROTATE_ME') {
          _failJob(jobId, 'OpenAI API key not configured — add OPENAI_API_KEY to .env');
          return;
        }
        final size = _snapOpenaiSize(width, height);
        final client = HttpClient();
        try {
          final req = await client.postUrl(Uri.parse('https://api.openai.com/v1/images/generations'));
          req.headers.set('Authorization', 'Bearer $key');
          req.headers.set('Content-Type', 'application/json');
          req.write(jsonEncode({
            'model': 'dall-e-3',
            'prompt': fullPrompt,
            'size': size,
            'response_format': 'b64_json',
            'n': 1,
          }));
          final resp = await req.close().timeout(const Duration(seconds: 90));
          final body = await resp.transform(utf8.decoder).join();
          if (resp.statusCode != 200) {
            _failJob(jobId, 'OpenAI returned ${resp.statusCode}: ${body.length > 300 ? body.substring(0, 300) : body}');
            return;
          }
          final data = jsonDecode(body) as Map<String, dynamic>;
          final items = data['data'] as List? ?? [];
          if (items.isEmpty) { _failJob(jobId, 'OpenAI returned no images'); return; }
          imageB64 = (items[0] as Map)['b64_json'] as String? ?? '';
          final parts = size.split('x');
          outW = int.parse(parts[0]); outH = int.parse(parts[1]);
        } finally {
          client.close();
        }

      } else if (backend == 'gemini') {
        final key = config.googleApiKey;
        if (key.isEmpty) {
          _failJob(jobId, 'Google API key not configured — add GOOGLE_API_KEY to .env');
          return;
        }
        final ratio = width == height ? '1:1'
            : width > height ? '16:9' : '9:16';
        final client = HttpClient();
        try {
          // Gemini Imagen 3 API
          final url = 'https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key=$key';
          final req = await client.postUrl(Uri.parse(url));
          req.headers.set('Content-Type', 'application/json');
          req.write(jsonEncode({
            'instances': [{'prompt': fullPrompt}],
            'parameters': {
              'sampleCount': 1,
              'aspectRatio': ratio,
            },
          }));
          final resp = await req.close().timeout(const Duration(seconds: 90));
          final body = await resp.transform(utf8.decoder).join();
          if (resp.statusCode != 200) {
            _failJob(jobId, 'Gemini returned ${resp.statusCode}: ${body.length > 300 ? body.substring(0, 300) : body}');
            return;
          }
          final data = jsonDecode(body) as Map<String, dynamic>;
          final predictions = data['predictions'] as List? ?? [];
          if (predictions.isEmpty) { _failJob(jobId, 'Gemini returned no images'); return; }
          imageB64 = (predictions[0] as Map)['bytesBase64Encoded'] as String? ?? '';
          outW = 1024; outH = ratio == '1:1' ? 1024 : (ratio == '16:9' ? 576 : 1792);
        } finally {
          client.close();
        }

      } else {
        _failJob(jobId, 'Unknown cloud backend: $backend');
        return;
      }

      if (imageB64.isEmpty) { _failJob(jobId, 'No image data returned'); return; }

      // Save to /app/pictures
      final images = <Map<String, dynamic>>[];
      try {
        final bytes = base64Decode(imageB64);
        final ts = DateTime.now().toIso8601String().replaceAll(RegExp(r'[:\-T]'), '').substring(0, 15);
        final cleanPrompt = prompt.length > 20
            ? prompt.substring(0, 20).replaceAll(RegExp(r'[^a-zA-Z0-9 ]'), '').trim().replaceAll(' ', '_')
            : prompt.replaceAll(RegExp(r'[^a-zA-Z0-9 ]'), '').trim().replaceAll(' ', '_');
        final savedAs = '${backend}_${ts}_$cleanPrompt.jpg';
        File('/app/pictures/$savedAs').writeAsBytesSync(bytes);
        images.add({'savedAs': savedAs, 'url': '/api/image/download/$savedAs', 'data': imageB64, 'mimeType': 'image/png'});
      } catch (e) {
        images.add({'data': imageB64, 'mimeType': 'image/png'});
      }

      // Upscale via ComfyUI if requested
      if (upscaleTo != null && upscaleTo > outW && images.isNotEmpty && config.comfyuiUrl.isNotEmpty) {
        _imageJobs[jobId]!['status'] = 'upscaling';
        _log.info('Job $jobId: upscaling API images to ${upscaleTo}px via ComfyUI');
        final upClient = HttpClient();
        try {
          final upscaled = await _upscaleImages(upClient, images, upscaleTo, outW, outH, backend, prompt);
          if (upscaled.isNotEmpty) { images.clear(); images.addAll(upscaled); }
        } finally {
          upClient.close();
        }
      }

      final apiDoneUserId = _imageJobs[jobId]?['user_id'];
      _imageJobs[jobId] = {
        'status': 'done',
        'images': images,
        'model': backend,
        'seed': 0,
        if (apiDoneUserId != null) 'user_id': apiDoneUserId,
      };
    } catch (e) {
      _log.severe('API image job error: $e');
      _failJob(jobId, '$e');
    }
  }

  /// Snap arbitrary dimensions to valid OpenAI DALL-E 3 sizes.
  static String _snapOpenaiSize(int w, int h) {
    final ratio = w / (h == 0 ? 1 : h);
    if (ratio > 1.2) return '1792x1024';
    if (ratio < 0.8) return '1024x1792';
    return '1024x1024';
  }

  // ── Img2Img ComfyUI Workflow ──────────────────────────────────────

  Map<String, dynamic> _buildComfyImg2ImgWorkflow({
    required String model,
    required String prompt,
    required String negPrompt,
    required int width,
    required int height,
    int? steps,
    int? seed,
    required String refFilename,
    required double denoise,
  }) {
    final rng = seed ?? 0;
    const models = {
      'flux_schnell': {'unet': 'flux1-schnell.safetensors', 'steps': 4, 'cfg': 1.0, 'type': 'flux'},
      'flux_dev':     {'unet': 'flux1-dev.safetensors',     'steps': 25, 'cfg': 1.0, 'type': 'flux'},
      'sdxl_lightning': {'ckpt': 'sd_xl_base_1.0.safetensors', 'unet': 'sdxl_lightning_4step.safetensors', 'steps': 4, 'cfg': 1.5, 'type': 'sdxl'},
      'sdxl_turbo':   {'ckpt': 'sdxl_turbo.safetensors', 'steps': 1, 'cfg': 1.0, 'type': 'sdxl'},
      'dreamshaper':      {'ckpt': 'dreamshaper_8.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sdxl'},
      'realistic_vision': {'ckpt': 'realistic_vision_v5.safetensors',  'steps': 30, 'cfg': 7.0, 'type': 'sdxl'},
      'deliberate':       {'ckpt': 'deliberate_v3.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sdxl'},
    };
    final cfg = models[model] ?? models['sdxl_lightning']!;
    final s = steps ?? (cfg['steps'] as int);
    final c = cfg['cfg'] as double;
    final type = cfg['type'] as String;

    if (type == 'flux') {
      // FLUX img2img: LoadImage → VAEEncode → KSampler(denoise<1) → VAEDecode → Save
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'simple',
          'denoise': denoise,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['12', 0]}},
        '4': {'class_type': 'UNETLoader', 'inputs': {'unet_name': cfg['unet'], 'weight_dtype': 'default'}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['11', 0]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['11', 0]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['10', 0]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_img2img_$model', 'images': ['8', 0]}},
        '10': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
        '11': {'class_type': 'DualCLIPLoader', 'inputs': {'clip_name1': 'clip_l.safetensors', 'clip_name2': 't5xxl_fp16.safetensors', 'type': 'flux'}},
        '13': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '12': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['13', 0], 'vae': ['10', 0]}},
      };
    } else {
      // SDXL img2img: LoadImage → VAEEncode → KSampler(denoise<1) → VAEDecode → Save
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'normal',
          'denoise': denoise,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['12', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_img2img_$model', 'images': ['8', 0]}},
        '13': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '12': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['13', 0], 'vae': ['4', 2]}},
      };
    }
  }

  // ── Inpainting ComfyUI Workflow ──────────────────────────────────

  Map<String, dynamic> _buildComfyInpaintWorkflow({
    required String model,
    required String prompt,
    required String negPrompt,
    required int width,
    required int height,
    int? steps,
    int? seed,
    required String refFilename,
    required String maskFilename,
    required double denoise,
  }) {
    final rng = seed ?? 0;
    const models = {
      'flux_schnell': {'unet': 'flux1-schnell.safetensors', 'steps': 4, 'cfg': 1.0, 'type': 'flux'},
      'flux_dev':     {'unet': 'flux1-dev.safetensors',     'steps': 25, 'cfg': 1.0, 'type': 'flux'},
      'sdxl_lightning': {'ckpt': 'sd_xl_base_1.0.safetensors', 'unet': 'sdxl_lightning_4step.safetensors', 'steps': 4, 'cfg': 1.5, 'type': 'sdxl'},
      'sdxl_turbo':   {'ckpt': 'sdxl_turbo.safetensors', 'steps': 1, 'cfg': 1.0, 'type': 'sdxl'},
      'dreamshaper':      {'ckpt': 'dreamshaper_8.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sdxl'},
      'realistic_vision': {'ckpt': 'realistic_vision_v5.safetensors',  'steps': 30, 'cfg': 7.0, 'type': 'sdxl'},
      'deliberate':       {'ckpt': 'deliberate_v3.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sdxl'},
    };
    final cfg = models[model] ?? models['sdxl_lightning']!;
    final s = steps ?? (cfg['steps'] as int);
    final c = cfg['cfg'] as double;
    final type = cfg['type'] as String;

    // Inpaint pipeline: LoadImage(src) → VAEEncode → SetLatentNoiseMask(mask) → KSampler → VAEDecode → Save
    // The mask is white=edit, black=keep. SetLatentNoiseMask tells KSampler to only denoise masked areas.
    if (type == 'flux') {
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'simple',
          'denoise': denoise,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['15', 0]}},
        '4': {'class_type': 'UNETLoader', 'inputs': {'unet_name': cfg['unet'], 'weight_dtype': 'default'}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['11', 0]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['11', 0]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['10', 0]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_inpaint_$model', 'images': ['8', 0]}},
        '10': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'ae.safetensors'}},
        '11': {'class_type': 'DualCLIPLoader', 'inputs': {'clip_name1': 'clip_l.safetensors', 'clip_name2': 't5xxl_fp16.safetensors', 'type': 'flux'}},
        '13': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '12': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['13', 0], 'vae': ['10', 0]}},
        '14': {'class_type': 'LoadImage', 'inputs': {'image': maskFilename}},
        '15': {'class_type': 'SetLatentNoiseMask', 'inputs': {'samples': ['12', 0], 'mask': ['14', 1]}},
      };
    } else {
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'normal',
          'denoise': denoise,
          'model': ['4', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['15', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_inpaint_$model', 'images': ['8', 0]}},
        '13': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '12': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['13', 0], 'vae': ['4', 2]}},
        '14': {'class_type': 'LoadImage', 'inputs': {'image': maskFilename}},
        '15': {'class_type': 'SetLatentNoiseMask', 'inputs': {'samples': ['12', 0], 'mask': ['14', 1]}},
      };
    }
  }

  // ── ControlNet ComfyUI Workflow ─────────────────────────────────

  Map<String, dynamic> _buildComfyControlNetWorkflow({
    required String model,
    required String prompt,
    required String negPrompt,
    required int width,
    required int height,
    int? steps,
    int? seed,
    required String controlType,
    required String controlFilename,
    required double strength,
    String? refFilename,
    double denoise = 1.0,
  }) {
    final rng = seed ?? 0;
    // ControlNet model filenames — must be installed in ComfyUI models/controlnet/
    const controlModels = {
      'openpose': 'control_v11p_sd15_openpose.pth',
      'depth':    'control_v11f1p_sd15_depth.pth',
      'canny':    'control_v11p_sd15_canny.pth',
    };
    // SDXL-only for ControlNet (FLUX ControlNet requires separate union models)
    const models = {
      'flux_schnell':   {'ckpt': 'sd_xl_base_1.0.safetensors', 'steps': 20, 'cfg': 7.0},
      'flux_dev':       {'ckpt': 'sd_xl_base_1.0.safetensors', 'steps': 20, 'cfg': 7.0},
      'sdxl_lightning': {'ckpt': 'sd_xl_base_1.0.safetensors', 'steps': 20, 'cfg': 7.0},
      'sdxl_turbo':     {'ckpt': 'sd_xl_base_1.0.safetensors', 'steps': 20, 'cfg': 7.0},
    };
    final cfg = models[model] ?? models['sdxl_lightning']!;
    final s = steps ?? (cfg['steps'] as int);
    final c = cfg['cfg'] as double;
    final cnModel = controlModels[controlType] ?? controlModels['openpose']!;

    // ControlNet pipeline:
    // LoadImage(control) → ControlNetLoader → ControlNetApply(positive conditioning + control image)
    // → KSampler(with modified conditioning) → VAEDecode → SaveImage
    // If refFilename provided: img2img + controlnet (VAEEncode ref → latent)
    final latentNode = refFilename != null
        ? {'class_type': 'VAEEncode', 'inputs': {'pixels': ['20', 0], 'vae': ['4', 2]}}
        : {'class_type': 'EmptyLatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}};

    return {
      '3': {'class_type': 'KSampler', 'inputs': {
        'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'normal',
        'denoise': refFilename != null ? denoise : 1.0,
        // ControlNetApplyAdvanced outputs: [0]=positive, [1]=negative
        'model': ['4', 0], 'positive': ['17', 0], 'negative': ['17', 1], 'latent_image': ['5', 0]}},
      '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
      '5': latentNode,
      '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
      '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
      '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
      '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_controlnet_$model', 'images': ['8', 0]}},
      // ControlNet: load control image + model, apply to conditioning
      '16': {'class_type': 'ControlNetLoader', 'inputs': {'control_net_name': cnModel}},
      '19': {'class_type': 'LoadImage', 'inputs': {'image': controlFilename}},
      '17': {'class_type': 'ControlNetApplyAdvanced', 'inputs': {
        'positive': ['6', 0], 'negative': ['7', 0], 'control_net': ['16', 0],
        'image': ['19', 0], 'strength': strength,
        'start_percent': 0.0, 'end_percent': 1.0}},
      // Reference image for img2img + ControlNet combo
      if (refFilename != null)
        '20': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
    };
  }

  // ── Upload Image to ComfyUI ─────────────────────────────────────

  /// Upload a base64 data URI image to ComfyUI's input directory.
  /// Returns the filename ComfyUI stored it as, or null on failure.
  Future<String?> _uploadToComfyUI(HttpClient client, String dataUri) async {
    try {
      // Parse data URI: "data:image/png;base64,iVBOR..."
      final commaIdx = dataUri.indexOf(',');
      if (commaIdx < 0) return null;
      final base64Data = dataUri.substring(commaIdx + 1);
      final bytes = base64Decode(base64Data);

      // Determine extension from MIME
      final mimeMatch = RegExp(r'data:image/(\w+)').firstMatch(dataUri);
      final ext = mimeMatch?.group(1) ?? 'png';
      final filename = 'ref_${DateTime.now().millisecondsSinceEpoch}.$ext';

      // POST multipart to ComfyUI /upload/image
      final uri = Uri.parse('${config.comfyuiUrl}/upload/image');
      final boundary = '----AichatUpload${DateTime.now().millisecondsSinceEpoch}';
      final req = await client.postUrl(uri);
      req.headers.contentType = ContentType('multipart', 'form-data', parameters: {'boundary': boundary});

      final bodyBytes = BytesBuilder();
      // Add image field
      bodyBytes.add(utf8.encode('--$boundary\r\n'));
      bodyBytes.add(utf8.encode('Content-Disposition: form-data; name="image"; filename="$filename"\r\n'));
      bodyBytes.add(utf8.encode('Content-Type: image/$ext\r\n\r\n'));
      bodyBytes.add(bytes);
      bodyBytes.add(utf8.encode('\r\n--$boundary--\r\n'));

      req.contentLength = bodyBytes.length;
      req.add(bodyBytes.takeBytes());
      final resp = await req.close().timeout(const Duration(seconds: 30));
      final respBody = await resp.transform(utf8.decoder).join();

      if (resp.statusCode != 200) {
        _log.warning('ComfyUI upload failed: ${resp.statusCode} $respBody');
        return null;
      }

      final data = jsonDecode(respBody);
      return (data is Map ? data['name'] as String? : null) ?? filename;
    } catch (e) {
      _log.warning('ComfyUI upload error: $e');
      return null;
    }
  }

  // ── Upscale Images via ComfyUI ──────────────────────────────────

  /// Upscale generated images using RealESRGAN_x4plus on ComfyUI.
  Future<List<Map<String, dynamic>>> _upscaleImages(
    HttpClient client,
    List<Map<String, dynamic>> origImages,
    int targetSize,
    int origWidth,
    int origHeight,
    String model,
    String prompt,
  ) async {
    final results = <Map<String, dynamic>>[];

    for (final img in origImages) {
      final savedAs = img['savedAs'] as String?;
      if (savedAs == null) continue;

      // Read the generated image and upload to ComfyUI as input
      final imgFile = File('/app/pictures/$savedAs');
      if (!imgFile.existsSync()) continue;
      final imgBytes = imgFile.readAsBytesSync();
      final b64 = 'data:image/jpeg;base64,${base64Encode(imgBytes)}';
      final refFilename = await _uploadToComfyUI(client, b64);
      if (refFilename == null) continue;

      // Build upscale workflow
      final workflow = <String, dynamic>{
        '1': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '2': {'class_type': 'UpscaleModelLoader', 'inputs': {'model_name': 'RealESRGAN_x4plus.pth'}},
        '3': {'class_type': 'ImageUpscaleWithModel', 'inputs': {'upscale_model': ['2', 0], 'image': ['1', 0]}},
        // Resize to exact target (4x upscale may overshoot)
        // Preserve aspect ratio: scale longest edge to targetSize
        '4': {'class_type': 'ImageScale', 'inputs': {
          'image': ['3', 0],
          'width': origWidth >= origHeight ? targetSize : (targetSize * origWidth / origHeight).round(),
          'height': origHeight >= origWidth ? targetSize : (targetSize * origHeight / origWidth).round(),
          'upscale_method': 'lanczos', 'crop': 'disabled'}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_upscale', 'images': ['4', 0]}},
      };

      // Submit upscale workflow
      try {
        final submitReq = await client.postUrl(Uri.parse('${config.comfyuiUrl}/prompt'));
        submitReq.headers.contentType = ContentType.json;
        submitReq.write(jsonEncode({'prompt': workflow}));
        final submitResp = await submitReq.close().timeout(const Duration(seconds: 30));
        final submitBody = await submitResp.transform(utf8.decoder).join();
        if (submitResp.statusCode != 200) continue;
        final submitData = jsonDecode(submitBody);
        final promptId = submitData is Map ? submitData['prompt_id'] as String? : null;
        if (promptId == null) continue;

        // Poll for completion (upscale is fast, ~10-30s)
        Map<String, dynamic>? outputs;
        for (var i = 0; i < 120; i++) {
          await Future.delayed(const Duration(milliseconds: 500));
          try {
            final histReq = await client.getUrl(Uri.parse('${config.comfyuiUrl}/history/$promptId'));
            final histResp = await histReq.close().timeout(const Duration(seconds: 10));
            if (histResp.statusCode != 200) continue;
            final histBody = await histResp.transform(utf8.decoder).join();
            final hist = jsonDecode(histBody);
            if (hist is! Map || !hist.containsKey(promptId)) continue;
            final entry = hist[promptId];
            if (entry is! Map) continue;
            final outs = entry['outputs'];
            if (outs is Map<String, dynamic> && outs.containsKey('9')) { outputs = outs; break; }
          } catch (_) { continue; }
        }
        if (outputs == null) continue;

        // Fetch upscaled image
        final outputNode = outputs['9'];
        if (outputNode is! Map || outputNode['images'] is! List) continue;
        for (final imgInfo in (outputNode['images'] as List)) {
          if (imgInfo is! Map) continue;
          final fname = imgInfo['filename'] as String?;
          if (fname == null) continue;
          final subfolder = (imgInfo['subfolder'] as String?) ?? '';
          final viewUrl = Uri.parse('${config.comfyuiUrl}/view').replace(
            queryParameters: {'filename': fname, 'subfolder': subfolder, 'type': 'output'},
          );
          final imgReq = await client.getUrl(viewUrl);
          final imgResp = await imgReq.close().timeout(const Duration(seconds: 30));
          final builder = BytesBuilder(copy: false);
          await imgResp.forEach(builder.add);
          final upscaledBytes = builder.takeBytes();

          // Save upscaled version
          final ts = DateTime.now().toIso8601String().replaceAll(RegExp(r'[:\-T]'), '').substring(0, 15);
          final safePrompt = prompt.length > 20 ? prompt.substring(0, 20) : prompt;
          final cleanPrompt = safePrompt.replaceAll(RegExp(r'[^a-zA-Z0-9 ]'), '').trim().replaceAll(' ', '_');
          final upSavedAs = '${model}_${targetSize}px_${ts}_$cleanPrompt.jpg';
          try {
            File('/app/pictures/$upSavedAs').writeAsBytesSync(upscaledBytes);
            results.add({
              'filename': fname,
              'savedAs': upSavedAs,
              'url': '/api/image/download/$upSavedAs',
            });
          } catch (e) {
            _log.warning('Upscale save failed: $e');
          }
        }
      } catch (e) {
        _log.warning('Upscale workflow error: $e');
      }
    }
    return results;
  }

  // ── Image Search Reference ──────────────────────────────────────

  Future<Response> _imageSearchReference(Request request) async {
    final body = await _readJson(request);
    final query = (body?['query'] as String?)?.trim() ?? '';
    if (query.isEmpty) return _json({'error': 'query is required'}, status: 400);
    final limit = _toInt(body?['limit'], 8);

    try {
      final result = await mcp.callTool('image', {
        'action': 'search',
        'query': query,
        'max_results': limit,
      });
      // Parse image URLs from MCP result
      final urls = <String>[];
      if (result['content'] is List) {
        for (final item in (result['content'] as List)) {
          if (item is Map && item['type'] == 'text') {
            // Parse URLs from text response
            final text = item['text'] as String? ?? '';
            final urlPattern = RegExp(r'https?://\S+\.(?:jpg|jpeg|png|webp|gif)', caseSensitive: false);
            for (final match in urlPattern.allMatches(text)) {
              urls.add(match.group(0)!);
            }
          }
        }
      }
      return _json({'urls': urls.take(limit).toList()});
    } catch (e) {
      return _json({'error': 'Search failed: $e', 'urls': []});
    }
  }

  // ── Tool Result Sanitizer ─────────────────────────────────────────

  /// Clean tool results before feeding back to LLM to prevent
  /// raw data (base64, JSON dumps, binary) from leaking into responses.
  String _sanitizeToolResult(String text) {
    if (text.length < 200) return text;

    var cleaned = text;

    // Strip base64 data blocks (long alphanumeric strings 100+ chars)
    cleaned = cleaned.replaceAll(
      RegExp(r'[A-Za-z0-9+/=]{100,}'),
      '[binary data removed]',
    );

    // Strip data: URIs
    cleaned = cleaned.replaceAll(
      RegExp(r'data:[a-z/+]+;base64,[A-Za-z0-9+/=]+'),
      '[embedded data removed]',
    );

    // Strip raw byte strings like b'...'
    cleaned = cleaned.replaceAll(
      RegExp(r"b'[^']{50,}'"),
      '[binary data removed]',
    );

    // Strip very long repeated number sequences (coordinates, pixel data)
    cleaned = cleaned.replaceAll(
      RegExp(r'(\d{1,5}[-,. ]\s*){20,}'),
      '[numeric data removed] ',
    );

    // Strip raw hex dumps
    cleaned = cleaned.replaceAll(
      RegExp(r'(\\x[0-9a-fA-F]{2}){10,}'),
      '[hex data removed]',
    );

    // Strip very large JSON-looking blocks (nested braces with many fields)
    cleaned = cleaned.replaceAll(
      RegExp(r'\{[^{}]{5000,}\}'),
      '[data object removed]',
    );

    // Truncate if still too long (keep first 2000 chars)
    if (cleaned.length > 2000) {
      cleaned =
          '${cleaned.substring(0, 2000)}\n[... truncated ${cleaned.length - 2000} chars]';
    }

    return cleaned;
  }

  /// Extract HTTP image URLs from tool result text, filtering out junk.
  ///
  /// Skips: site logos, favicons, placeholder/default images, tracking pixels,
  /// tiny icons, and generic CDN chrome. Only returns URLs likely to be actual
  /// content images worth rendering.
  List<String> _extractImageUrls(String text) {
    final urls = <String>[];
    final pattern = RegExp(
      r'https?://[^\s"<>]+\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s"<>]*)?',
      caseSensitive: false,
    );
    for (final match in pattern.allMatches(text)) {
      final url = match.group(0)!;
      if (_isJunkImage(url)) continue;
      urls.add(url);
      if (urls.length >= 6) break;
    }
    return urls;
  }

  /// True if [url] looks like a logo, favicon, placeholder, or tracking pixel.
  /// Uses path-segment matching to avoid false positives from substring hits.
  bool _isJunkImage(String url) {
    final lower = url.toLowerCase();
    // Check path segments — more precise than substring
    final segments = Uri.tryParse(lower)?.pathSegments ?? lower.split('/');
    const junkSegments = {
      'logo', 'favicon', 'icon', 'avatar', 'placeholder',
      'pixel', 'tracking', 'beacon', 'spacer', 'blank', 'spinner',
      'loading', 'arrow', 'button', 'badge', 'sprite', 'emoji',
      'ads', 'ad', '1x1', '2x2',
    };
    for (final seg in segments) {
      if (junkSegments.contains(seg)) return true;
    }
    // Domain-level blocks
    const junkDomains = ['gravatar.com', 'googleusercontent.com/s/'];
    for (final d in junkDomains) {
      if (lower.contains(d)) return true;
    }
    // SVGs are almost always icons
    if (lower.endsWith('.svg')) return true;
    // Very short filenames (< 4 chars before extension) are usually icons
    final filename = url.split('/').last.split('?').first;
    if (filename.length < 4) return true;
    return false;
  }

  // ── Tool Argument Inference ──────────────────────────────────────

  /// Infer reasonable default arguments for a mega-tool when the LLM
  /// produced empty or incomplete arguments.
  Map<String, dynamic> _inferToolArgs(String toolName, String userText) {
    switch (toolName) {
      case 'web':
        return {'action': 'search', 'query': userText};
      case 'browser':
        return {'action': 'navigate', 'url': userText};
      case 'image':
        return {'action': 'search', 'query': userText, 'count': '6'};
      case 'research':
        return {'action': 'deep', 'question': userText};
      case 'data':
        return {'action': 'search', 'q': userText};
      case 'memory':
        return {'action': 'recall', 'pattern': userText};
      case 'knowledge':
        return {'action': 'search', 'query': userText};
      case 'vector':
        return {'action': 'search', 'query': userText};
      case 'code':
        return {'action': 'python', 'code': userText};
      case 'planner':
        return {'action': 'plan', 'task': userText};
      default:
        return {'action': 'search', 'query': userText};
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────

  /// Send an SSE comment to keep the connection alive through proxies.
  void _sseKeepalive(StreamController<List<int>> controller) {
    if (controller.isClosed) return;
    controller.add(utf8.encode(':keepalive\n\n'));
  }

  /// Run an async operation while sending SSE keepalives every 15 seconds.
  /// Prevents proxy timeouts (524) during long tool calls.
  Future<T> _withKeepalive<T>(
    StreamController<List<int>> controller,
    Future<T> Function() work,
  ) async {
    final timer = Timer.periodic(
      const Duration(seconds: 15),
      (_) => _sseKeepalive(controller),
    );
    try {
      return await work();
    } finally {
      timer.cancel();
    }
  }

  void _sseEvent(
    StreamController<List<int>> controller,
    String event,
    Map<String, dynamic> data,
  ) {
    if (controller.isClosed) return;
    final payload = 'event: $event\ndata: ${jsonEncode(data)}\n\n';
    controller.add(utf8.encode(payload));
  }

  /// Parse a value to int, handling both int and String inputs from JSON.
  int _toInt(dynamic value, int fallback) {
    if (value is int) return value;
    if (value is double) return value.toInt();
    if (value is String) return int.tryParse(value) ?? fallback;
    return fallback;
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
      'anthropic': config.anthropicApiKey.isNotEmpty,
      'openai': config.openaiApiKey.isNotEmpty,
      'google': config.googleApiKey.isNotEmpty,
    });
  }

  // ── Standalone API Chat ─────────────────────────────────────────

  Future<void> _runApiChat(
    String conversationId,
    ApiProvider provider,
    String model,
    String apiKey,
    StreamController<List<int>> controller,
  ) async {
    final messages = db.getMessages(conversationId);
    final llmMessages = messages.map((m) => m.toLlmDict()).toList();

    // Extract system prompt (first message if role=system)
    String? systemPrompt;
    if (llmMessages.isNotEmpty && llmMessages.first['role'] == 'system') {
      systemPrompt = llmMessages.first['content'] as String;
      llmMessages.removeAt(0);
    }

    final buffer = StringBuffer();
    int totalPrompt = 0, totalCompletion = 0;
    try {
      await for (final event in apiClient.chatStream(
        provider: provider,
        apiKey: apiKey,
        model: model,
        messages: llmMessages,
        systemPrompt: systemPrompt,
        maxTokens: config.maxTokens,
        temperature: config.temperature,
      )) {
        switch (event) {
          case TokenEvent(:final text):
            buffer.write(text);
            _sseEvent(controller, 'token', {'text': text});
          case ReasoningTokenEvent(:final text):
            _sseEvent(controller, 'thinking', {'text': text});
          case UsageEvent(:final promptTokens, :final completionTokens):
            totalPrompt += promptTokens;
            totalCompletion += completionTokens;
          case ErrorEvent(:final message):
            _sseEvent(controller, 'error', {'message': message});
          case DoneEvent():
            break;
          case ToolCallsEvent():
            break; // API models don't use MCP tools through this path
        }
      }
    } catch (e) {
      _sseEvent(controller, 'error', {'message': 'Stream error: $e'});
    }

    // Save assistant response with real token count if available
    final responseText = buffer.toString();
    if (responseText.isNotEmpty) {
      final realTokens = totalPrompt + totalCompletion;
      db.addMessage(
        conversationId: conversationId,
        role: 'assistant',
        content: responseText,
        tokenCount: realTokens > 0 ? realTokens : null,
      );
      db.updateTokenCount(conversationId);
      if (realTokens > 0) {
        _log.info('API chat tokens: prompt=$totalPrompt completion=$totalCompletion total=$realTokens model=$model');
      }
    }

    _sseEvent(controller, 'done', {});
    if (!controller.isClosed) controller.close();
  }

  Response _json(Map<String, dynamic> data, {int status = 200}) {
    return Response(
      status,
      body: jsonEncode(data),
      headers: {'Content-Type': 'application/json'},
    );
  }

  Future<Map<String, dynamic>?> _readJson(Request request) async {
    try {
      final body = await request.readAsString();
      if (body.isEmpty) return {};
      return jsonDecode(body) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }
}
