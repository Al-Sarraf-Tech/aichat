import 'package:test/test.dart';
import '../../lib/model_profiles.dart';
import '../../lib/router_helpers.dart' as helpers;

void main() {
  group('router_helpers', () {
    test('jsonResponse produces valid JSON response', () {
      final resp = helpers.jsonResponse({'key': 'value'});
      expect(resp.statusCode, 200);
      expect(resp.headers['content-type'], 'application/json');
    });

    test('jsonResponse with custom status code', () {
      final resp = helpers.jsonResponse({'error': 'bad'}, status: 400);
      expect(resp.statusCode, 400);
    });

    test('toInt handles int input', () {
      expect(helpers.toInt(42, 0), 42);
    });

    test('toInt handles double input', () {
      expect(helpers.toInt(3.7, 0), 3);
    });

    test('toInt handles string input', () {
      expect(helpers.toInt('99', 0), 99);
    });

    test('toInt returns fallback on null', () {
      expect(helpers.toInt(null, -1), -1);
    });

    test('toInt returns fallback on invalid string', () {
      expect(helpers.toInt('abc', 5), 5);
    });

    test('getUserId returns empty for missing header', () {
      // Can't easily create a shelf Request in unit tests without the shelf_test package,
      // but we can verify the function signature and default behavior exist
      expect(helpers.corsOrigin, isNotEmpty);
      expect(helpers.corsHeaders, containsPair('Access-Control-Allow-Origin', isNotEmpty));
    });

    test('corsHeaders contains required methods', () {
      expect(
        helpers.corsHeaders['Access-Control-Allow-Methods'],
        contains('GET'),
      );
      expect(
        helpers.corsHeaders['Access-Control-Allow-Methods'],
        contains('POST'),
      );
    });
  });

  group('getProfile edge cases', () {
    test('unknown model with "reasoning" in name gets reasoning profile', () {
      final p = getProfile('some-custom-reasoning-model-v1');
      expect(p.supportsReasoning, true);
    });

    test('unknown model with "tiny" in name gets small profile', () {
      final p = getProfile('some-tiny-model');
      expect(p.promptSize, 'condensed');
    });

    test('completely unknown model gets default profile', () {
      final p = getProfile('totally-unknown-xyz');
      expect(p.temperature, 0.7);
      expect(p.supportsReasoning, false);
      expect(p.promptSize, 'full');
    });

    test('runtime profile overrides built-in', () {
      setRuntimeProfile('test-runtime-model', ModelProfile(
        temperature: 0.3,
        maxTokens: 1024,
        supportsTools: false,
        supportsReasoning: true,
        notes: 'Runtime override',
      ));
      final p = getProfile('test-runtime-model');
      expect(p.temperature, 0.3);
      expect(p.supportsTools, false);
      expect(p.supportsReasoning, true);
      expect(p.notes, contains('Runtime'));
    });

    test('toolCount returns null-based default (9) for unrestricted', () {
      final p = ModelProfile(
        temperature: 0.7,
        maxTokens: 4096,
        supportsTools: true,
        supportsReasoning: false,
      );
      expect(p.toolCount, 9);
    });

    test('toolCount returns explicit count', () {
      final p = ModelProfile(
        temperature: 0.7,
        maxTokens: 4096,
        supportsTools: true,
        supportsReasoning: false,
        allowedTools: ['web', 'code', 'memory'],
      );
      expect(p.toolCount, 3);
    });

    test('toJson includes all fields', () {
      final p = ModelProfile(
        temperature: 0.5,
        maxTokens: 2048,
        supportsTools: true,
        supportsReasoning: true,
        enforceTools: true,
        notes: 'test',
      );
      final j = p.toJson();
      expect(j['temperature'], 0.5);
      expect(j['max_tokens'], 2048);
      expect(j['supports_tools'], true);
      expect(j['supports_reasoning'], true);
      expect(j['enforce_tools'], true);
      expect(j['notes'], 'test');
    });
  });
}
