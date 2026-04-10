'use strict';
import { on } from './state.js';
import { authFetch } from './utils.js';

let statusOpen = false;
let refreshInterval = null;

export function initStatus() {
  on('status:toggle', toggle);
}

function toggle() {
  statusOpen ? closeStatus() : openStatus();
}

async function openStatus() {
  if (document.getElementById('status-panel')) return;
  statusOpen = true;

  const overlay = document.createElement('div');
  overlay.id = 'status-overlay';
  overlay.className = 'modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeStatus(); };

  const panel = document.createElement('div');
  panel.id = 'status-panel';
  panel.className = 'status-panel';

  const header = document.createElement('div');
  header.className = 'status-header';

  const heading = document.createElement('h2');
  heading.textContent = 'System Status';
  header.appendChild(heading);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'modal-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.onclick = closeStatus;
  header.appendChild(closeBtn);

  panel.appendChild(header);

  const body = document.createElement('div');
  body.id = 'status-body';
  body.className = 'status-body';
  body.textContent = 'Loading system status...';
  body.style.padding = '24px';
  body.style.textAlign = 'center';

  panel.appendChild(body);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  requestAnimationFrame(() => overlay.classList.add('visible'));

  await refresh();
  refreshInterval = setInterval(refresh, 10000);
}

function closeStatus() {
  const overlay = document.getElementById('status-overlay');
  if (overlay) {
    overlay.classList.remove('visible');
    setTimeout(() => overlay.remove(), 200);
  }
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
  statusOpen = false;
}

async function refresh() {
  const body = document.getElementById('status-body');
  if (!body) return;

  try {
    const [healthRes, toolsRes, modelsRes, imageRes] = await Promise.allSettled([
      authFetch('/health'),
      authFetch('/api/tools'),
      authFetch('/api/models'),
      authFetch('/api/image/status'),
    ]);

    const health = healthRes.status === 'fulfilled' ? await healthRes.value.json() : null;
    const tools  = toolsRes.status === 'fulfilled'  ? await toolsRes.value.json()  : null;
    const models = modelsRes.status === 'fulfilled'  ? await modelsRes.value.json() : null;
    const image  = imageRes.status === 'fulfilled'   ? await imageRes.value.json()  : null;

    const modelList = models ? models.models || [] : [];
    const loadedModels = modelList.filter(m => m.state === 'loaded');

    body.textContent = '';
    body.style.padding = '16px';
    body.style.textAlign = '';

    body.appendChild(makeCard('Services', health && health.ok ? 'healthy' : 'error', [
      ['Backend',    health && health.ok ? 'Online' : 'Offline',                          health && health.ok],
      ['LM Studio',  health ? health.lm_studio || 'N/A' : 'N/A',                          !!health],
      ['MCP Server', health ? health.mcp || 'N/A' : 'N/A',                                !!health],
      ['ComfyUI',    image && image.ok ? 'Ready' : (image ? image.error || 'Offline' : 'Offline'), image && image.ok],
    ]));

    body.appendChild(makeCard('Models', loadedModels.length > 0 ? 'healthy' : 'warning', [
      ['Available', String(modelList.length),    true],
      ['Loaded',    String(loadedModels.length), loadedModels.length > 0],
      ...loadedModels.map(m => [m.id, m.quantization || 'active', true]),
    ]));

    body.appendChild(makeCard('MCP Tools', tools ? 'healthy' : 'error', [
      ['Total tools', String(tools ? tools.count : 0), !!tools],
    ]));

    if (image && image.gpu) {
      body.appendChild(makeCard('GPU', 'healthy', [
        ['Device', image.gpu, true],
      ]));
    }
  } catch (err) {
    body.textContent = 'Failed to load: ' + err.message;
  }
}

function makeCard(title, health, metrics) {
  const card = document.createElement('div');
  card.className = 'status-card status-' + health;

  const dot = health === 'healthy' ? '\uD83D\uDFE2'
             : health === 'warning' ? '\uD83D\uDFE1'
             : '\uD83D\uDD34';

  const cardHeader = document.createElement('div');
  cardHeader.className = 'status-card-header';
  cardHeader.textContent = dot + ' ' + title;
  card.appendChild(cardHeader);

  const cardBody = document.createElement('div');
  cardBody.className = 'status-card-body';

  for (const [label, value, ok] of metrics) {
    const row = document.createElement('div');
    row.className = 'status-metric';

    const labelEl = document.createElement('span');
    labelEl.textContent = label;

    const valueEl = document.createElement('span');
    valueEl.className = 'status-badge ' + (ok ? 'ok' : 'err');
    valueEl.textContent = value;

    row.appendChild(labelEl);
    row.appendChild(valueEl);
    cardBody.appendChild(row);
  }

  card.appendChild(cardBody);
  return card;
}
