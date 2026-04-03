import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:logging/logging.dart';

import 'llm_client.dart';

final _log = Logger('ApiClient');

enum ApiProvider { anthropic, openai, google }

/// Model ID mapping: friendly name → (provider, actual API model ID)
const apiModelMap = <String, (ApiProvider, String)>{
  // Anthropic
  'api:claude:sonnet-4': (ApiProvider.anthropic, 'claude-sonnet-4-20250514'),
  'api:claude:opus-4': (ApiProvider.anthropic, 'claude-opus-4-20250514'),
  'api:claude:haiku-3.5': (ApiProvider.anthropic, 'claude-3-5-haiku-20241022'),
  // OpenAI
  'api:openai:gpt-5.4': (ApiProvider.openai, 'gpt-5.4'),
  'api:openai:gpt-4.1': (ApiProvider.openai, 'gpt-4.1'),
  'api:openai:o4-mini': (ApiProvider.openai, 'o4-mini'),
  // Google
  'api:google:gemini-2.5-flash': (ApiProvider.google, 'gemini-2.5-flash-preview-05-20'),
  'api:google:gemini-2.5-pro': (ApiProvider.google, 'gemini-2.5-pro-preview-05-06'),
};

/// Unified streaming client for Anthropic, OpenAI, and Google AI APIs.
/// Returns the same ChatEvent types as LlmClient for uniform SSE handling.
class ApiClient {
  final http.Client _client;

  ApiClient({http.Client? client}) : _client = client ?? http.Client();

  void close() => _client.close();

  /// Resolve a model string like "api:claude:sonnet-4" to provider + real model ID.
  static (ApiProvider, String)? resolve(String modelStr) {
    return apiModelMap[modelStr];
  }

  /// Stream chat events from an external API provider.
  Stream<ChatEvent> chatStream({
    required ApiProvider provider,
    required String apiKey,
    required String model,
    required List<Map<String, dynamic>> messages,
    String? systemPrompt,
    int maxTokens = 4096,
    double temperature = 0.7,
  }) {
    switch (provider) {
      case ApiProvider.anthropic:
        return _streamAnthropic(apiKey, model, messages, systemPrompt,
            maxTokens, temperature);
      case ApiProvider.openai:
        return _streamOpenAI(apiKey, model, messages, systemPrompt, maxTokens,
            temperature);
      case ApiProvider.google:
        return _streamGoogle(apiKey, model, messages, systemPrompt, maxTokens,
            temperature);
    }
  }

  // ── Anthropic ────────────────────────────────────────────────────

  Stream<ChatEvent> _streamAnthropic(
    String apiKey,
    String model,
    List<Map<String, dynamic>> messages,
    String? systemPrompt,
    int maxTokens,
    double temperature,
  ) async* {
    // Separate system prompt from messages
    final apiMessages = <Map<String, dynamic>>[];
    String? system = systemPrompt;
    for (final m in messages) {
      if (m['role'] == 'system') {
        system ??= m['content'] as String;
      } else {
        apiMessages.add({'role': m['role'], 'content': m['content']});
      }
    }

    final body = <String, dynamic>{
      'model': model,
      'max_tokens': maxTokens,
      'stream': true,
      'messages': apiMessages,
    };
    if (system != null && system.isNotEmpty) body['system'] = system;
    body['temperature'] = temperature;

    final request = http.Request(
        'POST', Uri.parse('https://api.anthropic.com/v1/messages'));
    request.headers['x-api-key'] = apiKey;
    request.headers['anthropic-version'] = '2023-06-01';
    request.headers['content-type'] = 'application/json';
    request.body = jsonEncode(body);

    yield* _parseSSE(request, (event, data) sync* {
      if (event == 'content_block_delta') {
        final delta = data['delta'] as Map<String, dynamic>?;
        if (delta != null) {
          final type = delta['type'] as String?;
          if (type == 'text_delta') {
            yield TokenEvent(delta['text'] as String? ?? '');
          } else if (type == 'thinking_delta') {
            yield ReasoningTokenEvent(delta['thinking'] as String? ?? '');
          }
        }
      } else if (event == 'message_start') {
        // Anthropic emits input token count at stream start
        final message = data['message'] as Map<String, dynamic>?;
        final usage = message?['usage'] as Map<String, dynamic>?;
        if (usage != null) {
          yield UsageEvent(
            promptTokens: usage['input_tokens'] as int? ?? 0,
            completionTokens: 0,
          );
        }
      } else if (event == 'message_delta') {
        // Anthropic emits output token count at stream end
        final usage = data['usage'] as Map<String, dynamic>?;
        if (usage != null) {
          yield UsageEvent(
            promptTokens: 0,
            completionTokens: usage['output_tokens'] as int? ?? 0,
          );
        }
      } else if (event == 'message_stop') {
        yield DoneEvent('stop');
      } else if (event == 'error') {
        final err = data['error'] as Map<String, dynamic>?;
        yield ErrorEvent(err?['message'] as String? ?? 'Unknown error');
      }
    });
  }

  // ── OpenAI ──────────────────────────────────────────────────────

