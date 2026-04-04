'use strict';
import { state, on, emit } from './state.js';
import { authFetch, timeAgo } from './utils.js';
import { openConversation } from './conversations.js';

let searchOpen = false;

export function initSearch() { on('search:open', openS); on('search:close', closeS); }

function openS() {
  if (searchOpen) { closeS(); return; }
  searchOpen = true;
  const ov = document.createElement('div'); ov.id = 'search-overlay'; ov.className = 'search-overlay';
  ov.onclick = (e) => { if (e.target === ov) closeS(); };
  const panel = document.createElement('div'); panel.className = 'search-panel';
  const input = document.createElement('input'); input.id = 'global-search-input'; input.className = 'search-input';
  input.type = 'text'; input.placeholder = 'Search all conversations...'; input.autocomplete = 'off';
  const results = document.createElement('div'); results.id = 'search-results'; results.className = 'search-results';
  results.textContent = 'Type to search across all your conversations';
  results.style.padding = '24px'; results.style.textAlign = 'center'; results.style.color = 'var(--text-muted)';
  panel.appendChild(input); panel.appendChild(results); ov.appendChild(panel); document.body.appendChild(ov);
  requestAnimationFrame(() => { ov.classList.add('visible'); input.focus(); });
  let debounce = null;
  input.oninput = () => { clearTimeout(debounce); const q = input.value.trim();
    if (q.length < 2) { results.textContent = 'Type at least 2 characters'; return; }
    debounce = setTimeout(() => doSearch(q, results), 300);
  };
  input.onkeydown = (e) => {
    if (e.key === 'Escape') closeS();
    if (e.key === 'Enter') { const f = results.querySelector('.search-result-item'); if (f) f.click(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); const items = results.querySelectorAll('.search-result-item');
      if (!items.length) return; const active = results.querySelector('.search-result-item.active');
      let idx = active ? Array.from(items).indexOf(active) : -1; if (active) active.classList.remove('active');
      idx = e.key === 'ArrowDown' ? Math.min(idx+1, items.length-1) : Math.max(idx-1, 0);
      items[idx].classList.add('active'); items[idx].scrollIntoView({ block: 'nearest' });
    }
  };
}

function closeS() {
  const ov = document.getElementById('search-overlay');
  if (ov) { ov.classList.remove('visible'); setTimeout(() => ov.remove(), 200); }
  searchOpen = false;
}

async function doSearch(query, el) {
  el.textContent = 'Searching...'; el.style.padding = '24px'; el.style.textAlign = 'center';
  const matches = [];
  for (const conv of state.allConversations) {
    if ((conv.title || '').toLowerCase().includes(query.toLowerCase())) {
      matches.push({ convId: conv.id, title: conv.title, snippet: 'Title match', time: conv.updated_at, type: 'title' });
    }
  }
  try {
    const res = await authFetch('/api/search?q=' + encodeURIComponent(query) + '&limit=20');
    if (res.ok) { const data = await res.json();
      for (const r of (data.results || [])) {
        if (matches.some(m => m.convId === r.conversation_id && m.type === 'title')) continue;
        const idx = (r.content||'').toLowerCase().indexOf(query.toLowerCase());
        const start = Math.max(0, idx - 40); const end = Math.min((r.content||'').length, idx + query.length + 80);
        let snippet = ''; if (start > 0) snippet += '...'; snippet += (r.content||'').substring(start, end); if (end < (r.content||'').length) snippet += '...';
        matches.push({ convId: r.conversation_id, title: r.conversation_title || 'Chat', snippet, time: r.created_at, type: 'message', role: r.role });
      }
    }
  } catch {}
  if (!matches.length) { el.textContent = 'No results found'; return; }
  el.textContent = ''; el.style.padding = '8px'; el.style.textAlign = '';
  for (const m of matches.slice(0, 20)) {
    const item = document.createElement('div'); item.className = 'search-result-item';
    item.onclick = () => { closeS(); openConversation(m.convId); };
    const titleRow = document.createElement('div'); titleRow.className = 'search-result-title';
    const titleText = document.createElement('span'); titleText.textContent = m.title;
    const timeText = document.createElement('span'); timeText.className = 'search-result-time'; timeText.textContent = timeAgo(m.time);
    titleRow.appendChild(titleText); titleRow.appendChild(timeText);
    if (m.type === 'message') { const badge = document.createElement('span'); badge.className = 'search-result-badge'; badge.textContent = m.role === 'assistant' ? 'AI' : 'You'; titleRow.insertBefore(badge, timeText); }
    const snippet = document.createElement('div'); snippet.className = 'search-result-snippet'; snippet.textContent = (m.snippet||'').substring(0, 150);
    item.appendChild(titleRow); item.appendChild(snippet); el.appendChild(item);
  }
}
