'use strict';
// (no external utils needed)

export function makeToolCard(toolCall, status) {
  const card = document.createElement('div');
  card.className = 'tool-card';
  card.dataset.toolName = toolCall.name;
  card.dataset.toolId = toolCall.id || '';

  const header = document.createElement('div');
  header.className = 'tool-header';

  const icon = document.createElement('span');
  icon.className = 'tool-icon';
  icon.textContent = '\u2699';

  const nameEl = document.createElement('span');
  nameEl.className = 'tool-name';
  nameEl.textContent = toolCall.name;

  const statusEl = document.createElement('span');
  statusEl.className = 'tool-status ' + status;
  statusEl.textContent = status === 'running' ? 'running\u2026' : status;

  header.appendChild(icon);
  header.appendChild(nameEl);
  header.appendChild(statusEl);
  header.onclick = () => {
    const body = card.querySelector('.tool-body');
    if (body) body.classList.toggle('open');
  };

  const body = document.createElement('div');
  body.className = 'tool-body';

  let args = toolCall.arguments || '{}';
  try { args = JSON.stringify(JSON.parse(args), null, 2); } catch {}

  const pre = document.createElement('pre');
  const code = document.createElement('code');
  code.textContent = args;
  pre.appendChild(code);
  body.appendChild(pre);

  card.appendChild(header);
  card.appendChild(body);
  return card;
}

export function createThinkingCard() {
  const card = document.createElement('div');
  card.className = 'thinking-card';

  const header = document.createElement('div');
  header.className = 'thinking-header';

  const icon = document.createElement('span');
  icon.className = 'thinking-icon';
  icon.textContent = '\uD83D\uDCAD';

  const label = document.createElement('span');
  label.className = 'thinking-label';
  label.textContent = 'Thinking...';

  header.appendChild(icon);
  header.appendChild(label);
  header.onclick = () => {
    const body = card.querySelector('.thinking-body');
    if (body) body.classList.toggle('open');
  };

  const body = document.createElement('div');
  body.className = 'thinking-body';

  card.appendChild(header);
  card.appendChild(body);
  return card;
}
