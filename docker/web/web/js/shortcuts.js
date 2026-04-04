'use strict';
import { state, emit } from './state.js';
import { newChat, toggleSidebar } from './conversations.js';
import { stopGeneration } from './messages.js';

const shortcuts = [];

function reg(key, desc, handler, opts) { shortcuts.push({ key, desc, handler, ...(opts||{}) }); }

export function initShortcuts() {
  reg('ctrl+n', 'New chat', () => newChat());
  reg('ctrl+/', 'Toggle sidebar', () => toggleSidebar());
  reg('ctrl+shift+s', 'Stop generation', () => stopGeneration());
  reg('ctrl+k', 'Search conversations', () => emit('search:open'));
  reg('ctrl+,', 'Settings', () => emit('settings:toggle'));
  reg('ctrl+e', 'Export conversation', () => { if (state.currentConvId) import('./export.js').then(m => m.exportConversation(state.currentConvId)); });
  reg('ctrl+shift+d', 'System status', () => emit('status:toggle'));
  reg('?', 'Show shortcuts', () => toggleHelp(), { skipInInput: true });
  reg('Escape', 'Close dialogs', () => closeAll());

  document.addEventListener('keydown', (e) => {
    if (!document.getElementById('auth-screen').classList.contains('hidden')) return;
    const key = buildKey(e);
    for (const s of shortcuts) {
      if (s.key.toLowerCase() !== key.toLowerCase()) continue;
      if (s.skipInInput && isInput(e.target)) continue;
      e.preventDefault(); s.handler(); return;
    }
  });
}

function buildKey(e) {
  const p = []; if (e.ctrlKey || e.metaKey) p.push('ctrl'); if (e.shiftKey) p.push('shift'); if (e.altKey) p.push('alt');
  p.push(e.key === ' ' ? 'space' : e.key); return p.join('+');
}
function isInput(el) { const t = el.tagName; return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT' || el.isContentEditable; }

function closeAll() {
  document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(m => m.classList.add('hidden'));
  emit('search:close'); emit('settings:close');
  const lb = document.querySelector('.lightbox-overlay'); if (lb) lb.remove();
  document.querySelectorAll('.conv-context-menu').forEach(m => m.remove());
  const so = document.getElementById('shortcuts-overlay'); if (so) so.remove();
}

function toggleHelp() {
  const modal = document.getElementById('shortcuts-modal');
  if (modal) modal.classList.toggle('hidden');
}

export function closeShortcuts() {
  const modal = document.getElementById('shortcuts-modal');
  if (modal) modal.classList.add('hidden');
}
