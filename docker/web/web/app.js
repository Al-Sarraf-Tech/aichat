'use strict';

// ── Entry Point — Jamal's AI Lab v2.0 ───────────────────────────────────────
// ES module orchestrator: imports all modules, wires events, boots the app.

import { state, on, loadSettings } from './js/state.js';
// (utils imported transitively by sub-modules)
import {
  checkAuth, authLogin, authRegister, authLogout,
  showLogin, showRegister, initAuthKeys,
} from './js/auth.js';
import {
  loadModels, loadToolCount, renderModelMenu,
  updateModelDisplay, updateToolsToggle,
} from './js/models.js';
import {
  loadConversations, newChat,
  toggleSidebar, filterConversations,
} from './js/conversations.js';
import {
  renderMessages, send, stopGeneration,
  handleFiles, setupDragDrop,
} from './js/messages.js';
import {
  loadPersonalities, togglePersonalityModal, closePersonalityModal,
  filterPersonalities, toggleCustomPrompt, applyCustomPrompt,
} from './js/personalities.js';
import { initImageGen, checkComfyUIStatus } from './js/imagegen.js';
import { initShortcuts, closeShortcuts } from './js/shortcuts.js';
import { initSettings, applyTheme } from './js/settings.js';
import { initSearch } from './js/search.js';
import { initVoice } from './js/voice.js';
import { initStatus } from './js/status.js';
import { initPreview } from './js/preview.js';
import { toast } from './js/toasts.js';

// ── Global Error Handler ─────────────────────────────────────────────────────

window.onerror = function (msg, src, line, col, err) {
  const srcStr = String(src || '');
  const msgStr = String(msg || '');
  const isOwn       = srcStr.includes('/app.js') || srcStr.includes('/js/');
  const isExtension = srcStr.startsWith('chrome-extension://')
    || srcStr.startsWith('moz-extension://')
    || srcStr.startsWith('safari-extension://');
  const isThirdParty = msgStr === 'Script error.'
    || isExtension
    || msgStr.includes('ethereum')
    || msgStr.includes('web3')
    || (!srcStr && !line);

  if (isThirdParty && !isOwn) return;

  console.error('JS Error:', msg, 'at', src, line);
  toast('JS Error: ' + msg, 'error');
};

window.addEventListener('unhandledrejection', function (e) {
  const msg = String(e.reason?.message || e.reason || '');
  if (msg.includes('ethereum') || msg.includes('web3') || msg.includes('extension')) return;
  console.error('Unhandled promise:', e.reason);
  toast('Error: ' + msg, 'error');
});

// ── Marked config ────────────────────────────────────────────────────────────

try {
  if (typeof marked !== 'undefined') {
    const renderer = new marked.Renderer();
    renderer.link = ({ href, title, text }) => {
      const t = title ? ` title="${title}"` : '';
      return `<a href="${href}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`;
    };
    marked.use({ breaks: true, gfm: true, renderer });
  }
} catch (e) {
  console.warn('marked.use failed:', e);
}

// ── Wire up cross-module events ──────────────────────────────────────────────

on('view:welcome', () => {
  document.getElementById('welcome').classList.remove('hidden');
  document.getElementById('chat-view').classList.add('hidden');
});

on('view:chat', () => {
  document.getElementById('welcome').classList.add('hidden');
  document.getElementById('chat-view').classList.remove('hidden');

  // Restore draft if available
  const draftKey = 'ailab-draft-' + (state.currentConvId || 'new');
  const draft    = localStorage.getItem(draftKey);
  const input    = document.getElementById('input');
  if (draft && input && !input.value.trim()) {
    input.value = draft;
    handleInput();
  }
});

on('messages:render', (msgs) => renderMessages(msgs));

on('model:changed', () => {
  updateModelDisplay();
  loadPersonalities();
});

on('model:loading', (detail) => {
  document.getElementById('loading-text').textContent = detail.text || 'Loading model...';
  document.getElementById('model-loading').classList.remove('hidden');
});

