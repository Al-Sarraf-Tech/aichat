'use strict';
import { state, on } from './state.js';
import { authFetch, timeAgo } from './utils.js';
import { openConversation } from './conversations.js';

let searchOpen = false;

export function initSearch() {
  on('search:open', openSearch);
  on('search:close', closeSearch);
}

function openSearch() {
  if (searchOpen) { closeSearch(); return; }
  searchOpen = true;

  const overlay = document.createElement('div');
  overlay.id = 'search-overlay';
  overlay.className = 'search-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeSearch(); };

  const panel = document.createElement('div');
  panel.className = 'search-panel';

  const input = document.createElement('input');
  input.id = 'global-search-input';
  input.className = 'search-input';
  input.type = 'text';
  input.placeholder = 'Search all conversations...';
  input.autocomplete = 'off';

  const results = document.createElement('div');
  results.id = 'search-results';
  results.className = 'search-results';
  results.textContent = 'Type to search across all your conversations';
  results.style.padding = '24px';
  results.style.textAlign = 'center';
  results.style.color = 'var(--text-muted)';

  panel.appendChild(input);
  panel.appendChild(results);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  requestAnimationFrame(() => {
    overlay.classList.add('visible');
    input.focus();
  });

  let debounceTimer = null;
  input.oninput = () => {
    clearTimeout(debounceTimer);
    const query = input.value.trim();
    if (query.length < 2) {
      results.textContent = 'Type at least 2 characters';
      return;
    }
    debounceTimer = setTimeout(() => doSearch(query, results), 300);
  };

  input.onkeydown = (e) => {
    if (e.key === 'Escape') {
      closeSearch();
      return;
    }

    if (e.key === 'Enter') {
      const first = results.querySelector('.search-result-item');
      if (first) first.click();
      return;
    }

    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const items = results.querySelectorAll('.search-result-item');
      if (!items.length) return;

      const active = results.querySelector('.search-result-item.active');
      let idx = active ? Array.from(items).indexOf(active) : -1;
      if (active) active.classList.remove('active');

      idx = e.key === 'ArrowDown'
        ? Math.min(idx + 1, items.length - 1)
        : Math.max(idx - 1, 0);

      items[idx].classList.add('active');
      items[idx].scrollIntoView({ block: 'nearest' });
    }
  };
}

function closeSearch() {
  const overlay = document.getElementById('search-overlay');
  if (overlay) {
    overlay.classList.remove('visible');
    setTimeout(() => overlay.remove(), 200);
  }
  searchOpen = false;
}

async function doSearch(query, resultsEl) {
  resultsEl.textContent = 'Searching...';
  resultsEl.style.padding = '24px';
  resultsEl.style.textAlign = 'center';

  const matches = [];

  for (const conv of state.allConversations) {
    if ((conv.title || '').toLowerCase().includes(query.toLowerCase())) {
      matches.push({
        convId: conv.id,
        title: conv.title,
        snippet: 'Title match',
        time: conv.updated_at,
        type: 'title',
      });
    }
  }

  try {
    const res = await authFetch('/api/search?q=' + encodeURIComponent(query) + '&limit=20');
    if (res.ok) {
      const data = await res.json();
      for (const result of (data.results || [])) {
        if (matches.some(m => m.convId === result.conversation_id && m.type === 'title')) continue;

        const content = result.content || '';
        const matchPos = content.toLowerCase().indexOf(query.toLowerCase());
        const start = Math.max(0, matchPos - 40);
        const end = Math.min(content.length, matchPos + query.length + 80);

        let snippet = '';
        if (start > 0) snippet += '...';
        snippet += content.substring(start, end);
        if (end < content.length) snippet += '...';

        matches.push({
          convId: result.conversation_id,
          title: result.conversation_title || 'Chat',
          snippet,
          time: result.created_at,
          type: 'message',
          role: result.role,
        });
      }
    }
  } catch {}

  if (!matches.length) {
    resultsEl.textContent = 'No results found';
    return;
  }

  resultsEl.textContent = '';
  resultsEl.style.padding = '8px';
  resultsEl.style.textAlign = '';

  for (const match of matches.slice(0, 20)) {
    const item = document.createElement('div');
    item.className = 'search-result-item';
    item.onclick = () => { closeSearch(); openConversation(match.convId); };

    const titleRow = document.createElement('div');
    titleRow.className = 'search-result-title';

    const titleText = document.createElement('span');
    titleText.textContent = match.title;

    const timeText = document.createElement('span');
    timeText.className = 'search-result-time';
    timeText.textContent = timeAgo(match.time);

    titleRow.appendChild(titleText);
    titleRow.appendChild(timeText);

    if (match.type === 'message') {
      const badge = document.createElement('span');
      badge.className = 'search-result-badge';
      badge.textContent = match.role === 'assistant' ? 'AI' : 'You';
      titleRow.insertBefore(badge, timeText);
    }

    const snippet = document.createElement('div');
    snippet.className = 'search-result-snippet';
    snippet.textContent = (match.snippet || '').substring(0, 150);

    item.appendChild(titleRow);
    item.appendChild(snippet);
    resultsEl.appendChild(item);
  }
}
