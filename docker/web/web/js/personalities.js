'use strict';
import { state } from './state.js';
import { authFetch } from './utils.js';

export async function loadPersonalities() {
  try {
    const url = state.selectedModel
      ? '/api/personalities?model=' + encodeURIComponent(state.selectedModel)
      : '/api/personalities';
    const res  = await authFetch(url);
    const data = await res.json();
    state.allPersonalities = data.personalities || [];
  } catch (e) {
    console.warn('loadPersonalities:', e);
  }

  const stored = localStorage.getItem('dartboard-personality');
  if (stored) {
    try {
      const parsed = JSON.parse(stored);
      if (parsed.id) state.selectedPersonality = parsed;
    } catch {}
  }

  const storedCustom = localStorage.getItem('dartboard-custom-prompt');
  if (storedCustom) state.customSystemPrompt = storedCustom;

  if (
    state.selectedPersonality.id &&
    !state.allPersonalities.some(p => p.id === state.selectedPersonality.id)
  ) {
    const general = state.allPersonalities.find(p => p.id === 'general');
    if (general) {
      state.selectedPersonality = { id: general.id, name: general.name, icon: general.icon };
      localStorage.setItem('dartboard-personality', JSON.stringify(state.selectedPersonality));
    }
  }

  updatePersonalityDisplay();
}

export function updatePersonalityDisplay() {
  const iconEl = document.getElementById('personality-icon');
  const nameEl = document.getElementById('personality-name');

  if (iconEl && nameEl) {
    if (state.customSystemPrompt) {
      iconEl.textContent = '\u270D';
      nameEl.textContent = 'Custom Prompt';
    } else {
      iconEl.textContent = state.selectedPersonality.icon || '\u{1F9E0}';
      nameEl.textContent = state.selectedPersonality.name || 'General Assistant';
    }
  }
}

export function togglePersonalityModal() {
  const modal = document.getElementById('personality-modal');

  if (modal.classList.contains('hidden')) {
    renderGrid();
    modal.classList.remove('hidden');
  } else {
    modal.classList.add('hidden');
  }
}

export function closePersonalityModal() {
  document.getElementById('personality-modal').classList.add('hidden');
}

function renderGrid() {
  const container = document.getElementById('personality-categories');
  container.textContent = '';

  const groups = {};
  for (const p of state.allPersonalities) {
    if (!groups[p.category]) groups[p.category] = [];
    groups[p.category].push(p);
  }

  for (const [category, items] of Object.entries(groups)) {
    const section = document.createElement('div');
    section.className = 'personality-section';

    const heading = document.createElement('h3');
    heading.className = 'personality-category';
    heading.textContent = category;
    section.appendChild(heading);

    const grid = document.createElement('div');
    grid.className = 'personality-grid';

    for (const p of items) {
      const card = document.createElement('button');
      card.className = 'personality-card' +
        (!state.customSystemPrompt && p.id === state.selectedPersonality.id ? ' active' : '');
      card.dataset.id = p.id;
      card.onclick = () => pickPersonality(p);

      const iconSpan = document.createElement('span');
      iconSpan.className = 'p-icon';
      iconSpan.textContent = p.icon;

      const nameSpan = document.createElement('span');
      nameSpan.className = 'p-name';
      nameSpan.textContent = p.name;

      const descSpan = document.createElement('span');
      descSpan.className = 'p-desc';
      descSpan.textContent = p.description;

      card.appendChild(iconSpan);
      card.appendChild(nameSpan);
      card.appendChild(descSpan);
      grid.appendChild(card);
    }

    section.appendChild(grid);
    container.appendChild(section);
  }
}

function pickPersonality(p) {
  state.selectedPersonality = { id: p.id, name: p.name, icon: p.icon };
  state.customSystemPrompt = null;
  localStorage.setItem('dartboard-personality', JSON.stringify(state.selectedPersonality));
  localStorage.removeItem('dartboard-custom-prompt');
  updatePersonalityDisplay();
  closePersonalityModal();
}

export function filterPersonalities() {
  const query = document.getElementById('personality-search').value.toLowerCase();

  document.querySelectorAll('.personality-card').forEach(card => {
    const text =
      (card.querySelector('.p-name')?.textContent || '') + ' ' +
      (card.querySelector('.p-desc')?.textContent || '');
    card.style.display = text.toLowerCase().includes(query) ? '' : 'none';
  });

  document.querySelectorAll('.personality-section').forEach(section => {
    section.style.display =
      section.querySelector('.personality-card:not([style*="display: none"])') ? '' : 'none';
  });
}

export function toggleCustomPrompt() {
  const area = document.getElementById('custom-prompt-area');
  area.classList.toggle('hidden');

  if (!area.classList.contains('hidden')) {
    const input = document.getElementById('custom-prompt-input');
    if (state.customSystemPrompt) input.value = state.customSystemPrompt;
    input.focus();
  }
}

export function applyCustomPrompt() {
  const input  = document.getElementById('custom-prompt-input');
  const prompt = input.value.trim();
  if (!prompt) return;

  state.customSystemPrompt = prompt;
  localStorage.setItem('dartboard-custom-prompt', prompt);
  updatePersonalityDisplay();
  closePersonalityModal();
}
