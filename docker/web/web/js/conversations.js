'use strict';
import { state, emit, savePins } from './state.js';
import { authFetch, isMobile, shortModel, timeAgo } from './utils.js';
import { capsHTML } from './models.js';
import { toast } from './toasts.js';

export async function loadConversations() {
  try {
    const res = await authFetch('/api/conversations?limit=100');
    if (!res.ok) return;
    const data = await res.json();
    state.allConversations = data.conversations || [];
    renderConvList();
  } catch (err) {
    console.error('loadConversations:', err);
  }
}

export function renderConvList() {
  const list = document.getElementById('conv-list');
  list.textContent = '';

  const convs = state.allConversations;
  const pinned = convs.filter(conv => state.pinnedConvs.has(conv.id));
  const unpinned = convs.filter(conv => !state.pinnedConvs.has(conv.id));

  // Pinned section
  if (pinned.length > 0) {
    const pinnedHeader = document.createElement('div');
    pinnedHeader.className = 'conv-section-header';
    pinnedHeader.textContent = '\uD83D\uDCCC Pinned';
    list.appendChild(pinnedHeader);

    for (const conv of pinned) {
      list.appendChild(makeConvItem(conv, true));
    }

    const divider = document.createElement('div');
    divider.className = 'conv-section-divider';
    list.appendChild(divider);
  }

  // Group unpinned by date
  const groups = bucketByDate(unpinned);
  for (const group of groups) {
    const groupHeader = document.createElement('div');
    groupHeader.className = 'conv-section-header conv-date-header';
    groupHeader.textContent = group.label;
    list.appendChild(groupHeader);

    for (const conv of group.items) {
      list.appendChild(makeConvItem(conv, false));
    }
  }

  // Empty state when no conversations exist
  if (convs.length === 0) {
    const emptyState = document.createElement('div');
    emptyState.className = 'conv-empty-state';
    emptyState.innerHTML = '<div class="conv-empty-icon">\uD83D\uDCAC</div>'
      + '<div>No conversations yet</div>'
      + '<div class="conv-empty-hint">'
      + 'Start by typing a message below,<br>or press <kbd>Ctrl+N</kbd> for a new chat.'
      + '</div>';
    list.appendChild(emptyState);
  }
}

function bucketByDate(convs) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  const yesterdayStart = new Date(todayStart);
  yesterdayStart.setDate(yesterdayStart.getDate() - 1);

  const weekStart = new Date(todayStart);
  weekStart.setDate(weekStart.getDate() - 7);

  const monthStart = new Date(todayStart);
  monthStart.setDate(monthStart.getDate() - 30);

  const buckets = [
    { label: 'Today',      items: [], after: todayStart },
    { label: 'Yesterday',  items: [], after: yesterdayStart },
    { label: 'This Week',  items: [], after: weekStart },
    { label: 'This Month', items: [], after: monthStart },
    { label: 'Older',      items: [], after: null },
  ];

  for (const conv of convs) {
    const date = new Date(conv.updated_at);
    let placed = false;

    for (const bucket of buckets) {
      if (bucket.after && date >= bucket.after) {
        bucket.items.push(conv);
        placed = true;
        break;
      }
    }

    if (!placed) {
      buckets[buckets.length - 1].items.push(conv);
    }
  }

  return buckets.filter(bucket => bucket.items.length > 0);
}