on('model:ready', (detail) => {
  document.getElementById('loading-text').textContent = detail.text || 'Ready';
  document.getElementById('model-loading').classList.remove('hidden');
  setTimeout(() => document.getElementById('model-loading').classList.add('hidden'), 2000);
  updateActionBtn();
});

on('conn:status', (ok) => {
  const dot    = document.getElementById('conn-status');
  dot.className = 'status-dot ' + (ok ? 'connected' : 'disconnected');
  dot.title     = ok ? 'Connected to LM Studio' : 'Cannot reach LM Studio';
});

on('tools:update',  () => updateToolsToggle());
on('input:changed', () => updateActionBtn());
on('send:trigger',  () => send());
on('stream:stop',   () => stopGeneration());

on('auth:login', async () => {
  await Promise.allSettled([
    loadModels(),
    loadConversations(),
    loadToolCount(),
    loadPersonalities(),
  ]);
});

on('auth:beforeLogout', () => {
  if (state.isStreaming) stopGeneration();
  if (state.selectedModel && localStorage.getItem('dartboard-jwt')) {
    const payload = JSON.stringify({ model: state.selectedModel });
    navigator.sendBeacon('/api/unload', new Blob([payload], { type: 'application/json' }));
  }
});

// ── Action Button ────────────────────────────────────────────────────────────

function updateActionBtn() {
  const btn      = document.getElementById('action-btn');
  const sendIcon = document.getElementById('icon-send');
  const stopIcon = document.getElementById('icon-stop');

  if (state.isStreaming) {
    sendIcon.classList.add('hidden');
    stopIcon.classList.remove('hidden');
    btn.classList.add('stop-mode');
    btn.disabled = false;
    btn.title    = 'Stop';
  } else {
    sendIcon.classList.remove('hidden');
    stopIcon.classList.add('hidden');
    btn.classList.remove('stop-mode');
    btn.title    = state.selectedModelReady ? 'Send' : 'Select and load a model first';
    btn.disabled = !state.selectedModelReady
      || (!document.getElementById('input').value.trim() && !state.pendingFiles.length);
  }
}

function handleAction() {
  state.isStreaming ? stopGeneration() : send();
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && state.settings.sendOnEnter) {
    e.preventDefault();
    if (!state.isStreaming) send();
  }
}

function handleInput() {
  const input = document.getElementById('input');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
  updateActionBtn();

  // Draft auto-save (debounced 1.5 s)
  clearTimeout(handleInput._dt);
  handleInput._dt = setTimeout(() => {
    const value   = input.value;
    const draftKey = 'ailab-draft-' + (state.currentConvId || 'new');
    if (value.trim()) localStorage.setItem(draftKey, value);
    else              localStorage.removeItem(draftKey);
  }, 1500);
}

// ── Tab Navigation ───────────────────────────────────────────────────────────

function switchTab(tab) {
  state.currentTab = tab;

  document.querySelectorAll('.tab-btn').forEach(
    (btn) => btn.classList.toggle('active', btn.dataset.tab === tab)
  );

  const imageGenView = document.getElementById('imagegen-view');

  if (tab === 'chat') {
    imageGenView.classList.add('hidden');

    document.getElementById('input-box').classList.remove('hidden');
    document.getElementById('model-selector').style.display = '';
    document.getElementById('model-caps').style.display     = '';
    document.getElementById('tools-toggle').style.display   = '';

    if (state.currentConvId) {
      document.getElementById('welcome').classList.add('hidden');
      document.getElementById('chat-view').classList.remove('hidden');
    } else {
      document.getElementById('welcome').classList.remove('hidden');
      document.getElementById('chat-view').classList.add('hidden');
    }

  } else if (tab === 'imagegen') {
    document.getElementById('welcome').classList.add('hidden');
    document.getElementById('chat-view').classList.add('hidden');
    document.getElementById('input-box').classList.add('hidden');
    document.getElementById('model-selector').style.display = 'none';
    document.getElementById('model-caps').style.display     = 'none';
    document.getElementById('tools-toggle').style.display   = 'none';

    imageGenView.classList.remove('hidden');
    checkComfyUIStatus();
  }
}

