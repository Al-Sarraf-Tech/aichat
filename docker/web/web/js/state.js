'use strict';

// ── Central State + Event Bus ────────────────────────────────────
// All modules import `state` and read/write freely.
// Use `on(event, fn)` / `emit(event, data)` for cross-module reactivity.

const _listeners = {};

export function on(event, fn) {
  if (!_listeners[event]) _listeners[event] = [];
  _listeners[event].push(fn);
}

export function off(event, fn) {
  if (!_listeners[event]) return;
  _listeners[event] = _listeners[event].filter(f => f !== fn);
}

export function emit(event, data) {
  if (!_listeners[event]) return;
  for (const fn of _listeners[event]) {
    try { fn(data); } catch (e) { console.error(`Event ${event}:`, e); }
  }
}

export const state = {
  currentConvId: null,
  isStreaming: false,
  selectedModel: null,
  selectedModelReady: false,
  availableModels: [],
  allConversations: [],
  abortController: null,
  pendingFiles: [],
  toolsEnabled: true,
  allPersonalities: [],
  selectedPersonality: { id: 'general', name: 'General Assistant', icon: '\u{1F9E0}' },
  customSystemPrompt: null,
  currentTab: 'chat',
  generationEpoch: 0,
  sendLock: false,

  // Model capabilities cache (validated by server)
  validatedCaps: {},

  // Settings (persisted to localStorage)
  settings: {
    theme: 'dark',
    fontSize: 15,
    sendOnEnter: true,
    showToolCards: true,
    showStreamStats: true,
    sidebarOpen: true,
  },

  // API provider availability {anthropic: true, openai: false, ...}
  availableProviders: {},

  // Conversation organization
  folders: [],       // [{ id, name, color }]
  pinnedConvs: new Set(),
};

// ── Restore persisted settings ───────────────────────────────────
export function loadSettings() {
  try {
    const saved = localStorage.getItem('ailab-settings');
    if (saved) Object.assign(state.settings, JSON.parse(saved));
  } catch {}
  state.toolsEnabled = localStorage.getItem('dartboard-tools') !== 'false';
  try {
    const folders = localStorage.getItem('ailab-folders');
    if (folders) state.folders = JSON.parse(folders);
  } catch {}
  try {
    const pins = localStorage.getItem('ailab-pins');
    if (pins) state.pinnedConvs = new Set(JSON.parse(pins));
  } catch {}
}

export function saveSettings() {
  localStorage.setItem('ailab-settings', JSON.stringify(state.settings));
}

export function saveFolders() {
  localStorage.setItem('ailab-folders', JSON.stringify(state.folders));
}

export function savePins() {
  localStorage.setItem('ailab-pins', JSON.stringify([...state.pinnedConvs]));
}