function makeConvItem(conv, isPinned) {
  // Root item
  const item = document.createElement('div');
  item.className = 'conv-item'
    + (conv.id === state.currentConvId ? ' active' : '')
    + (isPinned ? ' pinned' : '');
  item.dataset.id = conv.id;
  item.onclick = () => openConversation(conv.id);
  item.oncontextmenu = (event) => { event.preventDefault(); showConvMenu(event, conv); };

  // Title
  const title = document.createElement('span');
  title.className = 'conv-title';
  title.textContent = conv.title || 'New Chat';

  // Meta row: model tag + timestamp
  const meta = document.createElement('div');
  meta.className = 'conv-item-meta';

  const modelTag = document.createElement('span');
  modelTag.className = 'conv-model-tag';
  modelTag.innerHTML = shortModel(conv.model) + ' ' + capsHTML(conv.model);
  modelTag.title = conv.model || '';

  const timeLabel = document.createElement('span');
  timeLabel.className = 'conv-time';
  timeLabel.textContent = timeAgo(conv.updated_at);

  meta.appendChild(modelTag);
  meta.appendChild(timeLabel);

  // Action buttons: pin + delete
  const actions = document.createElement('div');
  actions.className = 'conv-actions';

  const pinBtn = document.createElement('button');
  pinBtn.className = 'conv-action-btn' + (isPinned ? ' active' : '');
  pinBtn.textContent = '\uD83D\uDCCC';
  pinBtn.title = isPinned ? 'Unpin' : 'Pin';
  pinBtn.onclick = (event) => { event.stopPropagation(); togglePin(conv.id); };

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'conv-action-btn del-btn';
  deleteBtn.textContent = '\u00d7';
  deleteBtn.title = 'Delete';
  deleteBtn.onclick = (event) => { event.stopPropagation(); deleteConv(conv.id); };

  actions.appendChild(pinBtn);
  actions.appendChild(deleteBtn);

  // Assemble
  item.appendChild(title);
  item.appendChild(meta);
  item.appendChild(actions);

  return item;
}

function showConvMenu(event, conv) {
  document.querySelectorAll('.conv-context-menu').forEach(menu => menu.remove());

  const menu = document.createElement('div');
  menu.className = 'conv-context-menu';
  menu.style.top = event.clientY + 'px';
  menu.style.left = event.clientX + 'px';

  const isPinned = state.pinnedConvs.has(conv.id);
  const menuItems = [
    { label: isPinned ? 'Unpin' : 'Pin to top', action: () => togglePin(conv.id) },
    { label: 'Rename',  action: () => renameConv(conv.id, conv.title) },
    { label: 'Export',  action: () => {
      import('./export.js').then(mod => mod.exportConversation(conv.id));
    } },
    { label: 'Delete',  action: () => deleteConv(conv.id), danger: true },
  ];

  for (const menuItem of menuItems) {
    const btn = document.createElement('button');
    btn.className = 'context-menu-item' + (menuItem.danger ? ' danger' : '');
    btn.textContent = menuItem.label;
    btn.onclick = () => { menu.remove(); menuItem.action(); };
    menu.appendChild(btn);
  }

  document.body.appendChild(menu);

  const closeOnOutsideClick = (clickEvent) => {
    if (!menu.contains(clickEvent.target)) {
      menu.remove();
      document.removeEventListener('click', closeOnOutsideClick);
    }
  };
  setTimeout(() => document.addEventListener('click', closeOnOutsideClick), 0);
}

export async function newChat() {
  if (state.isStreaming) emit('stream:stop');

  state.currentConvId = null;
  emit('view:welcome');
  document.getElementById('messages').textContent = '';

  renderConvList();
  closeMobileSidebar();
}

export async function openConversation(id) {
  if (state.isStreaming) emit('stream:stop');

  state.currentConvId = id;
  closeMobileSidebar();

  try {
    const res = await authFetch('/api/conversations/' + id);
    if (!res.ok) {
      toast('Failed to load conversation', 'error');
      return;
    }
    const data = await res.json();

    // Guard against stale response from a concurrent openConversation call
    if (state.currentConvId !== id) return;

    if (data.model && state.availableModels.some(model => model.id === data.model)) {
      state.selectedModel = data.model;
      localStorage.setItem('dartboard-model', data.model);
      emit('model:changed');
    }

    emit('messages:render', data.messages || []);
    emit('view:chat');
    renderConvList();
  } catch (err) {
    console.error('openConversation:', err);
    toast('Failed to load conversation', 'error');
  }
}

