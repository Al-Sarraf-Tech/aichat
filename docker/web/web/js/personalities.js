'use strict';
import { state } from './state.js';
import { authFetch, esc } from './utils.js';

export async function loadPersonalities() {
  try {
    const url = state.selectedModel ? '/api/personalities?model=' + encodeURIComponent(state.selectedModel) : '/api/personalities';
    const res = await authFetch(url); const data = await res.json();
    state.allPersonalities = data.personalities || [];
  } catch (e) { console.warn('loadPersonalities:', e); }
  const stored = localStorage.getItem('dartboard-personality');
  if (stored) { try { const p = JSON.parse(stored); if (p.id) state.selectedPersonality = p; } catch {} }
  const storedCustom = localStorage.getItem('dartboard-custom-prompt');
  if (storedCustom) state.customSystemPrompt = storedCustom;
  if (state.selectedPersonality.id && !state.allPersonalities.some(p => p.id === state.selectedPersonality.id)) {
    const general = state.allPersonalities.find(p => p.id === 'general');
    if (general) { state.selectedPersonality = { id: general.id, name: general.name, icon: general.icon }; localStorage.setItem('dartboard-personality', JSON.stringify(state.selectedPersonality)); }
  }
  updatePersonalityDisplay();
}

export function updatePersonalityDisplay() {
  const iconEl = document.getElementById('personality-icon');
  const nameEl = document.getElementById('personality-name');
  if (iconEl && nameEl) {
    if (state.customSystemPrompt) { iconEl.textContent = '\u270D'; nameEl.textContent = 'Custom Prompt'; }
    else { iconEl.textContent = state.selectedPersonality.icon || '\u{1F9E0}'; nameEl.textContent = state.selectedPersonality.name || 'General Assistant'; }
  }
}

export function togglePersonalityModal() {
  const modal = document.getElementById('personality-modal');
  if (modal.classList.contains('hidden')) { renderGrid(); modal.classList.remove('hidden'); }
  else modal.classList.add('hidden');
}

export function closePersonalityModal() { document.getElementById('personality-modal').classList.add('hidden'); }

function renderGrid() {
  const container = document.getElementById('personality-categories'); container.textContent = '';
  const groups = {};
  for (const p of state.allPersonalities) { if (!groups[p.category]) groups[p.category] = []; groups[p.category].push(p); }
  for (const [cat, items] of Object.entries(groups)) {
    const sec = document.createElement('div'); sec.className = 'personality-section';
    const h = document.createElement('h3'); h.className = 'personality-category'; h.textContent = cat; sec.appendChild(h);
    const grid = document.createElement('div'); grid.className = 'personality-grid';
    for (const p of items) {
      const card = document.createElement('button');
      card.className = 'personality-card' + (!state.customSystemPrompt && p.id === state.selectedPersonality.id ? ' active' : '');
      card.dataset.id = p.id; card.onclick = () => pickP(p);
      const ic = document.createElement('span'); ic.className = 'p-icon'; ic.textContent = p.icon;
      const nm = document.createElement('span'); nm.className = 'p-name'; nm.textContent = p.name;
      const ds = document.createElement('span'); ds.className = 'p-desc'; ds.textContent = p.description;
      card.appendChild(ic); card.appendChild(nm); card.appendChild(ds); grid.appendChild(card);
    }
    sec.appendChild(grid); container.appendChild(sec);
  }
}

function pickP(p) {
  state.selectedPersonality = { id: p.id, name: p.name, icon: p.icon };
  state.customSystemPrompt = null;
  localStorage.setItem('dartboard-personality', JSON.stringify(state.selectedPersonality));
  localStorage.removeItem('dartboard-custom-prompt');
  updatePersonalityDisplay(); closePersonalityModal();
}

export function filterPersonalities() {
  const q = document.getElementById('personality-search').value.toLowerCase();
  document.querySelectorAll('.personality-card').forEach(card => {
    const t = (card.querySelector('.p-name')?.textContent || '') + ' ' + (card.querySelector('.p-desc')?.textContent || '');
    card.style.display = t.toLowerCase().includes(q) ? '' : 'none';
  });
  document.querySelectorAll('.personality-section').forEach(sec => {
    sec.style.display = sec.querySelector('.personality-card:not([style*="display: none"])') ? '' : 'none';
  });
}

export function toggleCustomPrompt() {
  const area = document.getElementById('custom-prompt-area'); area.classList.toggle('hidden');
  if (!area.classList.contains('hidden')) { const i = document.getElementById('custom-prompt-input'); if (state.customSystemPrompt) i.value = state.customSystemPrompt; i.focus(); }
}

export function applyCustomPrompt() {
  const i = document.getElementById('custom-prompt-input'); const p = i.value.trim(); if (!p) return;
  state.customSystemPrompt = p; localStorage.setItem('dartboard-custom-prompt', p);
  updatePersonalityDisplay(); closePersonalityModal();
}
