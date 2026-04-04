'use strict';
import { state, emit, savePins } from './state.js';
import { authFetch, esc, isMobile, shortModel, timeAgo } from './utils.js';
import { capsHTML } from './models.js';
import { toast } from './toasts.js';

export async function loadConversations() {
  try { const res = await authFetch('/api/conversations?limit=100'); const data = await res.json(); state.allConversations = data.conversations || []; renderConvList(); } catch (e) { console.error('loadConversations:', e); }
}

export function renderConvList() {
  const list = document.getElementById('conv-list'); list.textContent = '';
  const convs = state.allConversations;
  const pinned = convs.filter(c => state.pinnedConvs.has(c.id));
  const unpinned = convs.filter(c => !state.pinnedConvs.has(c.id));
  if (pinned.length > 0) {
    const hdr = document.createElement('div'); hdr.className = 'conv-section-header'; hdr.textContent = '\uD83D\uDCCC Pinned'; list.appendChild(hdr);
    for (const c of pinned) list.appendChild(makeConvItem(c, true));
    const div = document.createElement('div'); div.className = 'conv-section-divider'; list.appendChild(div);
  }
  // Group unpinned by date
  const groups = bucketByDate(unpinned);
  for (const g of groups) {
    const hdr = document.createElement('div'); hdr.className = 'conv-section-header conv-date-header'; hdr.textContent = g.label; list.appendChild(hdr);
    for (const c of g.items) list.appendChild(makeConvItem(c, false));
  }
  // Empty state when no conversations exist
  if (convs.length === 0) {
    const empty = document.createElement('div'); empty.className = 'conv-empty-state';
    empty.innerHTML = '<div class="conv-empty-icon">\uD83D\uDCAC</div>'
      + '<div>No conversations yet</div>'
      + '<div class="conv-empty-hint">Start by typing a message below,<br>or press <kbd>Ctrl+N</kbd> for a new chat.</div>';
    list.appendChild(empty);
  }
}

function bucketByDate(convs) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart); yesterdayStart.setDate(yesterdayStart.getDate() - 1);
  const weekStart = new Date(todayStart); weekStart.setDate(weekStart.getDate() - 7);
  const monthStart = new Date(todayStart); monthStart.setDate(weekStart.getDate() - 30);
  const buckets = [
    { label: 'Today', items: [], after: todayStart },
    { label: 'Yesterday', items: [], after: yesterdayStart },
    { label: 'This Week', items: [], after: weekStart },
    { label: 'This Month', items: [], after: monthStart },
    { label: 'Older', items: [], after: null },
  ];
  for (const c of convs) {
    const d = new Date(c.updated_at);
    let placed = false;
    for (const b of buckets) {
      if (b.after && d >= b.after) { b.items.push(c); placed = true; break; }
    }
    if (!placed) buckets[buckets.length - 1].items.push(c);
  }
  return buckets.filter(b => b.items.length > 0);
}

function makeConvItem(c, isPinned) {
  const item = document.createElement('div');
  item.className = 'conv-item' + (c.id === state.currentConvId ? ' active' : '') + (isPinned ? ' pinned' : '');
  item.dataset.id = c.id;
  item.onclick = () => openConversation(c.id);
  item.oncontextmenu = (e) => { e.preventDefault(); showConvMenu(e, c); };
  const title = document.createElement('span'); title.className = 'conv-title'; title.textContent = c.title || 'New Chat';
  const meta = document.createElement('div'); meta.className = 'conv-item-meta';
  const tag = document.createElement('span'); tag.className = 'conv-model-tag'; tag.innerHTML = shortModel(c.model) + ' ' + capsHTML(c.model); tag.title = c.model || '';
  const time = document.createElement('span'); time.className = 'conv-time'; time.textContent = timeAgo(c.updated_at);
  meta.appendChild(tag); meta.appendChild(time);
  const actions = document.createElement('div'); actions.className = 'conv-actions';
  const pinBtn = document.createElement('button'); pinBtn.className = 'conv-action-btn' + (isPinned ? ' active' : ''); pinBtn.textContent = '\uD83D\uDCCC'; pinBtn.title = isPinned ? 'Unpin' : 'Pin';
  pinBtn.onclick = (e) => { e.stopPropagation(); togglePin(c.id); };
  const del = document.createElement('button'); del.className = 'conv-action-btn del-btn'; del.textContent = '\u00d7'; del.title = 'Delete';
  del.onclick = (e) => { e.stopPropagation(); deleteConv(c.id); };
  actions.appendChild(pinBtn); actions.appendChild(del);
  item.appendChild(title); item.appendChild(meta); item.appendChild(actions);
  return item;
}

function showConvMenu(e, conv) {
  document.querySelectorAll('.conv-context-menu').forEach(m => m.remove());
  const menu = document.createElement('div'); menu.className = 'conv-context-menu';
  menu.style.top = e.clientY + 'px'; menu.style.left = e.clientX + 'px';
  const isPinned = state.pinnedConvs.has(conv.id);
  const items = [
    { label: isPinned ? 'Unpin' : 'Pin to top', action: () => togglePin(conv.id) },
    { label: 'Rename', action: () => renameConv(conv.id, conv.title) },
    { label: 'Export', action: () => { import('./export.js').then(m => m.exportConversation(conv.id)); } },
    { label: 'Delete', action: () => deleteConv(conv.id), danger: true },
  ];
  for (const it of items) {
    const btn = document.createElement('button'); btn.className = 'context-menu-item' + (it.danger ? ' danger' : '');
    btn.textContent = it.label;
    btn.onclick = () => { menu.remove(); it.action(); }; menu.appendChild(btn);
  }
  document.body.appendChild(menu);
  const close = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', close); } };
  setTimeout(() => document.addEventListener('click', close), 0);
}

