'use strict';

// ── Shared Utilities ─────────────────────────────────────────────

export function esc(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ── Markdown Cache ──────────────────────────────────────────────
const _mdCache = new Map();
const MD_CACHE_MAX = 200;

export function renderMd(text) {
  if (!text) return '';

  const cached = _mdCache.get(text);
  if (cached !== undefined) return cached;

  try {
    const html = typeof marked !== 'undefined'
      ? marked.parse(text)
      : text.replace(/</g, '&lt;').replace(/\n/g, '<br>');

    const safe = typeof DOMPurify !== 'undefined'
      ? DOMPurify.sanitize(html, {
          ADD_TAGS: ['img'],
          ADD_ATTR: ['src', 'alt', 'class', 'loading', 'decoding'],
        })
      : html.replace(/</g, '&lt;').replace(/>/g, '&gt;');

    if (_mdCache.size >= MD_CACHE_MAX) {
      _mdCache.delete(_mdCache.keys().next().value);
    }

    _mdCache.set(text, safe);
    return safe;
  } catch (err) {
    console.error('renderMd:', err);
    return esc(text);
  }
}

export function clearMdCache() {
  _mdCache.clear();
}

// ── RAF-batched scroll ──────────────────────────────────────────
let _scrollPending = false;

export function scrollToBottom(opts) {
  const chatView = document.getElementById('chat-view');
  if (!chatView) return;

  if (opts && opts.immediate) {
    chatView.scrollTop = chatView.scrollHeight;
    return;
  }

  if (_scrollPending) return;
  _scrollPending = true;

  requestAnimationFrame(() => {
    _scrollPending = false;
    chatView.scrollTop = chatView.scrollHeight;
  });
}

export function formatCtx(num) {
  if (!num) return '';
  if (num >= 1048576) return (num / 1048576).toFixed(0) + 'M';
  if (num >= 1024) return (num / 1024).toFixed(0) + 'K';
  return String(num);
}

export function shortModel(id) {
  if (!id) return '';
  let label = id
    .replace(/-instruct$/i, '')
    .replace(/-chat$/i, '');
  return label.length > 14 ? label.substring(0, 14) + '\u2026' : label;
}

export function isMobile() {
  return window.matchMedia('(max-width: 768px)').matches;
}

// Strip raw data artifacts the LLM may echo from tool results
export function cleanResponse(text, stripMarkdownImages) {
  if (!text) return '';

  let cleaned = text;
  cleaned = cleaned.replace(/data:[a-z/+]+;base64,[A-Za-z0-9+/=]+/gi, '');
  cleaned = cleaned.replace(/;base64,[A-Za-z0-9+/=]{100,}/g, ';base64,[data removed]');
  cleaned = cleaned.replace(/b'[^']{50,}'/g, '');
  cleaned = cleaned.replace(/(\\x[0-9a-fA-F]{2}){10,}/g, '');

  if (stripMarkdownImages) {
    cleaned = cleaned.replace(/!\[[^\]]*\]\([^)]+\)/g, '');
  }

  cleaned = cleaned.replace(/\n{3,}/g, '\n\n').trim();
  return cleaned;
}

export function normalizeImageUrl(url) {
  if (!url) return '';
  let normalized = url.split('?')[0].toLowerCase();
  normalized = normalized.replace(/_\d+x\d+/g, '');
  normalized = normalized.replace(/-\d+x\d+/g, '');
  normalized = normalized.replace(/_thumb|_small|_medium|_large|_preview/g, '');
  return normalized;
}

// ── Auth-aware fetch ─────────────────────────────────────────────
export function authFetch(url, opts = {}) {
  const token = localStorage.getItem('dartboard-jwt');

  if (!token || token.length < 10) {
    import('./auth.js').then(module => module.showAuthScreen());
    return Promise.reject(new Error('Not authenticated'));
  }

  if (!opts.headers) opts.headers = {};
  opts.headers['Authorization'] = 'Bearer ' + token;

  return fetch(url, opts).then(res => {
    if (res.status === 401 && url.startsWith('/api/')) {
      const currentToken = localStorage.getItem('dartboard-jwt');
      if (currentToken === token) {
        localStorage.removeItem('dartboard-jwt');
        import('./auth.js').then(module => module.showAuthScreen());
      }
      throw new Error('Session expired');
    }
    return res;
  });
}

// ── Time formatting ──────────────────────────────────────────────
export function timeAgo(dateStr) {
  const date = new Date(dateStr);
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);

  if (seconds < 60)     return 'just now';
  if (seconds < 3600)   return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400)  return Math.floor(seconds / 3600) + 'h ago';
  if (seconds < 604800) return Math.floor(seconds / 86400) + 'd ago';

  return date.toLocaleDateString();
}
