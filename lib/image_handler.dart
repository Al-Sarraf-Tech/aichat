/// Image generation route handlers — extracted from router.dart.
///
/// Handles the async job system for ComfyUI and HuggingFace Inference API
/// image generation, including img2img, inpainting, ControlNet, and upscaling.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:logging/logging.dart';
import 'package:path/path.dart' as p;
import 'package:shelf/shelf.dart' show Request, Response;
import 'package:uuid/uuid.dart' show Uuid;

import 'config.dart';
import 'mcp_client.dart';
import 'router_helpers.dart' as helpers;

final _log = Logger('ImageHandler');

class ImageHandler {
  final Config config;
  final McpClient mcp;

  ImageHandler({required this.config, required this.mcp});

  // ── Image Generation (Async Job System) ───────────────────────────
  // Jobs are stored in memory. Each job has a status and result.
  final Map<String, Map<String, dynamic>> _imageJobs = {};

  /// Map frontend model names → HuggingFace Inference API model IDs.
  /// Only models verified to work on the free HF inference tier.
  static const _hfModelMap = <String, String>{
    'flux_schnell': 'black-forest-labs/FLUX.1-schnell',
    'flux_dev': 'black-forest-labs/FLUX.1-schnell',           // dev is paid-only, use schnell
    'sdxl_lightning': 'stabilityai/stable-diffusion-xl-base-1.0',
    'sdxl_turbo': 'stabilityai/stable-diffusion-xl-base-1.0', // turbo not on HF inference
    'dreamshaper': 'black-forest-labs/FLUX.1-schnell',
    'realistic_vision': 'stabilityai/stable-diffusion-xl-base-1.0',
    'deliberate': 'black-forest-labs/FLUX.1-schnell',
    'juggernaut_xl': 'stabilityai/stable-diffusion-xl-base-1.0',
    'animagine_xl': 'black-forest-labs/FLUX.1-schnell',
    'realvisxl': 'stabilityai/stable-diffusion-xl-base-1.0',
  };

  Future<Response> imageStatus(Request request) async {
    // Try ComfyUI first
    if (config.comfyuiUrl.isNotEmpty) {
      final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
      try {
        final req = await client.getUrl(Uri.parse('${config.comfyuiUrl}/system_stats'));
        final resp = await req.close().timeout(const Duration(seconds: 5));
        if (resp.statusCode == 200) {
          final body = await resp.transform(utf8.decoder).join();
          final data = jsonDecode(body);
          final devices = (data is Map ? data['devices'] as List? : null) ?? [];
          final gpu = devices.isNotEmpty ? (devices[0]['name'] ?? 'GPU') as String : '';
          // Check if ComfyUI actually has models installed
          bool hasModels = false;
          try {
            final ckptReq = await client.getUrl(Uri.parse('${config.comfyuiUrl}/object_info/CheckpointLoaderSimple'));
            final ckptResp = await ckptReq.close().timeout(const Duration(seconds: 5));
            if (ckptResp.statusCode == 200) {
              final ckptBody = await ckptResp.transform(utf8.decoder).join();
              final ckptData = jsonDecode(ckptBody) as Map<String, dynamic>?;
              final node = ckptData?['CheckpointLoaderSimple'] as Map?;
              final inp = (node?['input'] as Map?)?['required'] as Map?;
              final ckptList = inp?['ckpt_name'];
              if (ckptList is List && ckptList.isNotEmpty && ckptList.first is List && (ckptList.first as List).isNotEmpty) {
                hasModels = true;
              }
            }
            if (!hasModels) {
              final unetReq = await client.getUrl(Uri.parse('${config.comfyuiUrl}/object_info/UNETLoader'));
              final unetResp = await unetReq.close().timeout(const Duration(seconds: 5));
              if (unetResp.statusCode == 200) {
                final unetBody = await unetResp.transform(utf8.decoder).join();
                final unetData = jsonDecode(unetBody) as Map<String, dynamic>?;
                final node = unetData?['UNETLoader'] as Map?;
                final inp = (node?['input'] as Map?)?['required'] as Map?;
                final unetList = inp?['unet_name'];
                if (unetList is List && unetList.isNotEmpty && unetList.first is List && (unetList.first as List).isNotEmpty) {
                  hasModels = true;
                }
              }
            }
          } catch (_) {
            // Model check failed — treat as no models
          }
          if (hasModels) {
            return helpers.jsonResponse({'ok': true, 'gpu': gpu, 'backend': 'comfyui'});
          }
          // ComfyUI reachable but no models — fall through to HF with GPU info
          _log.info('ComfyUI reachable ($gpu) but has no models installed');
          if (config.hfToken.isNotEmpty) {
            return helpers.jsonResponse({
              'ok': true,
              'gpu': 'HuggingFace API \u2014 GPU: $gpu (no models)',
              'backend': 'huggingface',
            });
          }
          return helpers.jsonResponse({'ok': false, 'error': 'ComfyUI ($gpu) has no models installed'});
        }
      } catch (_) {
        // ComfyUI unreachable — fall through to HF check
      } finally {
        client.close();
      }
    }
    // Fallback: HuggingFace Inference API
    if (config.hfToken.isNotEmpty) {
      return helpers.jsonResponse({'ok': true, 'gpu': 'HuggingFace Inference API', 'backend': 'huggingface'});
    }
    return helpers.jsonResponse({'ok': false, 'error': config.comfyuiUrl.isEmpty ? 'No image backend configured' : 'ComfyUI unreachable'});
  }

