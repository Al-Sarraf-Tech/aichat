'use strict';
import { state, saveSettings, on } from './state.js';
import { toast } from './toasts.js';

let settingsOpen = false;

export function initSettings() { on('settings:toggle', toggle); on('settings:close', close); }

function toggle() { settingsOpen ? close() : open(); }

function open() {
  if (document.getElementById('settings-drawer')) return;
  settingsOpen = true;
  const ov = document.createElement('div'); ov.id = 'settings-overlay'; ov.className = 'settings-overlay'; ov.onclick = close;
  const d = document.createElement('div'); d.id = 'settings-drawer'; d.className = 'settings-drawer';
  // Build header
  const hdr = document.createElement('div'); hdr.className = 'settings-header';
  const h2 = document.createElement('h2'); h2.textContent = 'Settings'; hdr.appendChild(h2);
  const cls = document.createElement('button'); cls.className = 'modal-close'; cls.textContent = '\u00d7'; cls.onclick = close; hdr.appendChild(cls);
  d.appendChild(hdr);
  // Body
  const body = document.createElement('div'); body.className = 'settings-body';
  // Theme section
  body.appendChild(makeSection('Appearance', [
    makeRow('Theme', makeSelect('set-theme', [['dark','Dark'],['light','Light'],['auto','System']], state.settings.theme, (v) => { state.settings.theme = v; applyTheme(); saveSettings(); })),
    makeRow('Font Size', makeRange('set-fontsize', 12, 20, state.settings.fontSize, (v) => { state.settings.fontSize = v; document.documentElement.style.fontSize = v+'px'; saveSettings(); })),
  ]));
  body.appendChild(makeSection('Chat', [
    makeRow('Send on Enter', makeToggle('set-enter', state.settings.sendOnEnter, (v) => { state.settings.sendOnEnter = v; saveSettings(); })),
    makeRow('Show tool cards', makeToggle('set-toolcards', state.settings.showToolCards, (v) => { state.settings.showToolCards = v; saveSettings(); })),
    makeRow('Show streaming stats', makeToggle('set-stats', state.settings.showStreamStats, (v) => { state.settings.showStreamStats = v; saveSettings(); })),
  ]));
  // Data section
  const exportBtn = document.createElement('button'); exportBtn.className = 'settings-btn'; exportBtn.textContent = 'Export JSON';
  exportBtn.onclick = async () => {
    try { const { authFetch } = await import('./utils.js'); const res = await authFetch('/api/conversations?limit=500'); const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'ailab-export-' + new Date().toISOString().split('T')[0] + '.json'; a.click(); URL.revokeObjectURL(url);
    toast('Exported', 'success'); } catch (e) { toast('Export failed', 'error'); }
  };
  const clearBtn = document.createElement('button'); clearBtn.className = 'settings-btn danger'; clearBtn.textContent = 'Clear Local Data';
  clearBtn.onclick = () => { if (!confirm('Clear local settings?')) return; const jwt = localStorage.getItem('dartboard-jwt'); localStorage.clear(); if (jwt) localStorage.setItem('dartboard-jwt', jwt); toast('Cleared', 'info'); };
  body.appendChild(makeSection('Data', [makeRow('Export all chats', exportBtn), makeRow('Clear all data', clearBtn)]));
  // Footer
  const footer = document.createElement('p'); footer.className = 'settings-footer-text'; footer.textContent = "Jamal's AI Lab v2.0 \u2014 Press ? for shortcuts";
  body.appendChild(footer);
  d.appendChild(body);
  document.body.appendChild(ov); document.body.appendChild(d);
  requestAnimationFrame(() => { ov.classList.add('visible'); d.classList.add('open'); });
}

function close() {
  const ov = document.getElementById('settings-overlay'); const d = document.getElementById('settings-drawer');
  if (ov) { ov.classList.remove('visible'); setTimeout(() => ov.remove(), 200); }
  if (d) { d.classList.remove('open'); setTimeout(() => d.remove(), 200); }
  settingsOpen = false;
}

function makeSection(title, rows) {
  const sec = document.createElement('div'); sec.className = 'settings-section';
  const h3 = document.createElement('h3'); h3.textContent = title; sec.appendChild(h3);
  for (const r of rows) sec.appendChild(r); return sec;
}

function makeRow(label, control) {
  const row = document.createElement('div'); row.className = 'setting-row';
  const lbl = document.createElement('label'); lbl.textContent = label; row.appendChild(lbl); row.appendChild(control); return row;
}

function makeSelect(id, options, value, onChange) {
  const sel = document.createElement('select'); sel.id = id;
  for (const [v, l] of options) { const opt = document.createElement('option'); opt.value = v; opt.textContent = l; if (v === value) opt.selected = true; sel.appendChild(opt); }
  sel.onchange = () => onChange(sel.value); return sel;
}

function makeRange(id, min, max, value, onChange) {
  const wrap = document.createElement('div'); wrap.className = 'setting-range';
  const input = document.createElement('input'); input.type = 'range'; input.id = id; input.min = min; input.max = max; input.value = value;
  const val = document.createElement('span'); val.textContent = value + 'px';
  input.oninput = () => { val.textContent = input.value + 'px'; onChange(parseInt(input.value)); };
  wrap.appendChild(input); wrap.appendChild(val); return wrap;
}

function makeToggle(id, checked, onChange) {
  const lbl = document.createElement('label'); lbl.className = 'toggle';
  const input = document.createElement('input'); input.type = 'checkbox'; input.id = id; input.checked = checked;
  const slider = document.createElement('span'); slider.className = 'toggle-slider';
  input.onchange = () => onChange(input.checked);
  lbl.appendChild(input); lbl.appendChild(slider); return lbl;
}

export function applyTheme() {
  const theme = state.settings.theme;
  const resolved = theme === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.style.fontSize = state.settings.fontSize + 'px';
  // Swap highlight.js theme to match
  const hljs = document.querySelector('link[href*="highlight.js"]');
  if (hljs) hljs.href = resolved === 'light'
    ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css'
    : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';
}
