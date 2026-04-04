'use strict';
import { esc } from './utils.js';

export function makeToolCard(tc, status) {
  const card = document.createElement('div');
  card.className = 'tool-card';
  card.dataset.toolName = tc.name;
  card.dataset.toolId = tc.id || '';
  const hdr = document.createElement('div');
  hdr.className = 'tool-header';
  const icon = document.createElement('span');
  icon.className = 'tool-icon'; icon.textContent = '\u2699';
  const name = document.createElement('span');
  name.className = 'tool-name'; name.textContent = tc.name;
  const statusEl = document.createElement('span');
  statusEl.className = 'tool-status ' + status;
  statusEl.textContent = status === 'running' ? 'running\u2026' : status;
  hdr.appendChild(icon); hdr.appendChild(name); hdr.appendChild(statusEl);
  hdr.onclick = () => { const b = card.querySelector('.tool-body'); if (b) b.classList.toggle('open'); };
  const body = document.createElement('div');
  body.className = 'tool-body';
  let args = tc.arguments || '{}';
  try { args = JSON.stringify(JSON.parse(args), null, 2); } catch {}
  const pre = document.createElement('pre');
  const code = document.createElement('code');
  code.textContent = args;
  pre.appendChild(code); body.appendChild(pre);
  card.appendChild(hdr); card.appendChild(body);
  return card;
}

export function createThinkingCard() {
  const c = document.createElement('div'); c.className = 'thinking-card';
  const h = document.createElement('div'); h.className = 'thinking-header';
  const icon = document.createElement('span');
  icon.className = 'thinking-icon'; icon.textContent = '\uD83D\uDCAD';
  const label = document.createElement('span');
  label.className = 'thinking-label'; label.textContent = 'Thinking...';
  h.appendChild(icon); h.appendChild(label);
  h.onclick = () => { const b = c.querySelector('.thinking-body'); if (b) b.classList.toggle('open'); };
  const b = document.createElement('div'); b.className = 'thinking-body';
  c.appendChild(h); c.appendChild(b);
  return c;
}
