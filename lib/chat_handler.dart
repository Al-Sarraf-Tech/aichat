/// Chat route handlers: conversation creation and SSE message streaming.
///
/// Extracted from router.dart. Handles:
///   - POST /api/conversations          → createConversation
///   - POST /api/conversations/`<id>`/messages → sendMessage
///
/// Internal helpers cover CLI-agent routing (_runCliChat) and the
/// full LM-Studio tool-loop (_runChatLoop).
library;

import 'dart:async';
import 'dart:convert';

import 'package:logging/logging.dart';
import 'package:shelf/shelf.dart' show Request, Response;

import 'compaction.dart';
import 'config.dart';
import 'database.dart';
import 'llm_client.dart';
import 'mcp_client.dart';
import 'model_profiles.dart';
import 'models.dart';
import 'personalities.dart';
import 'router_helpers.dart' as helpers;
import 'sanitizer.dart' as sanitizer;
import 'tool_router.dart' as tool_router;

final _log = Logger('ChatHandler');

class ChatHandler {
  final Config config;
  final AppDatabase db;
  final LlmClient llm;
  final McpClient mcp;
  final Compactor compactor;

  ChatHandler({
    required this.config,
    required this.db,
    required this.llm,
    required this.mcp,
    required this.compactor,
  });

  // ── Convenience delegates ─────────────────────────────────────────

  String _getUserId(Request request) => helpers.getUserId(request);
  Response _json(Map<String, dynamic> data, {int status = 200}) =>
      helpers.jsonResponse(data, status: status);
  Future<Map<String, dynamic>?> _readJson(Request request) =>
      helpers.readJson(request);
  Future<T> _withKeepalive<T>(
    StreamController<List<int>> controller,
    Future<T> Function() work,
  ) =>
      helpers.withKeepalive(controller, work);
  void _sseEvent(
    StreamController<List<int>> controller,
    String event,
    Map<String, dynamic> data,
  ) =>
      helpers.sseEvent(controller, event, data);
  String _sanitizeToolResult(String text) =>
      sanitizer.sanitizeToolResult(text);
  List<String> _extractImageUrls(String text) =>
      sanitizer.extractImageUrls(text);
  Map<String, dynamic> _inferToolArgs(String toolName, String userText) =>
      sanitizer.inferToolArgs(toolName, userText);

  // ── Public route handlers ─────────────────────────────────────────

  Future<Response> createConversation(Request request) async {
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

  Future<Response> sendMessage(Request request, String id) async {
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
          ...helpers.corsHeaders,
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
          ...helpers.corsHeaders,
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
        ...helpers.corsHeaders,
      },
    );
  }

  // ── Private helpers ───────────────────────────────────────────────

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
}
