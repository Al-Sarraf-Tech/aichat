import 'package:test/test.dart';
import '../../lib/sanitizer.dart';

void main() {
  group('sanitizeToolResult', () {
    test('short text passes through unchanged', () {
      expect(sanitizeToolResult('hello world'), 'hello world');
    });

    test('strips base64 data blocks', () {
      // Input must be > 200 chars to trigger sanitization
      final input = 'prefix ${'A' * 150} suffix' + ' ' * 100;
      final result = sanitizeToolResult(input);
      expect(result, contains('[binary data removed]'));
      expect(result, isNot(contains('A' * 150)));
    });

    test('strips data: URIs', () {
      // The base64 regex fires first, stripping the payload.
      // The result contains [binary data removed] where the base64 was.
      final input = 'image: data:image/png;base64,${'A' * 100} done' + ' ' * 100;
      final result = sanitizeToolResult(input);
      expect(result, contains('[binary data removed]'));
      expect(result, isNot(contains('A' * 100)));
    });

    test('strips raw byte strings', () {
      final input = "result: b'${'x' * 60}' end" + ' ' * 200;
      final result = sanitizeToolResult(input);
      expect(result, contains('[binary data removed]'));
    });

    test('strips hex dumps', () {
      final hex = List.generate(15, (_) => r'\x4f').join();
      final input = 'data: $hex end' + ' ' * 200;
      final result = sanitizeToolResult(input);
      expect(result, contains('[hex data removed]'));
    });

    test('truncates to 2000 chars', () {
      // Use chars that won't match any stripping regex
      final input = List.generate(5000, (i) => 'word${i % 100} ').join();
      final result = sanitizeToolResult(input);
      expect(result.length, lessThan(2100));
      expect(result, contains('truncated'));
    });

    test('preserves normal text under 200 chars', () {
      const input = 'The weather in San Francisco is 65F and sunny.';
      expect(sanitizeToolResult(input), input);
    });
  });

  group('extractImageUrls', () {
    test('extracts valid image URLs', () {
      const text = 'Here is https://example.com/photos/sunset.jpg in the article';
      final urls = extractImageUrls(text);
      expect(urls, ['https://example.com/photos/sunset.jpg']);
    });

    test('filters junk images', () {
      const text = 'https://example.com/logo/brand.png and https://example.com/photos/sunset.jpg';
      final urls = extractImageUrls(text);
      expect(urls, ['https://example.com/photos/sunset.jpg']);
    });

    test('caps at 6 URLs', () {
      final text = List.generate(10, (i) => 'https://example.com/photo$i/image.jpg').join(' ');
      final urls = extractImageUrls(text);
      expect(urls.length, 6);
    });

    test('handles empty text', () {
      expect(extractImageUrls(''), isEmpty);
    });

    test('handles text with no image URLs', () {
      expect(extractImageUrls('just plain text here'), isEmpty);
    });
  });

  group('isJunkImage', () {
    test('rejects logo URLs', () {
      expect(isJunkImage('https://example.com/logo/brand.png'), true);
    });

    test('rejects favicon', () {
      expect(isJunkImage('https://example.com/favicon/icon.png'), true);
    });

    test('rejects SVGs', () {
      expect(isJunkImage('https://example.com/assets/arrow.svg'), true);
    });

    test('rejects gravatar', () {
      expect(isJunkImage('https://gravatar.com/avatar/abc123.jpg'), true);
    });

    test('accepts normal-length image filenames', () {
      // Filenames with standard extensions are >= 4 chars (e.g. "a.jpg" = 5)
      // so the < 4 check mostly catches extensionless paths
      expect(isJunkImage('https://example.com/photo.jpg'), false);
      expect(isJunkImage('https://example.com/image-large.png'), false);
    });

    test('accepts normal photo URLs', () {
      expect(isJunkImage('https://example.com/photos/sunset-beach.jpg'), false);
    });

    test('accepts long meaningful filenames', () {
      expect(isJunkImage('https://cdn.example.com/article-hero-image.webp'), false);
    });
  });

  group('inferToolArgs', () {
    test('web defaults to search', () {
      final args = inferToolArgs('web', 'latest news');
      expect(args['action'], 'search');
      expect(args['query'], 'latest news');
    });

    test('browser defaults to navigate', () {
      final args = inferToolArgs('browser', 'https://example.com');
      expect(args['action'], 'navigate');
      expect(args['url'], 'https://example.com');
    });

    test('image defaults to search with count', () {
      final args = inferToolArgs('image', 'cats');
      expect(args['action'], 'search');
      expect(args['count'], '6');
    });

    test('code defaults to python', () {
      final args = inferToolArgs('code', 'print("hello")');
      expect(args['action'], 'python');
      expect(args['code'], 'print("hello")');
    });

    test('unknown tool defaults to search', () {
      final args = inferToolArgs('unknown_tool', 'query');
      expect(args['action'], 'search');
    });
  });
}
