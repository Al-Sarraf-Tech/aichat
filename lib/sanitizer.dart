/// Pure utility functions for cleaning tool results and extracting image URLs.
///
/// These are stateless — no class dependencies, no side effects.
/// Extracted from router.dart to reduce the god-object.
library;

/// Clean tool results before feeding back to LLM to prevent
/// raw data (base64, JSON dumps, binary) from leaking into responses.
String sanitizeToolResult(String text) {
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
List<String> extractImageUrls(String text) {
  final urls = <String>[];
  final pattern = RegExp(
    r'https?://[^\s"<>]+\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s"<>]*)?',
    caseSensitive: false,
  );
  for (final match in pattern.allMatches(text)) {
    final url = match.group(0)!;
    if (isJunkImage(url)) continue;
    urls.add(url);
    if (urls.length >= 6) break;
  }
  return urls;
}

/// True if [url] looks like a logo, favicon, placeholder, or tracking pixel.
/// Uses path-segment matching to avoid false positives from substring hits.
bool isJunkImage(String url) {
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

/// Infer reasonable default arguments for a mega-tool when the LLM
/// produced empty or incomplete arguments.
Map<String, dynamic> inferToolArgs(String toolName, String userText) {
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
