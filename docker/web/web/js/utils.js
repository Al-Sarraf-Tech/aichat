'use strict';

// ── Shared Utilities ─────────────────────────────────────────────

export function esc(t) {
  if (!t) return '';
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

export function renderMd(t) {
  if (!t) return '';
  try {
    const html = typeof marked !== 'undefined' ? marked.parse(t) : t.replace(/</g, '&lt;').replace(/\n/g, '<br>');
    return typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html, { ADD_TAGS: ['img'], ADD_ATTR: ['src', 'alt', 'class', 'loading', 'decoding'] }) : html;
  } catch (e) { console.error('renderMd:', e); return esc(t); }
}

export function scrollToBottom() {
  const v = document.getElementById('chat-view');
  if (v) v.scrollTop = v.scrollHeight;
}

export function formatCtx(n) {
  if (!n) return '';
  if (n >= 1048576) return (n / 1048576).toFixed(0) + 'M';
  if (n >= 1024) return (n / 1024).toFixed(0) + 'K';
  return String(n);
}

export function shortModel(id) {
  if (!id) return '';
  let s = id.replace(/-instruct$/i, '').replace(/-chat$/i, '');
  return s.length > 14 ? s.substring(0, 14) + '\u2026' : s;
}

export function isMobile() {
  return window.matchMedia('(max-width: 768px)').matches;
}

// Strip raw data artifacts the LLM may echo from tool results
export function cleanResponse(text, stripMarkdownImages) {
  if (!text) return '';
  let c = text;
  c = c.replace(/data:[a-z/+]+;base64,[A-Za-z0-9+/=]+/gi, '');
  c = c.replace(/;base64,[A-Za-z0-9+/=]{100,}/g, ';base64,[data removed]');
  c = c.replace(/b'[^']{50,}'/g, '');
  c = c.replace(/(\\x[0-9a-fA-F]{2}){10,}/g, '');
  if (stripMarkdownImages) {
    c = c.replace(/!\[[^\]]*\]\([^)]+\)/g, '');
  }
  c = c.replace(/\n{3,}/g, '\n\n').trim();
  return c;
}

export function normalizeImageUrl(url) {
  if (!url) return '';
  let u = url.split('?')[0].toLowerCase();
  u = u.replace(/_\d+x\d+/g, '');
  u = u.replace(/-\d+x\d+/g, '');
  u = u.replace(/_thumb|_small|_medium|_large|_preview/g, '');
  return u;
}

// ── Auth-aware fetch ─────────────────────────────────────────────
export function authFetch(url, opts = {}) {
  const token = localStorage.getItem('dartboard-jwt');
  if (!token || token.length < 10) {
    import('./auth.js').then(m => m.showAuthScreen());
    return Promise.reject(new Error('Not authenticated'));
  }
  if (!opts.headers) opts.headers = {};
  opts.headers['Authorization'] = 'Bearer ' + token;
  return fetch(url, opts).then(res => {
    if (res.status === 401 && url.startsWith('/api/')) {
      const currentToken = localStorage.getItem('dartboard-jwt');
      if (currentToken === token) {
        localStorage.removeItem('dartboard-jwt');
        import('./auth.js').then(m => m.showAuthScreen());
      }
      throw new Error('Session expired');
    }
    return res;
  });
}

// ── Time formatting ──────────────────────────────────────────────
export function timeAgo(dateStr) {
  const d = new Date(dateStr);
  const now = Date.now();
  const sec = Math.floor((now - d.getTime()) / 1000);
  if (sec < 60) return 'just now';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  if (sec < 604800) return Math.floor(sec / 86400) + 'd ago';
  return d.toLocaleDateString();
}