  Stream<ChatEvent> _streamOpenAI(
    String apiKey,
    String model,
    List<Map<String, dynamic>> messages,
    String? systemPrompt,
    int maxTokens,
    double temperature,
  ) async* {
    final apiMessages = <Map<String, dynamic>>[];
    if (systemPrompt != null && systemPrompt.isNotEmpty) {
      apiMessages.add({'role': 'system', 'content': systemPrompt});
    }
    for (final m in messages) {
      apiMessages.add({'role': m['role'], 'content': m['content']});
    }

    final body = <String, dynamic>{
      'model': model,
      'max_tokens': maxTokens,
      'stream': true,
      'stream_options': {'include_usage': true},
      'messages': apiMessages,
    };
    if (!model.contains('o4') && !model.contains('o3')) {
      body['temperature'] = temperature;
    }

    final request = http.Request(
        'POST', Uri.parse('https://api.openai.com/v1/chat/completions'));
    request.headers['authorization'] = 'Bearer $apiKey';
    request.headers['content-type'] = 'application/json';
    request.body = jsonEncode(body);

    yield* _parseSSE(request, (event, data) sync* {
      // OpenAI emits usage in the final chunk when stream_options.include_usage is true
      final usage = data['usage'] as Map<String, dynamic>?;
      if (usage != null) {
        yield UsageEvent(
          promptTokens: usage['prompt_tokens'] as int? ?? 0,
          completionTokens: usage['completion_tokens'] as int? ?? 0,
        );
      }
      final choices = data['choices'] as List?;
      if (choices == null || choices.isEmpty) return;
      final delta = (choices[0] as Map)['delta'] as Map<String, dynamic>?;
      final finish = (choices[0] as Map)['finish_reason'] as String?;
      if (delta != null) {
        final content = delta['content'] as String?;
        if (content != null && content.isNotEmpty) {
          yield TokenEvent(content);
        }
      }
      if (finish != null) {
        yield DoneEvent(finish);
      }
    });
  }

  // ── Google ──────────────────────────────────────────────────────

  Stream<ChatEvent> _streamGoogle(
    String apiKey,
    String model,
    List<Map<String, dynamic>> messages,
    String? systemPrompt,
    int maxTokens,
    double temperature,
  ) async* {
    // Convert to Google format: {role: "user"/"model", parts: [{text: "..."}]}
    final contents = <Map<String, dynamic>>[];
    for (final m in messages) {
      if (m['role'] == 'system') continue; // Handled via systemInstruction
      final role = m['role'] == 'assistant' ? 'model' : 'user';
      contents.add({
        'role': role,
        'parts': [
          {'text': m['content']}
        ],
      });
    }

    final body = <String, dynamic>{
      'contents': contents,
      'generationConfig': {
        'maxOutputTokens': maxTokens,
        'temperature': temperature,
      },
    };
    if (systemPrompt != null && systemPrompt.isNotEmpty) {
      body['systemInstruction'] = {
        'parts': [
          {'text': systemPrompt}
        ]
      };
    }

    final url = Uri.parse(
        'https://generativelanguage.googleapis.com/v1beta/models/$model:streamGenerateContent?alt=sse&key=$apiKey');
    final request = http.Request('POST', url);
    request.headers['content-type'] = 'application/json';
    request.body = jsonEncode(body);

    yield* _parseSSE(request, (event, data) sync* {
      final candidates = data['candidates'] as List?;
      if (candidates == null || candidates.isEmpty) return;
      final content =
          (candidates[0] as Map)['content'] as Map<String, dynamic>?;
      if (content == null) return;
      final parts = content['parts'] as List?;
      if (parts == null || parts.isEmpty) return;
      final text = (parts[0] as Map)['text'] as String?;
      if (text != null && text.isNotEmpty) {
        yield TokenEvent(text);
      }
      final finish =
          (candidates[0] as Map)['finishReason'] as String?;
      if (finish == 'STOP') {
        yield DoneEvent('stop');
      } else if (finish == 'SAFETY') {
        yield ErrorEvent('Content blocked by safety filter');
        yield DoneEvent('safety');
      } else if (finish == 'MAX_TOKENS') {
        yield ErrorEvent('Response truncated (max tokens reached)');
        yield DoneEvent('max_tokens');
      }
    });
  }

  // ── SSE Parser ─────────────────────────────────────────────────

  Stream<ChatEvent> _parseSSE(
    http.Request request,
    Iterable<ChatEvent> Function(String event, Map<String, dynamic> data)
        handler,
  ) async* {
    http.StreamedResponse response;
    try {
      response = await _client.send(request);
    } catch (e) {
      yield ErrorEvent('Connection failed: $e');
      return;
    }

    if (response.statusCode != 200) {
      final body = await response.stream.bytesToString();
      String msg;
      try {
        final j = jsonDecode(body);
        msg = j['error']?['message'] as String? ??
            j['error']?.toString() ??
            body;
      } catch (_) {
        msg = body.length > 300 ? body.substring(0, 300) : body;
      }
      yield ErrorEvent('API error ${response.statusCode}: $msg');
      return;
    }

    String buf = '';
    String currentEvent = '';
    const maxBufSize = 65536; // 64KB safety cap
    await for (final chunk in response.stream.transform(utf8.decoder)) {
      buf += chunk;
      if (buf.length > maxBufSize) {
        _log.warning('SSE buffer exceeded ${maxBufSize}B, truncating');
        buf = buf.substring(buf.length - maxBufSize);
      }
      final lines = buf.split('\n');
      buf = lines.removeLast(); // Keep incomplete line in buffer

      for (final line in lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.substring(7).trim();
        } else if (line.startsWith('data: ')) {
          final dataStr = line.substring(6).trim();
          if (dataStr == '[DONE]') {
            yield DoneEvent('stop');
            return;
          }
          try {
            final data = jsonDecode(dataStr) as Map<String, dynamic>;
            yield* Stream.fromIterable(handler(currentEvent, data));
          } catch (e) {
            _log.fine('SSE parse error: $e for line: $dataStr');
          }
          currentEvent = '';
        }
      }
    }
    // Stream ended without explicit [DONE]
    yield DoneEvent('stop');
  }
}
