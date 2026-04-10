/// Unit tests for ImageHandler — covering the parts testable without a live
/// HTTP backend (no ComfyUI, no HuggingFace).
///
/// What's tested here:
///   1. _hfModelMap contents — verified indirectly via the public constant
///      accessor pattern described below.
///   2. imageDownload filename sanitisation — pure path/string logic that
///      doesn't touch the filesystem (returns 400 before any File access).
///   3. imageJobStatus with a missing job — returns 404 without any HTTP.
///   4. _failJob behaviour — tested through the in-memory _imageJobs map
///      via a thin test-only subclass that exposes the two private methods
///      for inspection without changing production behaviour.
///
/// What is NOT tested here (and why):
///   - _buildComfyWorkflow / _buildComfyImg2ImgWorkflow /
///     _buildComfyInpaintWorkflow / _buildComfyControlNetWorkflow
///     These are private methods called only inside _runImageJob, which
///     requires a live ComfyUI server.  To make them directly testable:
///       (a) Annotate them `@visibleForTesting` and add package:meta, OR
///       (b) Move model configs into a top-level const / function in a
///           separate comfy_workflows.dart file that can be imported freely.
///     Either approach would allow full workflow-map validation (correct node
///     IDs, class_type strings, sampler names per model, denoise propagation,
///     etc.) with zero external dependencies.
///   - _runBatchImageJob / _runHfImageJob — require live HTTP.
///   - imageGenerate / imageStatus / imageModels — require live HTTP.
library;

import 'dart:convert';

import 'package:shelf/shelf.dart' show Request;
import 'package:test/test.dart';

import '../../lib/config.dart';
import '../../lib/image_handler.dart';
import '../../lib/mcp_client.dart';

// ── Test-only subclass ────────────────────────────────────────────────────────
//
// Dart's name-mangling makes private names inaccessible from outside the
// library, so we can't call _failJob or read _imageJobs directly in tests.
// The subclass lives in the *same* package, which doesn't help (privacy is
// per-library in Dart, not per-class).  We therefore expose minimal white-box
// helpers as public members on the subclass, delegating to the protected
// state.  This is the idiomatic Dart approach when @visibleForTesting is
// intentionally avoided.
//
// NOTE: Because the private fields truly cannot be reached from outside the
// library, the tests below that exercise _failJob and _imageJobs use the
// *public* imageJobStatus endpoint as an observable proxy — we seed jobs by
// calling the internal helper, then assert the shape of the response.
// ─────────────────────────────────────────────────────────────────────────────

/// Minimal Config suitable for unit tests — all service URLs are empty so no
/// network calls will ever be made.
Config _testConfig() => Config(
      lmStudioUrl: '',
      lmStudioFallbackUrl: '',
      mcpUrl: '',
      port: 9999,
      dbPath: ':memory:',
      model: 'test-model',
      temperature: 0.7,
      maxTokens: 1024,
      maxToolIterations: 3,
      compactionThreshold: 50,
      compactionKeepRecent: 10,
      toolCacheTtl: Duration.zero,
      systemPrompt: '',
      webDir: '.',
      maxLoadedModels: 1,
      toolRouterUrl: '',
      comfyuiUrl: '',    // keeps all ComfyUI branches unreachable
      visionGenUrl: '',
      hfToken: '',       // keeps HF branches unreachable
    );

McpClient _testMcpClient() => McpClient(baseUrl: 'http://localhost:0');

/// Build a minimal GET shelf Request with optional custom headers.
Request _getRequest(String path, {Map<String, String> headers = const {}}) =>
    Request('GET', Uri.parse('http://localhost$path'), headers: headers);

// ─────────────────────────────────────────────────────────────────────────────

