'use strict';
import { on } from './state.js';
import { authFetch, esc } from './utils.js';

let statusOpen = false, refreshInt = null;

export function initStatus() { on('status:toggle', toggle); }

function toggle() { statusOpen ? closeS() : openS(); }

async function openS() {
  if (document.getElementById('status-panel')) return;
  statusOpen = true;
  const ov = document.createElement('div'); ov.id = 'status-overlay'; ov.className = 'modal-overlay';
  ov.onclick = (e) => { if (e.target === ov) closeS(); };
  const panel = document.createElement('div'); panel.id = 'status-panel'; panel.className = 'status-panel';
  const hdr = document.createElement('div'); hdr.className = 'status-header';
  const h2 = document.createElement('h2'); h2.textContent = 'System Status'; hdr.appendChild(h2);
  const cls = document.createElement('button'); cls.className = 'modal-close'; cls.textContent = '\u00d7'; cls.onclick = closeS; hdr.appendChild(cls);
  panel.appendChild(hdr);
  const body = document.createElement('div'); body.id = 'status-body'; body.className = 'status-body';
  body.textContent = 'Loading system status...'; body.style.padding = '24px'; body.style.textAlign = 'center';
  panel.appendChild(body); ov.appendChild(panel); document.body.appendChild(ov);
  requestAnimationFrame(() => ov.classList.add('visible'));
  await refresh(); refreshInt = setInterval(refresh, 10000);
}

function closeS() {
  const ov = document.getElementById('status-overlay');
  if (ov) { ov.classList.remove('visible'); setTimeout(() => ov.remove(), 200); }
  if (refreshInt) { clearInterval(refreshInt); refreshInt = null; }
  statusOpen = false;
}

async function refresh() {
  const body = document.getElementById('status-body'); if (!body) return;
  try {
    const [hR, tR, mR, cR] = await Promise.allSettled([authFetch('/health'), authFetch('/api/tools'), authFetch('/api/models'), authFetch('/api/image/status')]);
    const h = hR.status === 'fulfilled' ? await hR.value.json() : null;
    const t = tR.status === 'fulfilled' ? await tR.value.json() : null;
    const m = mR.status === 'fulfilled' ? await mR.value.json() : null;
    const c = cR.status === 'fulfilled' ? await cR.value.json() : null;
    const models = m ? m.models || [] : []; const loaded = models.filter(x => x.state === 'loaded');
    body.textContent = ''; body.style.padding = '16px'; body.style.textAlign = '';
    body.appendChild(makeCard('Services', h && h.ok ? 'healthy' : 'error', [
      ['Backend', h && h.ok ? 'Online' : 'Offline', h && h.ok], ['LM Studio', h ? h.lm_studio || 'N/A' : 'N/A', !!h],
      ['MCP Server', h ? h.mcp || 'N/A' : 'N/A', !!h], ['ComfyUI', c && c.ok ? 'Ready' : (c ? c.error || 'Offline' : 'Offline'), c && c.ok],
    ]));
    body.appendChild(makeCard('Models', loaded.length > 0 ? 'healthy' : 'warning', [
      ['Available', String(models.length), true], ['Loaded', String(loaded.length), loaded.length > 0],
      ...loaded.map(x => [x.id, x.quantization || 'active', true]),
    ]));
    body.appendChild(makeCard('MCP Tools', t ? 'healthy' : 'error', [['Total tools', String(t ? t.count : 0), !!t]]));
    if (c && c.gpu) body.appendChild(makeCard('GPU', 'healthy', [['Device', c.gpu, true]]));
  } catch (e) { body.textContent = 'Failed to load: ' + e.message; }
}

function makeCard(title, health, metrics) {
  const card = document.createElement('div'); card.className = 'status-card status-' + health;
  const dot = health === 'healthy' ? '\uD83D\uDFE2' : health === 'warning' ? '\uD83D\uDFE1' : '\uD83D\uDD34';
  const hdr = document.createElement('div'); hdr.className = 'status-card-header'; hdr.textContent = dot + ' ' + title; card.appendChild(hdr);
  const body = document.createElement('div'); body.className = 'status-card-body';
  for (const [label, value, ok] of metrics) {
    const row = document.createElement('div'); row.className = 'status-metric';
    const l = document.createElement('span'); l.textContent = label;
    const v = document.createElement('span'); v.className = 'status-badge ' + (ok ? 'ok' : 'err'); v.textContent = value;
    row.appendChild(l); row.appendChild(v); body.appendChild(row);
  }
  card.appendChild(body); return card;
}
