'use strict';

import { state, saveSettings, on } from './state.js';
import { toast } from './toasts.js';

let settingsOpen = false;

// ── Public API ───────────────────────────────────────────────────────────────

export function initSettings() {
  on('settings:toggle', toggle);
  on('settings:close', close);
}

// ── Toggle / Open / Close ────────────────────────────────────────────────────

function toggle() {
  settingsOpen ? close() : open();
}

function open() {
  if (document.getElementById('settings-drawer')) return;
  settingsOpen = true;

  const overlay = document.createElement('div');
  overlay.id        = 'settings-overlay';
  overlay.className = 'settings-overlay';
  overlay.onclick   = close;

  const drawer = document.createElement('div');
  drawer.id        = 'settings-drawer';
  drawer.className = 'settings-drawer';

  // Header
  const header = document.createElement('div');
  header.className = 'settings-header';

  const heading = document.createElement('h2');
  heading.textContent = 'Settings';
  header.appendChild(heading);

  const closeBtn = document.createElement('button');
  closeBtn.className   = 'modal-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.onclick     = close;
  header.appendChild(closeBtn);

  drawer.appendChild(header);

  // Body
  const body = document.createElement('div');
  body.className = 'settings-body';

  // Appearance section
  body.appendChild(makeSection('Appearance', [
    makeRow('Theme', makeSelect(
      'set-theme',
      [['dark', 'Dark'], ['light', 'Light'], ['auto', 'System']],
      state.settings.theme,
      (value) => { state.settings.theme = value; applyTheme(); saveSettings(); }
    )),
    makeRow('Font Size', makeRange(
      'set-fontsize',
      12, 20,
      state.settings.fontSize,
      (value) => {
        state.settings.fontSize = value;
        document.documentElement.style.fontSize = value + 'px';
        saveSettings();
      }
    )),
  ]));

  // Chat section
  body.appendChild(makeSection('Chat', [
    makeRow('Send on Enter', makeToggle(
      'set-enter',
      state.settings.sendOnEnter,
      (value) => { state.settings.sendOnEnter = value; saveSettings(); }
    )),
    makeRow('Show tool cards', makeToggle(
      'set-toolcards',
      state.settings.showToolCards,
      (value) => { state.settings.showToolCards = value; saveSettings(); }
    )),
    makeRow('Show streaming stats', makeToggle(
      'set-stats',
      state.settings.showStreamStats,
      (value) => { state.settings.showStreamStats = value; saveSettings(); }
    )),
  ]));

  // Data section
  const exportBtn = document.createElement('button');
  exportBtn.className   = 'settings-btn';
  exportBtn.textContent = 'Export JSON';
  exportBtn.onclick     = async () => {
    try {
      const { authFetch } = await import('./utils.js');
      const res  = await authFetch('/api/conversations?limit=500');
      const data = await res.json();

      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url  = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href     = url;
      link.download = 'ailab-export-' + new Date().toISOString().split('T')[0] + '.json';
      link.click();
      URL.revokeObjectURL(url);

      toast('Exported', 'success');
    } catch (e) {
      toast('Export failed', 'error');
    }
  };

  const clearBtn = document.createElement('button');
  clearBtn.className   = 'settings-btn danger';
  clearBtn.textContent = 'Clear Local Data';
  clearBtn.onclick     = () => {
    if (!confirm('Clear local settings?')) return;
    const jwt = localStorage.getItem('dartboard-jwt');
    localStorage.clear();
    if (jwt) localStorage.setItem('dartboard-jwt', jwt);
    toast('Cleared', 'info');
  };

  body.appendChild(makeSection('Data', [
    makeRow('Export all chats', exportBtn),
    makeRow('Clear all data', clearBtn),
  ]));

  // Footer
  const footer = document.createElement('p');
  footer.className   = 'settings-footer-text';
  footer.textContent = "Jamal's AI Lab v2.0 \u2014 Press ? for shortcuts";
  body.appendChild(footer);

  drawer.appendChild(body);
  document.body.appendChild(overlay);
  document.body.appendChild(drawer);

  requestAnimationFrame(() => {
    overlay.classList.add('visible');
    drawer.classList.add('open');
  });
}

function close() {
  const overlay = document.getElementById('settings-overlay');
  const drawer  = document.getElementById('settings-drawer');

  if (overlay) {
    overlay.classList.remove('visible');
    setTimeout(() => overlay.remove(), 200);
  }
  if (drawer) {
    drawer.classList.remove('open');
    setTimeout(() => drawer.remove(), 200);
  }

  settingsOpen = false;
}

// ── DOM Helpers ──────────────────────────────────────────────────────────────

function makeSection(title, rows) {
  const section = document.createElement('div');
  section.className = 'settings-section';

  const heading = document.createElement('h3');
  heading.textContent = title;
  section.appendChild(heading);

  for (const row of rows) section.appendChild(row);

  return section;
}

function makeRow(label, control) {
  const row = document.createElement('div');
  row.className = 'setting-row';

  const lbl = document.createElement('label');
  lbl.textContent = label;

  row.appendChild(lbl);
  row.appendChild(control);

  return row;
}

function makeSelect(id, options, currentValue, onChange) {
  const select = document.createElement('select');
  select.id = id;

  for (const [value, label] of options) {
    const opt = document.createElement('option');
    opt.value       = value;
    opt.textContent = label;
    if (value === currentValue) opt.selected = true;
    select.appendChild(opt);
  }

  select.onchange = () => onChange(select.value);

  return select;
}

function makeRange(id, min, max, currentValue, onChange) {
  const wrap = document.createElement('div');
  wrap.className = 'setting-range';

  const input = document.createElement('input');
  input.type  = 'range';
  input.id    = id;
  input.min   = min;
  input.max   = max;
  input.value = currentValue;

  const display = document.createElement('span');
  display.textContent = currentValue + 'px';

  input.oninput = () => {
    display.textContent = input.value + 'px';
    onChange(parseInt(input.value));
  };

  wrap.appendChild(input);
  wrap.appendChild(display);

  return wrap;
}

function makeToggle(id, checked, onChange) {
  const label = document.createElement('label');
  label.className = 'toggle';

  const input = document.createElement('input');
  input.type    = 'checkbox';
  input.id      = id;
  input.checked = checked;

  const slider = document.createElement('span');
  slider.className = 'toggle-slider';

  input.onchange = () => onChange(input.checked);

  label.appendChild(input);
  label.appendChild(slider);

  return label;
}

// ── Theme Application ────────────────────────────────────────────────────────

export function applyTheme() {
  const theme    = state.settings.theme;
  const resolved = theme === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;

  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.style.fontSize = state.settings.fontSize + 'px';

  // Swap highlight.js stylesheet to match
  const hljsLink = document.querySelector('link[href*="highlight.js"]');
  if (hljsLink) {
    hljsLink.href = resolved === 'light'
      ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css'
      : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';
  }
}
