'use strict';
import { prepareWithSegments, layout, walkLineRanges } from './vendor/pretext/layout.js';

// ── Config ──────────────────────────────────────────────────────
const FONT = '15px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
const LINE_HEIGHT = 22;
const BUBBLE_PADDING_H = 16;
const CACHE_MAX = 300;

// ── Cache ───────────────────────────────────────────────────────
const _preparedCache = new Map();

function getPrepared(text) {
  if (!text) return null;
  const key = text.length > 120 ? text.substring(0, 120) : text;
  let p = _preparedCache.get(key);
  if (!p) {
    p = prepareWithSegments(text, FONT);
    if (_preparedCache.size >= CACHE_MAX) {
      _preparedCache.delete(_preparedCache.keys().next().value);
    }
    _preparedCache.set(key, p);
  }
  return p;
}

// ── Public API ──────────────────────────────────────────────────

/**
 * Compute the tightest bubble width that doesn't add extra line breaks.
 * Returns pixel width (content area, excluding padding).
 *
 * Uses binary search: find the narrowest width where lineCount stays the same
 * as at maxWidth. This eliminates wasted whitespace on short messages.
 */
export function tightenBubble(text, maxWidth) {
  if (!text || maxWidth <= 0) return maxWidth;
  const prepared = getPrepared(text);
  if (!prepared) return maxWidth;

  const contentMax = maxWidth - BUBBLE_PADDING_H * 2;
  if (contentMax <= 0) return maxWidth;

  const baseline = layout(prepared, contentMax, LINE_HEIGHT);
  if (baseline.lineCount <= 1) {
    // Single line — tighten to actual text width
    let maxLineW = 0;
    walkLineRanges(prepared, contentMax, line => {
      if (line.width > maxLineW) maxLineW = line.width;
    });
    return Math.ceil(maxLineW) + BUBBLE_PADDING_H * 2 + 2; // +2 for rounding safety
  }

  // Multi-line: binary search for the narrowest width with the same line count
  let lo = 1;
  let hi = Math.ceil(contentMax);
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    const midLines = layout(prepared, mid, LINE_HEIGHT).lineCount;
    if (midLines <= baseline.lineCount) {
      hi = mid;
    } else {
      lo = mid + 1;
    }
  }

  // Get actual max line width at the tight width for precise sizing
  let tightMaxW = 0;
  walkLineRanges(prepared, lo, line => {
    if (line.width > tightMaxW) tightMaxW = line.width;
  });

  return Math.ceil(tightMaxW) + BUBBLE_PADDING_H * 2 + 2;
}

/**
 * Estimate the rendered height of text at a given width.
 * Useful for virtual scrolling / CLS prevention.
 */
export function estimateHeight(text, maxWidth) {
  if (!text || maxWidth <= 0) return 0;
  const prepared = getPrepared(text);
  if (!prepared) return 0;
  return layout(prepared, maxWidth - BUBBLE_PADDING_H * 2, LINE_HEIGHT).height;
}