  /// Query ComfyUI for installed model files — used by frontend to enable/disable buttons.
  Future<Response> imageModels(Request request) async {
    if (config.comfyuiUrl.isEmpty) {
      return helpers.jsonResponse({'checkpoints': [], 'unets': []});
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
      return helpers.jsonResponse({'checkpoints': checkpoints, 'unets': unets});
    } catch (e) {
      return helpers.jsonResponse({'checkpoints': [], 'unets': [], 'error': '$e'});
    } finally {
      client.close();
    }
  }

  Future<Response> imageGenerate(Request request) async {
    final body = await helpers.readJson(request);
    if (body == null) return helpers.jsonResponse({'error': 'Invalid JSON'}, status: 400);
    final prompt = (body['prompt'] as String?)?.trim() ?? '';
    if (prompt.isEmpty) return helpers.jsonResponse({'error': 'prompt is required'}, status: 400);
    final model = (body['model'] as String?) ?? 'flux_schnell';
    final width = helpers.toInt(body['width'], 1024).clamp(64, 4096);
    final height = helpers.toInt(body['height'], 1024).clamp(64, 4096);
    final negPrompt = (body['negative_prompt'] as String?) ?? '';
    final steps = body['steps'] != null ? helpers.toInt(body['steps'], 0) : null;
    final seed = body['seed'] != null ? helpers.toInt(body['seed'], -1) : null;
    final effectiveSeed = seed ?? DateTime.now().millisecondsSinceEpoch % (1 << 32);
    // Img2img parameters
    final referenceImage = body['reference_image'] as String?; // base64 data URI
    final denoise = ((body['denoise'] as num?)?.toDouble() ?? 0.65).clamp(0.05, 1.0);
    final upscaleTo = body['upscale_to'] != null ? helpers.toInt(body['upscale_to'], 2048).clamp(1024, 4096) : null;
    // ComfyUI is the sole image generation backend (cloud backends removed)
    // Batch count
    final count = helpers.toInt(body['count'], 1).clamp(1, 4);
    // Inpainting mask
    final mask = body['mask'] as String?;
    // ControlNet
    final controlnetType = body['controlnet_type'] as String?;
    final controlnetImage = body['controlnet_image'] as String?;
    final controlnetStrength = ((body['controlnet_strength'] as num?)?.toDouble() ?? 0.8).clamp(0.1, 1.0);

    // Create job with UUID and user binding
    final userId = helpers.getUserId(request);
    final jobId = const Uuid().v4();
    _imageJobs[jobId] = {
      'status': 'submitted',
      'model': model,
      'seed': effectiveSeed,
      'prompt': prompt,
      'user_id': userId,
    };
    _log.info('Image job $jobId: model=$model ${width}x$height count=$count${referenceImage != null ? " img2img" : ""}${mask != null ? " inpaint" : ""}${upscaleTo != null ? " upscale→$upscaleTo" : ""}');

    // Require at least one image backend (ComfyUI or HuggingFace)
    if (config.comfyuiUrl.isEmpty && config.hfToken.isEmpty) {
      return helpers.jsonResponse({'error': 'No image backend configured (set COMFYUI_URL or HF_TOKEN)'}, status: 503);
    }
    {
      _runBatchImageJob(jobId, count: count,
          model: model, prompt: prompt, negPrompt: negPrompt,
          width: width, height: height, steps: steps, baseSeed: effectiveSeed,
          referenceImage: referenceImage, denoise: denoise, upscaleTo: upscaleTo,
          mask: mask, controlnetType: controlnetType, controlnetImage: controlnetImage,
          controlnetStrength: controlnetStrength);
    }

    // Return instantly — client polls /api/image/job/<jobId>
    return helpers.jsonResponse({'jobId': jobId, 'status': 'submitted'});
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

  /// Batch wrapper: runs [count] sequential jobs and aggregates all
  /// images into the parent job. Each iteration uses baseSeed + i.
  /// Auto-detects backend: tries ComfyUI first, falls back to HuggingFace.
  Future<void> _runBatchImageJob(String jobId, {
    required int count,
    required String model, required String prompt, required String negPrompt,
    required int width, required int height, int? steps, required int baseSeed,
    String? referenceImage, double denoise = 1.0, int? upscaleTo,
    String? mask, String? controlnetType, String? controlnetImage,
    double controlnetStrength = 0.8,
  }) async {
    // Determine backend: try ComfyUI health check, fall back to HF
    bool useHf = config.comfyuiUrl.isEmpty;
    if (!useHf) {
      final probe = HttpClient()..connectionTimeout = const Duration(seconds: 3);
      try {
        final req = await probe.getUrl(Uri.parse('${config.comfyuiUrl}/system_stats'));
        final resp = await req.close().timeout(const Duration(seconds: 3));
        if (resp.statusCode != 200) useHf = true;
      } catch (_) {
        useHf = true;
      } finally {
        probe.close();
      }
    }
    if (useHf && config.hfToken.isEmpty) {
      _failJob(jobId, 'ComfyUI unreachable and no HF_TOKEN configured');
      return;
    }
    // HF only supports text-to-image (no img2img, inpaint, or ControlNet)
    if (useHf && (referenceImage != null || mask != null || controlnetImage != null)) {
      _failJob(jobId, 'Img2img, inpainting, and ControlNet require ComfyUI (currently unreachable)');
      return;
    }
    if (useHf) {
      _log.info('Job $jobId: ComfyUI unavailable, using HuggingFace Inference API');
    }

    final allImages = <Map<String, dynamic>>[];
    final userId = _imageJobs[jobId]?['user_id'];
    for (var i = 0; i < count; i++) {
      final childId = '${jobId}__batch_$i';
      _imageJobs[childId] = {'status': 'submitted', 'user_id': userId};
      if (useHf) {
        await _runHfImageJob(childId,
            model: model, prompt: prompt, negPrompt: negPrompt,
            width: width, height: height, steps: steps, seed: baseSeed + i);
      } else {
        await _runImageJob(childId,
            model: model, prompt: prompt, negPrompt: negPrompt,
            width: width, height: height, steps: steps, seed: baseSeed + i,
            referenceImage: referenceImage, denoise: denoise, upscaleTo: upscaleTo,
            mask: mask, controlnetType: controlnetType, controlnetImage: controlnetImage,
            controlnetStrength: controlnetStrength);
      }
      final child = _imageJobs[childId];
      if (child != null && child['status'] == 'done') {
        final imgs = child['images'];
        if (imgs is List) allImages.addAll(imgs.cast<Map<String, dynamic>>());
      } else {
        _log.warning('Batch child $childId failed: ${child?['error'] ?? child?['status'] ?? 'unknown'}');
      }
      _imageJobs.remove(childId);
    }
    // If ComfyUI batch failed and HF is available, retry with HF
    if (allImages.isEmpty && !useHf && config.hfToken.isNotEmpty &&
        referenceImage == null && mask == null && controlnetImage == null) {
      _log.info('Job $jobId: ComfyUI batch failed, retrying with HuggingFace');
      for (var i = 0; i < count; i++) {
        final childId = '${jobId}__hf_$i';
        _imageJobs[childId] = {'status': 'submitted', 'user_id': userId};
        await _runHfImageJob(childId,
            model: model, prompt: prompt, negPrompt: negPrompt,
            width: width, height: height, steps: steps, seed: baseSeed + i);
        final child = _imageJobs[childId];
        if (child != null && child['status'] == 'done') {
          final imgs = child['images'];
          if (imgs is List) allImages.addAll(imgs.cast<Map<String, dynamic>>());
        } else {
          _log.warning('HF child $childId failed: ${child?['error'] ?? 'unknown'}');
        }
        _imageJobs.remove(childId);
      }
    }
    if (allImages.isEmpty) {
      _failJob(jobId, 'All batch items failed');
    } else {
      _imageJobs[jobId] = {
        'status': 'done',
        'images': allImages,
        'model': model,
        'seed': baseSeed,
        if (userId != null) 'user_id': userId,
      };
      _log.info('Batch job $jobId: complete, ${allImages.length} images from $count runs');
    }
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
        _log.warning('Job $jobId: ComfyUI rejected workflow (${submitResp.statusCode}): $submitBody');
        _failJob(jobId, 'ComfyUI rejected workflow: $submitBody');
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
        // Save to /app/pictures/{userId}/ for ownership scoping
        String? savedAs;
        try {
          final jobUserId = _imageJobs[jobId]?['user_id'] as String? ?? 'shared';
          final picDir = Directory('/app/pictures/$jobUserId');
          if (!picDir.existsSync()) picDir.createSync(recursive: true);
          final ts = DateTime.now().toIso8601String().replaceAll(RegExp(r'[:\-T]'), '').substring(0, 15);
          final safePrompt = prompt.length > 30 ? prompt.substring(0, 30) : prompt;
          final cleanPrompt = safePrompt.replaceAll(RegExp(r'[^a-zA-Z0-9 ]'), '').trim().replaceAll(' ', '_');
          savedAs = '${model}_${ts}_$cleanPrompt.jpg';
          File('${picDir.path}/$savedAs').writeAsBytesSync(imgBytes);
          _log.info('Job $jobId: saved /app/pictures/$jobUserId/$savedAs');
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

  /// HuggingFace Inference API image generation — fallback when ComfyUI is down.
  Future<void> _runHfImageJob(String jobId, {
    required String model, required String prompt, required String negPrompt,
    required int width, required int height, int? steps, required int seed,
  }) async {
    _imageJobs[jobId]!['status'] = 'generating';
    final hfModel = _hfModelMap[model] ?? 'black-forest-labs/FLUX.1-schnell';
    final url = Uri.parse('https://router.huggingface.co/hf-inference/models/$hfModel');
    final client = HttpClient();
    try {
      // Build HF Inference API payload
      final params = <String, dynamic>{
        'width': width.clamp(256, 1024),
        'height': height.clamp(256, 1024),
        'seed': seed,
      };
      if (negPrompt.isNotEmpty) params['negative_prompt'] = negPrompt;
      if (steps != null && steps > 0) params['num_inference_steps'] = steps;

      // Retry loop for cold model loading (HF returns 503 while loading)
      Uint8List? imgBytes;
      for (var attempt = 0; attempt < 5; attempt++) {
        final req = await client.postUrl(url);
        req.headers.set('Authorization', 'Bearer ${config.hfToken}');
        req.headers.contentType = ContentType.json;
        req.write(jsonEncode({'inputs': prompt, 'parameters': params}));
        final resp = await req.close().timeout(const Duration(seconds: 120));

        if (resp.statusCode == 200) {
          final builder = BytesBuilder(copy: false);
          await resp.forEach(builder.add);
          imgBytes = builder.takeBytes();
          break;
        }
        if (resp.statusCode == 503) {
          // Model is loading — parse estimated_time and wait
          final body = await resp.transform(utf8.decoder).join();
          _log.info('Job $jobId: HF model loading (attempt ${attempt + 1}/5): $body');
          _imageJobs[jobId]!['status'] = 'loading model';
          int wait = 20;
          try {
            final data = jsonDecode(body);
            if (data is Map && data['estimated_time'] is num) {
              wait = (data['estimated_time'] as num).ceil().clamp(5, 60);
            }
          } catch (_) {}
          await Future.delayed(Duration(seconds: wait));
          continue;
        }
        // Other errors — fail immediately
        final errBody = await resp.transform(utf8.decoder).join();
        _failJob(jobId, 'HuggingFace API error ${resp.statusCode}: $errBody');
        return;
      }
      if (imgBytes == null || imgBytes.isEmpty) {
        _failJob(jobId, 'HuggingFace API: model failed to load after 5 attempts');
        return;
      }

      // Save image to disk
      final userId = _imageJobs[jobId]?['user_id'] as String? ?? 'shared';
      final picDir = Directory('/app/pictures/$userId');
      if (!picDir.existsSync()) picDir.createSync(recursive: true);
      final ts = DateTime.now().toIso8601String()
          .replaceAll(RegExp(r'[:\-T]'), '').substring(0, 15);
      final safePrompt = prompt.length > 30 ? prompt.substring(0, 30) : prompt;
      final cleanPrompt = safePrompt
          .replaceAll(RegExp(r'[^a-zA-Z0-9 ]'), '').trim().replaceAll(' ', '_');
      final savedAs = '${model}_hf_${ts}_$cleanPrompt.png';
      File('${picDir.path}/$savedAs').writeAsBytesSync(imgBytes);
      _log.info('Job $jobId: HF saved /app/pictures/$userId/$savedAs (${imgBytes.length} bytes)');

      _imageJobs[jobId] = {
        'status': 'done',
        'images': [{'savedAs': savedAs, 'url': '/api/image/download/$savedAs'}],
        'model': model,
        'seed': seed,
        'user_id': userId,
      };
    } catch (e) {
      _log.severe('Job $jobId HF error: $e');
      _failJob(jobId, 'HuggingFace: $e');
    } finally {
      client.close();
    }
  }

  Future<Response> imageJobStatus(Request request, String jobId) async {
    final job = _imageJobs[jobId];
    if (job == null) {
      return helpers.jsonResponse({'status': 'not_found', 'error': 'Job not found'}, status: 404);
    }
    // Enforce user ownership
    final userId = helpers.getUserId(request);
    if (userId.isNotEmpty && job['user_id'] != userId) {
      return helpers.jsonResponse({'status': 'not_found', 'error': 'Job not found'}, status: 404);
    }
    return helpers.jsonResponse(job);
  }

  Future<Response> imageDownload(Request request, String filename) async {
    final userId = helpers.getUserId(request);
    // Sanitize filename to prevent path traversal
    final safe = filename.replaceAll(RegExp(r'[^a-zA-Z0-9_.\-]'), '');
    if (safe.isEmpty || safe.contains('..')) {
      return helpers.jsonResponse({'error': 'Invalid filename'}, status: 400);
    }
    final picDir = '/app/pictures';
    // User-scoped: check user's own dir first, then shared, then legacy root
    // Only the user's own files and shared/legacy are accessible
    final userFile = userId.isNotEmpty ? File('$picDir/$userId/$safe') : null;
    final sharedFile = File('$picDir/shared/$safe');
    final legacyFile = File('$picDir/$safe');
    final file = (userFile != null && userFile.existsSync()) ? userFile
        : sharedFile.existsSync() ? sharedFile
        : legacyFile.existsSync() ? legacyFile
        : null;
    if (file == null) {
      return helpers.jsonResponse({'error': 'File not found'}, status: 404);
    }
    // Verify file is within pictures directory (use p.isWithin for safe prefix check)
    try {
      final resolved = file.resolveSymbolicLinksSync();
      final resolvedDir = p.canonicalize(picDir);
      if (!p.isWithin(resolvedDir, resolved) && resolved != resolvedDir) {
        return Response.forbidden('Forbidden');
      }
    } catch (e) {
      return helpers.jsonResponse({'error': 'File access error'}, status: 500);
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
      // Specialized SDXL models (single checkpoint, 1024px native)
      'juggernaut_xl':    {'ckpt': 'juggernaut_xl_v9.safetensors',     'steps': 30, 'cfg': 4.5, 'type': 'sd15'},
      'animagine_xl':     {'ckpt': 'animagine_xl_31.safetensors',      'steps': 28, 'cfg': 5.0, 'type': 'sd15'},
      'realvisxl':        {'ckpt': 'realvisxl_v5.safetensors',         'steps': 25, 'cfg': 4.0, 'type': 'sd15'},
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
      'sdxl_lightning': {'ckpt': 'sd_xl_base_1.0.safetensors', 'unet': 'sdxl_lightning_4step.safetensors', 'steps': 4, 'cfg': 1.5, 'type': 'sdxl_lightning'},
      'sdxl_turbo':   {'ckpt': 'sdxl_turbo.safetensors', 'steps': 1, 'cfg': 1.0, 'type': 'sdxl'},
      'dreamshaper':      {'ckpt': 'dreamshaper_8.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sd15'},
      'realistic_vision': {'ckpt': 'realistic_vision_v5.safetensors',  'steps': 30, 'cfg': 7.0, 'type': 'sd15'},
      'deliberate':       {'ckpt': 'deliberate_v3.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sd15'},
      'juggernaut_xl':    {'ckpt': 'juggernaut_xl_v9.safetensors',     'steps': 30, 'cfg': 4.5, 'type': 'sd15'},
      'animagine_xl':     {'ckpt': 'animagine_xl_31.safetensors',      'steps': 28, 'cfg': 5.0, 'type': 'sd15'},
      'realvisxl':        {'ckpt': 'realvisxl_v5.safetensors',         'steps': 25, 'cfg': 4.0, 'type': 'sd15'},
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
    } else if (type == 'sdxl_lightning') {
      // SDXL Lightning img2img: CheckpointLoader + UNETLoader → ModelMerge → KSampler
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'sgm_uniform',
          'denoise': denoise,
          'model': ['4c', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['12', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '4b': {'class_type': 'UNETLoader', 'inputs': {'unet_name': cfg['unet'], 'weight_dtype': 'default'}},
        '4c': {'class_type': 'ModelMergeSimple', 'inputs': {'model1': ['4', 0], 'model2': ['4b', 0], 'ratio': 1.0}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_img2img_$model', 'images': ['8', 0]}},
        '13': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '12': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['13', 0], 'vae': ['4', 2]}},
      };
    } else {
      // SDXL/SD1.5 img2img: LoadImage → VAEEncode → KSampler(denoise<1) → VAEDecode → Save
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c,
          'sampler_name': type == 'sd15' ? 'euler_ancestral' : 'euler',
          'scheduler': 'normal',
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
      'sdxl_lightning': {'ckpt': 'sd_xl_base_1.0.safetensors', 'unet': 'sdxl_lightning_4step.safetensors', 'steps': 4, 'cfg': 1.5, 'type': 'sdxl_lightning'},
      'sdxl_turbo':   {'ckpt': 'sdxl_turbo.safetensors', 'steps': 1, 'cfg': 1.0, 'type': 'sdxl'},
      'dreamshaper':      {'ckpt': 'dreamshaper_8.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sd15'},
      'realistic_vision': {'ckpt': 'realistic_vision_v5.safetensors',  'steps': 30, 'cfg': 7.0, 'type': 'sd15'},
      'deliberate':       {'ckpt': 'deliberate_v3.safetensors',        'steps': 25, 'cfg': 7.0, 'type': 'sd15'},
      'juggernaut_xl':    {'ckpt': 'juggernaut_xl_v9.safetensors',     'steps': 30, 'cfg': 4.5, 'type': 'sd15'},
      'animagine_xl':     {'ckpt': 'animagine_xl_31.safetensors',      'steps': 28, 'cfg': 5.0, 'type': 'sd15'},
      'realvisxl':        {'ckpt': 'realvisxl_v5.safetensors',         'steps': 25, 'cfg': 4.0, 'type': 'sd15'},
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
    } else if (type == 'sdxl_lightning') {
      // SDXL Lightning inpaint: CheckpointLoader + UNETLoader → ModelMerge → KSampler
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler', 'scheduler': 'sgm_uniform',
          'denoise': denoise,
          'model': ['4c', 0], 'positive': ['6', 0], 'negative': ['7', 0], 'latent_image': ['15', 0]}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': cfg['ckpt']}},
        '4b': {'class_type': 'UNETLoader', 'inputs': {'unet_name': cfg['unet'], 'weight_dtype': 'default'}},
        '4c': {'class_type': 'ModelMergeSimple', 'inputs': {'model1': ['4', 0], 'model2': ['4b', 0], 'ratio': 1.0}},
        '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['4', 1]}},
        '7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': negPrompt, 'clip': ['4', 1]}},
        '8': {'class_type': 'VAEDecode', 'inputs': {'samples': ['3', 0], 'vae': ['4', 2]}},
        '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'aichat_inpaint_$model', 'images': ['8', 0]}},
        '13': {'class_type': 'LoadImage', 'inputs': {'image': refFilename}},
        '12': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['13', 0], 'vae': ['4', 2]}},
        '14': {'class_type': 'LoadImage', 'inputs': {'image': maskFilename}},
        '15': {'class_type': 'SetLatentNoiseMask', 'inputs': {'samples': ['12', 0], 'mask': ['14', 1]}},
      };
    } else {
      return {
        '3': {'class_type': 'KSampler', 'inputs': {
          'seed': rng, 'steps': s, 'cfg': c,
          'sampler_name': type == 'sd15' ? 'euler_ancestral' : 'euler',
          'scheduler': 'normal',
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
    // ControlNet model filenames — SD 1.5 ControlNet v1.1 weights
    const controlModels = {
      'openpose': 'control_v11p_sd15_openpose.pth',
      'depth':    'control_v11f1p_sd15_depth.pth',
      'canny':    'control_v11p_sd15_canny.pth',
    };
    // SD 1.5 ControlNet requires SD 1.5 checkpoint (not SDXL/FLUX).
    // Use DreamShaper v8 as the SD 1.5 base for all ControlNet workflows.
    const ckpt = 'dreamshaper_8.safetensors';
    final s = steps ?? 25;
    const c = 7.0;
    final cnModel = controlModels[controlType] ?? controlModels['openpose']!;
    // SD 1.5 native: 512x512, supports up to 768x768
    final cnW = width > 768 ? 512 : width;
    final cnH = height > 768 ? 512 : height;

    // ControlNet pipeline:
    // LoadImage(control) → ControlNetLoader → ControlNetApply(positive conditioning + control image)
    // → KSampler(with modified conditioning) → VAEDecode → SaveImage
    // If refFilename provided: img2img + controlnet (VAEEncode ref → latent)
    final latentNode = refFilename != null
        ? {'class_type': 'VAEEncode', 'inputs': {'pixels': ['20', 0], 'vae': ['4', 2]}}
        : {'class_type': 'EmptyLatentImage', 'inputs': {'width': cnW, 'height': cnH, 'batch_size': 1}};

    return {
      '3': {'class_type': 'KSampler', 'inputs': {
        'seed': rng, 'steps': s, 'cfg': c, 'sampler_name': 'euler_ancestral', 'scheduler': 'normal',
        'denoise': refFilename != null ? denoise : 1.0,
        // ControlNetApplyAdvanced outputs: [0]=positive, [1]=negative
        'model': ['4', 0], 'positive': ['17', 0], 'negative': ['17', 1], 'latent_image': ['5', 0]}},
      '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': ckpt}},
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

  Future<Response> imageSearchReference(Request request) async {
    final body = await helpers.readJson(request);
    final query = (body?['query'] as String?)?.trim() ?? '';
    if (query.isEmpty) return helpers.jsonResponse({'error': 'query is required'}, status: 400);
    final limit = helpers.toInt(body?['limit'], 8);

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
      return helpers.jsonResponse({'urls': urls.take(limit).toList()});
    } catch (e) {
      return helpers.jsonResponse({'error': 'Search failed: $e', 'urls': []});
    }
  }
}
