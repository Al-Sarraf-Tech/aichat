/// Shared HTTP helpers used by all route handlers.
///
/// Extracted from router.dart to allow handler modules to produce
/// JSON responses and parse request bodies without depending on
/// the main AppRouter class.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:shelf/shelf.dart' show Request, Response;

/// The CORS origin, read once from CORS_ORIGIN env var.
final String corsOrigin =
    Platform.environment['CORS_ORIGIN'] ?? 'http://localhost:8200';

/// Standard CORS headers applied to all API responses.
final Map<String, String> corsHeaders = {
  'Access-Control-Allow-Origin': corsOrigin,
  'Access-Control-Allow-Methods': 'GET, POST, PATCH, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
};

/// Encode [data] as a JSON Response with the given HTTP [status].
Response jsonResponse(Map<String, dynamic> data, {int status = 200}) {
  return Response(
    status,
    body: jsonEncode(data),
    headers: {'Content-Type': 'application/json'},
  );
}

/// Parse the request body as a JSON Map. Returns null on failure.
Future<Map<String, dynamic>?> readJson(Request request) async {
  try {
    final body = await request.readAsString();
    if (body.isEmpty) return {};
    return jsonDecode(body) as Map<String, dynamic>;
  } catch (_) {
    return null;
  }
}

/// Extract authenticated user ID from X-Auth-User header (set by auth proxy).
String getUserId(Request request) {
  return request.headers['x-auth-user'] ?? '';
}

/// Parse a value to int, handling both int and String inputs from JSON.
int toInt(dynamic value, int fallback) {
  if (value is int) return value;
  if (value is double) return value.toInt();
  if (value is String) return int.tryParse(value) ?? fallback;
  return fallback;
}

/// Send an SSE comment to keep the connection alive through proxies.
void sseKeepalive(StreamController<List<int>> controller) {
  if (controller.isClosed) return;
  controller.add(utf8.encode(':keepalive\n\n'));
}

/// Run an async operation while sending SSE keepalives every 15 seconds.
/// Prevents proxy timeouts (524) during long tool calls.
Future<T> withKeepalive<T>(
  StreamController<List<int>> controller,
  Future<T> Function() work,
) async {
  final timer = Timer.periodic(
    const Duration(seconds: 15),
    (_) => sseKeepalive(controller),
  );
  try {
    return await work();
  } finally {
    timer.cancel();
  }
}

/// Encode and write a named SSE event to the stream controller.
void sseEvent(
  StreamController<List<int>> controller,
  String event,
  Map<String, dynamic> data,
) {
  if (controller.isClosed) return;
  final payload = 'event: $event\ndata: ${jsonEncode(data)}\n\n';
  controller.add(utf8.encode(payload));
}