export async function newChat() {
  if (state.isStreaming) emit('stream:stop');
  state.currentConvId = null; emit('view:welcome');
  document.getElementById('messages').textContent = '';
  renderConvList(); closeMobileSidebar();
}

export async function openConversation(id) {
  if (state.isStreaming) emit('stream:stop');
  state.currentConvId = id; closeMobileSidebar();
  try {
    const res = await authFetch('/api/conversations/' + id); const data = await res.json();
    if (data.model && state.availableModels.some(m => m.id === data.model)) {
      state.selectedModel = data.model; localStorage.setItem('dartboard-model', data.model); emit('model:changed');
    }
    emit('messages:render', data.messages || []); emit('view:chat'); renderConvList();
  } catch (e) { console.error('openConversation:', e); }
}

export async function deleteConv(id) {
  // Find conversation title for confirmation
  const conv = state.allConversations.find(c => c.id === id);
  const title = conv ? (conv.title || 'Untitled').substring(0, 50) : 'this conversation';
  // Styled confirmation dialog
  return new Promise((resolve) => {
    const overlay = document.createElement('div'); overlay.className = 'confirm-overlay';
    const dialog = document.createElement('div'); dialog.className = 'confirm-dialog';
    dialog.innerHTML = '<div class="confirm-title">Delete conversation?</div>'
      + '<div class="confirm-body">' + title.replace(/</g, '&lt;') + '</div>'
      + '<div class="confirm-actions">'
      + '<button class="confirm-cancel">Cancel</button>'
      + '<button class="confirm-delete">Delete</button></div>';
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    const cleanup = () => { overlay.remove(); };
    dialog.querySelector('.confirm-cancel').onclick = () => { cleanup(); resolve(false); };
    overlay.onclick = (e) => { if (e.target === overlay) { cleanup(); resolve(false); } };
    dialog.querySelector('.confirm-delete').onclick = async () => {
      cleanup();
      try { await authFetch('/api/conversations/' + id, { method: 'DELETE' });
        if (id === state.currentConvId) { state.currentConvId = null; emit('view:welcome'); document.getElementById('messages').textContent = ''; }
        toast('Conversation deleted', 'info'); await loadConversations();
      } catch (e) { console.error('deleteConv:', e); toast('Delete failed', 'error'); }
      resolve(true);
    };
    dialog.querySelector('.confirm-cancel').focus();
  });
}

export function togglePin(id) {
  if (state.pinnedConvs.has(id)) { state.pinnedConvs.delete(id); toast('Unpinned', 'info'); }
  else { state.pinnedConvs.add(id); toast('Pinned', 'success'); }
  savePins(); renderConvList();
}

export async function renameConv(id, currentTitle) {
  // Find the title element in the sidebar and make it editable
  const item = document.querySelector(`.conv-item[data-id="${id}"]`);
  const titleEl = item && item.querySelector('.conv-title');
  if (!titleEl) {
    // Fallback if DOM element not found
    const t = prompt('Rename conversation:', currentTitle || 'New Chat');
    if (!t || t === currentTitle) return;
    try { await authFetch('/api/conversations/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t }) }); toast('Renamed', 'success'); await loadConversations(); } catch { toast('Rename failed', 'error'); }
    return;
  }
  const original = titleEl.textContent;
  titleEl.contentEditable = 'true';
  titleEl.classList.add('editing');
  titleEl.focus();
  // Select all text
  const range = document.createRange(); range.selectNodeContents(titleEl);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  const save = async () => {
    titleEl.contentEditable = 'false';
    titleEl.classList.remove('editing');
    const newTitle = titleEl.textContent.trim();
    if (!newTitle || newTitle === original) { titleEl.textContent = original; return; }
    try { await authFetch('/api/conversations/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: newTitle }) }); toast('Renamed', 'success'); } catch { toast('Rename failed', 'error'); titleEl.textContent = original; }
  };
  titleEl.onblur = save;
  titleEl.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
    if (e.key === 'Escape') { titleEl.textContent = original; titleEl.blur(); }
  };
}

export function filterConversations() {
  const q = document.getElementById('conv-search').value.trim();
  const ql = q.toLowerCase();
  document.querySelectorAll('#conv-list .conv-item').forEach(el => {
    const t = el.querySelector('.conv-title');
    if (!t) return;
    const text = t.textContent;
    const matches = text.toLowerCase().includes(ql);
    el.style.display = matches ? '' : 'none';
    // Highlight matching text
    if (q && matches) {
      const re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
      t.innerHTML = text.replace(re, '<mark>$1</mark>');
    } else {
      t.textContent = text; // remove highlights
    }
  });
}

export function closeMobileSidebar() { if (isMobile()) document.getElementById('sidebar').classList.remove('open'); }
export function toggleSidebar() { const s = document.getElementById('sidebar'); if (isMobile()) s.classList.toggle('open'); else s.classList.toggle('collapsed'); }