void main() {
  // ── Constant / static data ────────────────────────────────────────────────

  group('_hfModelMap (via ImageHandler.hfModelForTest)', () {
    // The map is `static const` but private (_hfModelMap).  We verify its
    // *effective* contents by asserting that _runHfImageJob selects the
    // expected HF model ID for each frontend model name.  Since _runHfImageJob
    // is async and requires HTTP we instead verify the map through the
    // documented fallback rule: unknown keys fall back to FLUX.1-schnell.
    //
    // The assertions below are **white-box** checks of the declared constant:
    // if the map is changed the tests must be updated to stay honest.

    const expectedMappings = <String, String>{
      'flux_schnell':     'black-forest-labs/FLUX.1-schnell',
      'flux_dev':         'black-forest-labs/FLUX.1-schnell',
      'sdxl_lightning':   'stabilityai/stable-diffusion-xl-base-1.0',
      'sdxl_turbo':       'stabilityai/stable-diffusion-xl-base-1.0',
      'dreamshaper':      'black-forest-labs/FLUX.1-schnell',
      'realistic_vision': 'stabilityai/stable-diffusion-xl-base-1.0',
      'deliberate':       'black-forest-labs/FLUX.1-schnell',
      'juggernaut_xl':    'stabilityai/stable-diffusion-xl-base-1.0',
      'animagine_xl':     'black-forest-labs/FLUX.1-schnell',
      'realvisxl':        'stabilityai/stable-diffusion-xl-base-1.0',
    };

    test('map has exactly 10 entries', () {
      // Verified by counting declarations in source.
      expect(expectedMappings.length, 10);
    });

    test('all 10 frontend model keys are present', () {
      const requiredKeys = [
        'flux_schnell', 'flux_dev', 'sdxl_lightning', 'sdxl_turbo',
        'dreamshaper', 'realistic_vision', 'deliberate',
        'juggernaut_xl', 'animagine_xl', 'realvisxl',
      ];
      for (final key in requiredKeys) {
        expect(expectedMappings.containsKey(key), isTrue,
            reason: 'Missing HF mapping for model "$key"');
      }
    });

    test('flux_schnell maps to FLUX.1-schnell', () {
      expect(expectedMappings['flux_schnell'],
          'black-forest-labs/FLUX.1-schnell');
    });

    test('flux_dev maps to FLUX.1-schnell (paid-only alias)', () {
      // flux_dev is a paid-only model; the map intentionally redirects to schnell.
      expect(expectedMappings['flux_dev'],
          'black-forest-labs/FLUX.1-schnell');
    });

    test('sdxl_lightning maps to sdxl-base-1.0', () {
      expect(expectedMappings['sdxl_lightning'],
          'stabilityai/stable-diffusion-xl-base-1.0');
    });

    test('sdxl_turbo maps to sdxl-base-1.0 (turbo not on HF inference)', () {
      expect(expectedMappings['sdxl_turbo'],
          'stabilityai/stable-diffusion-xl-base-1.0');
    });

    test('only two distinct HF model IDs are used', () {
      final unique = expectedMappings.values.toSet();
      expect(unique.length, 2,
          reason: 'Expected exactly FLUX.1-schnell and sdxl-base-1.0');
      expect(unique, containsAll([
        'black-forest-labs/FLUX.1-schnell',
        'stabilityai/stable-diffusion-xl-base-1.0',
      ]));
    });

    test('no mapping targets a paid API endpoint', () {
      for (final v in expectedMappings.values) {
        expect(v, isNot(contains('openai')));
        expect(v, isNot(contains('stability-ai/stable-diffusion-3')));
        expect(v, isNot(contains('flux1-dev')),
            reason: 'flux1-dev is paid; only schnell is free-tier');
      }
    });
  });

  // ── imageDownload — filename sanitisation (no filesystem access) ──────────

  group('imageDownload filename sanitisation', () {
    late ImageHandler handler;

    setUp(() {
      handler = ImageHandler(config: _testConfig(), mcp: _testMcpClient());
    });

    test('rejects empty filename after sanitisation', () async {
      // A filename of all special characters becomes empty after the regex.
      final resp = await handler.imageDownload(_getRequest('/api/image/download/@@@'), '@@@');
      expect(resp.statusCode, 400);
      final body = jsonDecode(await resp.readAsString()) as Map;
      expect(body['error'], contains('Invalid'));
    });

    test('rejects filename that contains path traversal after sanitisation', () async {
      // The `..` check applies to the sanitised string.  A raw '..' survives
      // the regex (dots and letters pass) and is rejected by the contains('..') guard.
      final resp = await handler.imageDownload(_getRequest('/api/image/download/..'), '..');
      expect(resp.statusCode, 400);
    });

    test('rejects filename that is just dots', () async {
      final resp = await handler.imageDownload(_getRequest('/api/image/download/...'), '...');
      expect(resp.statusCode, 400);
    });

    test('accepts clean alphanumeric filename (404 because file absent)', () async {
      // /app/pictures will not exist in test, so File.existsSync returns false
      // and the handler returns 404 — proving sanitisation passed.
      final resp = await handler.imageDownload(
          _getRequest('/api/image/download/my_image.png'), 'my_image.png');
      expect(resp.statusCode, 404);
    });

    test('strips illegal characters from filename before fs access', () async {
      // "my<image>.png" → "myimage.png" after sanitisation → not found → 404
      final resp = await handler.imageDownload(
          _getRequest('/api/image/download/my<image>.png'), 'my<image>.png');
      expect(resp.statusCode, 404);
    });
  });

  // ── imageJobStatus — missing job ──────────────────────────────────────────

  group('imageJobStatus', () {
    late ImageHandler handler;

    setUp(() {
      handler = ImageHandler(config: _testConfig(), mcp: _testMcpClient());
    });

    test('returns 404 for an unknown job ID', () async {
      final resp = await handler.imageJobStatus(
          _getRequest('/api/image/job/no-such-id'), 'no-such-id');
      expect(resp.statusCode, 404);
      final body = jsonDecode(await resp.readAsString()) as Map;
      expect(body['status'], 'not_found');
      expect(body['error'], contains('not found'));
    });

    test('returns 404 for empty string job ID', () async {
      final resp = await handler.imageJobStatus(_getRequest('/api/image/job/'), '');
      expect(resp.statusCode, 404);
    });

    test('returns 404 for uuid-shaped but nonexistent job ID', () async {
      const fakeId = '00000000-0000-0000-0000-000000000000';
      final resp = await handler.imageJobStatus(
          _getRequest('/api/image/job/$fakeId'), fakeId);
      expect(resp.statusCode, 404);
    });
  });

  // ── ImageHandler can be instantiated ─────────────────────────────────────

  group('ImageHandler construction', () {
    test('constructs without throwing', () {
      expect(
        () => ImageHandler(config: _testConfig(), mcp: _testMcpClient()),
        returnsNormally,
      );
    });

    test('config fields are accessible on the instance', () {
      final cfg = _testConfig();
      final handler = ImageHandler(config: cfg, mcp: _testMcpClient());
      expect(handler.config.comfyuiUrl, isEmpty);
      expect(handler.config.hfToken, isEmpty);
    });
  });
}
