import 'package:test/test.dart';
import '../lib/model_profiles.dart';
import '../lib/personalities.dart';

void main() {
  group('Model Profiles (updated inventory)', () {
    test('gpt-oss-20b-absolute-heresy-i1 has 9 tools', () {
      final p = getProfile('gpt-oss-20b-absolute-heresy-i1');
      expect(p.toolCount, 9);
      expect(p.supportsReasoning, false);
      expect(p.promptSize, 'full');
    });

    test('dolphin has 9 tools, reasoning, enforceTools', () {
      final p = getProfile('cognitivecomputations_dolphin-mistral-24b-venice-edition');
      expect(p.toolCount, 9);
      expect(p.supportsReasoning, true);
      expect(p.enforceTools, true);
    });

    test('qwen3.5-27b reasoning distilled has 9 tools', () {
      final p = getProfile('qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2');
      expect(p.toolCount, 9);
      expect(p.supportsReasoning, true);
      expect(p.maxTokens, 8192);
    });

    test('gemma-4-26b-a4b-it has 7 tools, reasoning', () {
      final p = getProfile('gemma-4-26b-a4b-it');
      expect(p.toolCount, 7);
      expect(p.supportsReasoning, true);
    });

    test('gemma-4-e2b-it is small model with 5 tools', () {
      final p = getProfile('gemma-4-e2b-it');
      expect(p.toolCount, 5);
      expect(p.promptSize, 'condensed');
      expect(p.systemPromptMaxChars, 4000);
    });

    test('qwen3.5-9b unchanged', () {
      final p = getProfile('qwen/qwen3.5-9b');
      expect(p.toolCount, 7);
      expect(p.supportsReasoning, true);
    });

    test('heuristic: gemma-4 detected as reasoning', () {
      final p = getProfile('some-future-gemma-4-model');
      expect(p.supportsReasoning, true);
    });

    test('removed models fall back to defaults', () {
      final p1 = getProfile('openai/gpt-oss-20b');
      expect(p1.notes, contains('Default'));
      final p2 = getProfile('ibm/granite-4-h-tiny');
      expect(p2.notes, contains('small'));
    });
  });

  group('Personalities (Porn Master)', () {
    test('porn_master exists', () {
      final p = getPersonality('porn_master');
      expect(p, isNotNull);
      expect(p!.name, 'Porn Master');
      expect(p.category, 'Special');
    });

    test('porn_master visible only for dolphin models', () {
      final p = getPersonality('porn_master')!;
      expect(p.visibleFor('cognitivecomputations_dolphin-mistral-24b-venice-edition'), true);
      expect(p.visibleFor('some-future-dolphin-model'), true);
      expect(p.visibleFor('qwen/qwen3.5-9b'), false);
      expect(p.visibleFor('gemma-4-26b-a4b-it'), false);
      expect(p.visibleFor(null), false);
    });

    test('personality count correct', () {
      final all = personalityIndex();
      final dolphin = personalityIndex(model: 'cognitivecomputations_dolphin-mistral-24b-venice-edition');
      final qwen = personalityIndex(model: 'qwen/qwen3.5-9b');
      // All unrestricted + porn_master
      expect(dolphin.length, all.length + 1);
      // No model-restricted for qwen
      expect(qwen.length, all.length);
    });
  });
}