export async function deleteConv(id) {
  // Find conversation title for the confirmation dialog
  const conv = state.allConversations.find(item => item.id === id);
  const displayTitle = conv
    ? (conv.title || 'Untitled').substring(0, 50)
    : 'this conversation';

  // Styled confirmation dialog
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog';
    dialog.innerHTML = ''
      + '<div class="confirm-title">Delete conversation?</div>'
      + '<div class="confirm-body">' + displayTitle.replace(/</g, '&lt;') + '</div>'
      + '<div class="confirm-actions">'
      +   '<button class="confirm-cancel">Cancel</button>'
      +   '<button class="confirm-delete">Delete</button>'
      + '</div>';

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    const cleanup = () => { overlay.remove(); };

    dialog.querySelector('.confirm-cancel').onclick = () => {
      cleanup();
      resolve(false);
    };

    overlay.onclick = (clickEvent) => {
      if (clickEvent.target === overlay) {
        cleanup();
        resolve(false);
      }
    };

    dialog.querySelector('.confirm-delete').onclick = async () => {
      cleanup();
      try {
        await authFetch('/api/conversations/' + id, { method: 'DELETE' });

        if (id === state.currentConvId) {
          state.currentConvId = null;
          emit('view:welcome');
          document.getElementById('messages').textContent = '';
        }

        toast('Conversation deleted', 'info');
        await loadConversations();
      } catch (err) {
        console.error('deleteConv:', err);
        toast('Delete failed', 'error');
      }
      resolve(true);
    };

    dialog.querySelector('.confirm-cancel').focus();
  });
}

export function togglePin(id) {
  if (state.pinnedConvs.has(id)) {
    state.pinnedConvs.delete(id);
    toast('Unpinned', 'info');
  } else {
    state.pinnedConvs.add(id);
    toast('Pinned', 'success');
  }
  savePins();
  renderConvList();
}

export async function renameConv(id, currentTitle) {
  // Find the title element in the sidebar and make it editable inline
  const convItem = document.querySelector(`.conv-item[data-id="${id}"]`);
  const titleEl = convItem && convItem.querySelector('.conv-title');

  if (!titleEl) {
    // Fallback: prompt if DOM element is not found
    const promptResult = prompt('Rename conversation:', currentTitle || 'New Chat');
    if (!promptResult || promptResult === currentTitle) return;
    try {
      await authFetch('/api/conversations/' + id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: promptResult }),
      });
      toast('Renamed', 'success');
      await loadConversations();
    } catch {
      toast('Rename failed', 'error');
    }
    return;
  }

  const originalTitle = titleEl.textContent;
  titleEl.contentEditable = 'true';
  titleEl.classList.add('editing');
  titleEl.focus();

  // Select all text in the element
  const range = document.createRange();
  range.selectNodeContents(titleEl);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);

  const save = async () => {
    titleEl.contentEditable = 'false';
    titleEl.classList.remove('editing');

    const newTitle = titleEl.textContent.trim();
    if (!newTitle || newTitle === originalTitle) {
      titleEl.textContent = originalTitle;
      return;
    }

    try {
      await authFetch('/api/conversations/' + id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      });
      toast('Renamed', 'success');
    } catch {
      toast('Rename failed', 'error');
      titleEl.textContent = originalTitle;
    }
  };

  titleEl.onblur = save;
  titleEl.onkeydown = (event) => {
    if (event.key === 'Enter') { event.preventDefault(); titleEl.blur(); }
    if (event.key === 'Escape') { titleEl.textContent = originalTitle; titleEl.blur(); }
  };
}

export function filterConversations() {
  const query = document.getElementById('conv-search').value.trim();
  const queryLower = query.toLowerCase();

  document.querySelectorAll('#conv-list .conv-item').forEach(el => {
    const titleEl = el.querySelector('.conv-title');
    if (!titleEl) return;

    const text = titleEl.textContent;
    const matches = text.toLowerCase().includes(queryLower);
    el.style.display = matches ? '' : 'none';

    // Highlight matching text
    if (query && matches) {
      const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const highlightPattern = new RegExp('(' + escapedQuery + ')', 'gi');
      titleEl.innerHTML = text.replace(highlightPattern, '<mark>$1</mark>');
    } else {
      titleEl.textContent = text; // remove highlights
    }
  });
}

export function closeMobileSidebar() {
  if (isMobile()) {
    document.getElementById('sidebar').classList.remove('open');
  }
}

export function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (isMobile()) {
    sidebar.classList.toggle('open');
  } else {
    sidebar.classList.toggle('collapsed');
  }
}
