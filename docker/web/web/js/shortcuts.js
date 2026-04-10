'use strict';
import { state, emit } from './state.js';
import { newChat, toggleSidebar } from './conversations.js';
import { stopGeneration } from './messages.js';

const shortcuts = [];

function reg(key, desc, handler, opts) {
  shortcuts.push({ key, desc, handler, ...(opts || {}) });
}

export function initShortcuts() {
  reg('ctrl+n', 'New chat', () => newChat());
  reg('ctrl+/', 'Toggle sidebar', () => toggleSidebar());
  reg('ctrl+shift+s', 'Stop generation', () => stopGeneration());
  reg('ctrl+k', 'Search conversations', () => emit('search:open'));
  reg('ctrl+,', 'Settings', () => emit('settings:toggle'));
  reg('ctrl+e', 'Export conversation', () => {
    if (state.currentConvId) {
      import('./export.js').then(m => m.exportConversation(state.currentConvId));
    }
  });
  reg('ctrl+shift+d', 'System status', () => emit('status:toggle'));
  reg('?', 'Show shortcuts', () => toggleHelp(), { skipInInput: true });
  reg('Escape', 'Close dialogs', () => closeAll());

  document.addEventListener('keydown', (e) => {
    if (!document.getElementById('auth-screen').classList.contains('hidden')) return;
    const key = buildKey(e);
    for (const shortcut of shortcuts) {
      if (shortcut.key.toLowerCase() !== key.toLowerCase()) continue;
      if (shortcut.skipInInput && isInput(e.target)) continue;
      e.preventDefault();
      shortcut.handler();
      return;
    }
  });
}

function buildKey(e) {
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push('ctrl');
  if (e.shiftKey) parts.push('shift');
  if (e.altKey) parts.push('alt');
  parts.push(e.key === ' ' ? 'space' : e.key);
  return parts.join('+');
}

function isInput(el) {
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

function closeAll() {
  document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(modal => {
    modal.classList.add('hidden');
  });
  emit('search:close');
  emit('settings:close');
  const lightbox = document.querySelector('.lightbox-overlay');
  if (lightbox) lightbox.remove();
  document.querySelectorAll('.conv-context-menu').forEach(menu => menu.remove());
  const shortcutsModal = document.getElementById('shortcuts-modal');
  if (shortcutsModal) shortcutsModal.classList.add('hidden');
}

function toggleHelp() {
  const modal = document.getElementById('shortcuts-modal');
  if (modal) modal.classList.toggle('hidden');
}

export function closeShortcuts() {
  const modal = document.getElementById('shortcuts-modal');
  if (modal) modal.classList.add('hidden');
}