// ── Expose global handlers for HTML onclick attributes ───────────────────────

window.authLogin             = authLogin;
window.authRegister          = authRegister;
window.authLogout            = authLogout;
window.showLogin             = showLogin;
window.showRegister          = showRegister;
window.newChat               = newChat;
window.toggleSidebar         = toggleSidebar;
window.filterConversations   = filterConversations;
window.togglePersonalityModal = togglePersonalityModal;
window.closePersonalityModal = closePersonalityModal;
window.filterPersonalities   = filterPersonalities;
window.toggleCustomPrompt    = toggleCustomPrompt;
window.applyCustomPrompt     = applyCustomPrompt;
window.handleKey             = handleKey;
window.handleInput           = handleInput;
window.handleAction          = handleAction;
window.handleFiles           = (input) => handleFiles(input);
window.switchTab             = switchTab;
window.closeShortcuts        = closeShortcuts;

window.toggleModelMenu = () => {
  const menu = document.getElementById('model-menu');
  if (menu.classList.contains('hidden')) {
    renderModelMenu();
    menu.classList.remove('hidden');
  } else {
    menu.classList.add('hidden');
  }
};

window.toggleTools = () => {
  state.toolsEnabled = !state.toolsEnabled;
  localStorage.setItem('dartboard-tools', state.toolsEnabled);
  updateToolsToggle();
};

window.useSuggestion = (text) => {
  const input = document.getElementById('input');
  input.value = text;
  input.focus();
  handleInput();
};

window.scrollChatToBottom = () => {
  const chatView = document.getElementById('chat-view');
  if (chatView) chatView.scrollTo({ top: chatView.scrollHeight, behavior: 'smooth' });
};

// ── Close model menu on outside click ───────────────────────────────────────

document.addEventListener('click', (e) => {
  const selector = document.getElementById('model-selector');
  const menu     = document.getElementById('model-menu');
  if (selector && menu && !selector.contains(e.target) && !menu.contains(e.target)) {
    menu.classList.add('hidden');
  }
});

// ── Model Unload on Page Exit ────────────────────────────────────────────────

window.addEventListener('beforeunload', () => {
  if (state.selectedModel && localStorage.getItem('dartboard-jwt')) {
    const payload = JSON.stringify({ model: state.selectedModel });
    navigator.sendBeacon('/api/unload', new Blob([payload], { type: 'application/json' }));
  }
});

// ── Boot ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  // Load persisted settings
  loadSettings();
  applyTheme();

  // Init feature modules
  initAuthKeys();
  initShortcuts();
  initSettings();
  initSearch();
  initVoice();
  initStatus();
  initPreview();
  initImageGen();

  updateToolsToggle();
  setupDragDrop();

  // Scroll FAB: show when user scrolls up in chat
  const chatView  = document.getElementById('chat-view');
  const scrollFab = document.getElementById('scroll-fab');
  if (chatView && scrollFab) {
    chatView.addEventListener('scroll', () => {
      const distFromBottom = chatView.scrollHeight - chatView.scrollTop - chatView.clientHeight;
      scrollFab.classList.toggle('hidden', distFromBottom < 120);
    });
  }

  const authed = await checkAuth();
  if (authed) {
    const jwt = localStorage.getItem('dartboard-jwt');
    if (jwt) {
      document.cookie = `dartboard_token=${jwt}; path=/; SameSite=Strict; Secure; max-age=${60 * 60 * 24 * 7}`;
    }
    await Promise.all([
      loadModels(),
      loadConversations(),
      loadToolCount(),
      loadPersonalities(),
    ]);
  }
});
