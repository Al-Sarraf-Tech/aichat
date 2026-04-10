/// Model management handlers — listing, warmup/validation, status, unload.
///
/// Extracted from router.dart. Manages model capability caching and
/// runtime profile detection.
library;

import 'package:logging/logging.dart';
import 'package:shelf/shelf.dart' show Request, Response;

import 'config.dart';
import 'llm_client.dart';
import 'mcp_client.dart';
import 'model_profiles.dart';
import 'router_helpers.dart' as helpers;
// models.dart provides ToolCallsEvent/DoneEvent — imported transitively via llm_client

final _log = Logger('ModelHandler');

/// Handles all /api/models, /api/model-status, /api/warmup, /api/unload
/// endpoints. Maintains an in-memory validation cache ([modelCaps]).
class ModelHandler {
  final Config config;
  final LlmClient llm;
  final McpClient mcp;

  /// Validation cache: model_id → capabilities map.
  /// Written by [warmupModel], read by [listModels].
  final modelCaps = <String, Map<String, dynamic>>{};

  ModelHandler({
    required this.config,
    required this.llm,
    required this.mcp,
  });

  Future<Response> listModels(Request request) async {
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
      final caps = modelCaps[id];
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
    return helpers.jsonResponse({'models': annotated});
  }

  Future<Response> modelStatus(Request request) async {
    final status = <String, String>{};

    // OAuth CLI agents — always available if SSH is up
    status['claude'] = 'ok';
    status['codex'] = 'ok';
    status['gemini'] = 'ok';
    status['qwen'] = 'ok';

    // Cloud API providers — disabled (no keys configured)
    status['anthropic'] = 'no_key';
    status['openai'] = 'no_key';
    status['google'] = 'no_key';

    // LM Studio — check if reachable
    try {
      final r = await llm.listModels();
      status['lmstudio'] = r.isNotEmpty ? 'ok' : 'empty';
    } catch (e) {
      status['lmstudio'] = 'unreachable';
    }

    return helpers.jsonResponse({'status': status});
  }

  Future<Response> warmupModel(Request request) async {
    final body = await helpers.readJson(request);
    final model = body?['model'] as String?;
    if (model == null || model.isEmpty) {
      return helpers.jsonResponse({'error': 'model is required'}, status: 400);
    }

    // Skip if already validated
    if (modelCaps.containsKey(model)) {
      _log.info('Model $model already validated');
      return helpers.jsonResponse(
        {'status': 'ready', 'model': model, ...modelCaps[model]!},
      );
    }

    // Capacity guard: don't trigger warmup if loading this model would
    // evict another (and it's not already loaded).
    final warmupBusy = await llm.ensureModelOrBusy(
      model,
      maxLoaded: config.maxLoadedModels,
    );
    if (warmupBusy != null) {
      _log.info('Warmup skipped for $model — at capacity');
      return helpers.jsonResponse({
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
      modelCaps[model] = caps;
      return helpers.jsonResponse(
        {'status': 'limited', 'model': model, ...caps},
      );
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
        lm.contains('gemma-4') ||
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
    modelCaps[model] = caps;

    // Store detected capabilities as a runtime profile override
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
    return helpers.jsonResponse({'status': status, 'model': model, ...caps});
  }

  Future<Response> unloadModel(Request request) async {
    final body = await helpers.readJson(request);
    final model = body?['model'] as String?;
    if (model == null || model.isEmpty) {
      return helpers.jsonResponse({'status': 'skipped'});
    }
    _log.info('Unload requested for: $model (JIST auto-manages)');
    return helpers.jsonResponse({'status': 'acknowledged', 'model': model});
  }
}
