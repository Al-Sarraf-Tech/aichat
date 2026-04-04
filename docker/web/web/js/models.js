'use strict';
import { state, emit } from './state.js';
import { authFetch, esc, formatCtx } from './utils.js';

// ── Cloud Models (OAuth — routed via SSH CLI) ──────────────────
// Format: agent:model:effort — invokes the agent's CLI directly.
export const MODELS = [
  // Claude (Anthropic — OAuth via Claude CLI)
  { id: 'claude:opus:max', name: 'Claude Opus', provider: 'claude', icon: '\uD83D\uDFE3', desc: 'Most capable, max effort — deep reasoning', cost: 'paid', group: 'Anthropic' },
  { id: 'claude:sonnet:high', name: 'Claude Sonnet', provider: 'claude', icon: '\uD83D\uDFE3', desc: 'Best balance of speed and intelligence', cost: 'paid', group: 'Anthropic' },
  { id: 'claude:haiku:high', name: 'Claude Haiku', provider: 'claude', icon: '\uD83D\uDFE3', desc: 'Fast and cost-effective', cost: 'paid', group: 'Anthropic' },
  // Codex / OpenAI (OAuth via Codex CLI)
  { id: 'codex::xhigh', name: 'GPT-5.4 (xHigh)', provider: 'codex', icon: '\uD83D\uDFE2', desc: 'Latest GPT — maximum reasoning', cost: 'paid', group: 'OpenAI' },
  { id: 'codex::high', name: 'GPT-5.4 (High)', provider: 'codex', icon: '\uD83D\uDFE2', desc: 'GPT-5.4 balanced reasoning', cost: 'paid', group: 'OpenAI' },
  { id: 'codex::medium', name: 'GPT-5.4 (Medium)', provider: 'codex', icon: '\uD83D\uDFE2', desc: 'GPT-5.4 fast mode', cost: 'paid', group: 'OpenAI' },
  // Gemini (OAuth via Gemini CLI)
  { id: 'gemini:gemini-2.5-pro', name: 'Gemini 2.5 Pro', provider: 'gemini', icon: '\uD83D\uDD35', desc: 'Best quality, multimodal', cost: 'free', group: 'Google' },
  { id: 'gemini:gemini-2.5-flash', name: 'Gemini 2.5 Flash', provider: 'gemini', icon: '\uD83D\uDD35', desc: 'Fast multimodal', cost: 'free', group: 'Google' },
  { id: 'gemini:gemini-2.0-flash', name: 'Gemini 2.0 Flash', provider: 'gemini', icon: '\uD83D\uDD35', desc: 'Previous gen, very fast', cost: 'free', group: 'Google' },
  // Qwen (local LM Studio on RTX 3090)
  { id: 'qwen', name: 'Qwen 3.5 (RTX 3090)', provider: 'qwen', icon: '\uD83D\uDFE0', desc: 'QA, summarization — local RTX 3090', cost: 'free', group: 'Local' },
];

// API models removed — no cloud API keys configured.
export const API_MODELS = [];

// Model health status cache (populated on dropdown open)
const _modelHealth = {};

export function isCloudModel(id) {
  return id && (MODELS.some(m => m.id === id) || API_MODELS.some(m => m.id === id));
}

export function isApiModel(id) { return id && id.startsWith('api:'); }

// CLI models use these prefixes for MCP routing
const _CLOUD_PREFIXES = ['claude:', 'codex:', 'gemini:'];
export function isCliModel(id) {
  if (!id) return false;
  if (id === 'qwen') return true;
  return _CLOUD_PREFIXES.some(p => id.startsWith(p));
}

// Check model health by probing /api/model-status
export async function checkModelHealth() {
  try {
    const r = await authFetch('/api/model-status');
    const data = await r.json();
    Object.assign(_modelHealth, data.status || {});
  } catch {}
}
export function getModelHealth(provider) { return _modelHealth[provider] || 'unknown'; }

export function getModelCaps(id) {
  if (!id) return { vision: false, tools: false, reasoning: false, embedding: false };
  if (state.validatedCaps[id]) return state.validatedCaps[id];
  const l = id.toLowerCase();
  if (/embed|embedding/.test(l)) return { vision: false, tools: false, reasoning: false, embedding: true };
  const caps = { vision: false, tools: true, reasoning: false, embedding: false };
  if (/\bvl\b|-vl-|vision|4v/.test(l)) caps.vision = true;
  const m = state.availableModels.find(m => m.id === id);
  if (m && m.model_type === 'vlm') caps.vision = true;
  if (/reasoning|qwen3\.5|magistral|think|phi-4/.test(l)) caps.reasoning = true;
  return caps;
}

export function getModelMeta(id) {
  const m = state.availableModels.find(m => m.id === id);
  return { state: m ? m.state || 'unknown' : 'unknown', quant: m ? m.quantization || '' : '', ctx: m ? m.max_context_length || (m.profile ? m.profile.max_tokens : 0) || 0 : 0, type: m ? m.model_type || (m.type === 'vlm' ? 'vlm' : 'llm') : 'llm', toolCount: m && m.profile ? m.profile.tool_count || 9 : 9 };
}

export function capsHTML(id) {
  const c = getModelCaps(id);
  if (c.embedding) return '<span class="cap-badge cap-embed" title="Embedding">&#128202;</span>';
  let h = '';
  if (c.vision) h += '<span class="cap-badge cap-vision" title="Vision">&#128065;&#65039;</span>';
  if (c.reasoning) h += '<span class="cap-badge cap-reason" title="Reasoning">&#129504;</span>';
  if (c.tools) h += '<span class="cap-badge cap-tools" title="Tools">&#128296;</span>';
  else h += '<span class="cap-badge cap-notools" title="No tools">&#128683;</span>';
  return h;
}

export function getToolCount(modelId) {
  if (!modelId) return 0;
  const m = state.availableModels.find(m => m.id === modelId);
  if (!m || !m.profile) return 9;
  return m.profile.tool_count || 9;
}

export async function loadModels() {
  try {
    const res = await authFetch('/api/models');
    const data = await res.json();
    state.availableModels = (data.models || []).filter(m => m.id);
    for (const m of state.availableModels) {
      if (m.validated) {
        state.validatedCaps[m.id] = { chat: m.chat !== false, tools: m.tools !== false, reasoning: m.reasoning || false, embedding: m.embedding || false, vision: m.vision || false, limitation: m.limitation || null };
      }
    }
    const stored = localStorage.getItem('dartboard-model');
    if (stored && (state.availableModels.some(m => m.id === stored) || stored.startsWith('api:') || isCliModel(stored))) state.selectedModel = stored;
    emit('models:loaded');
    emit('conn:status', true);
  } catch (e) { console.error('loadModels:', e); emit('conn:status', false); }
}

export async function loadToolCount() {
  try {
    const res = await authFetch('/api/tools');
    const data = await res.json();
    const el = document.getElementById('tool-count');
    if (el) el.textContent = data.count + ' tools';
  } catch {}
}

export async function pickModel(modelId) {
  if (modelId === state.selectedModel) return;
  state.selectedModel = modelId;
  state.selectedModelReady = false;
  localStorage.setItem('dartboard-model', modelId);
  emit('model:changed');
  if (isCliModel(modelId)) {
    const m = MODELS.find(m => m.id === modelId);
    state.validatedCaps[modelId] = { chat: true, tools: true, reasoning: true, embedding: false, vision: modelId.startsWith('claude:') || modelId.startsWith('gemini:') || modelId.startsWith('codex:'), limitation: null };
    state.selectedModelReady = true;
    emit('model:ready', { text: (m ? m.icon + ' ' + m.name : modelId) + ' ready' });
    return;
  }
  if (isApiModel(modelId)) {
    const m = API_MODELS.find(m => m.id === modelId);
    state.validatedCaps[modelId] = { chat: true, tools: false, reasoning: modelId.includes('o4-') || modelId.includes('opus'), embedding: false, vision: !modelId.includes('o4-'), limitation: null };
    state.selectedModelReady = true;
    emit('model:ready', { text: (m ? m.icon + ' ' + m.name : modelId) + ' ready' });
    return;
  }
  emit('model:loading', { text: 'Loading ' + modelId + '...' });
  try {
    const res = await authFetch('/api/warmup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: modelId }) });
    const data = await res.json();
    state.validatedCaps[modelId] = { chat: data.chat !== false, tools: data.tools !== false, reasoning: data.reasoning || false, embedding: data.embedding || false, vision: false, limitation: data.limitation || null };
    emit('model:changed');
    if (data.status === 'ready') { state.selectedModelReady = true; const cl = []; if (data.chat) cl.push('Chat'); if (data.tools) cl.push('Tools'); if (data.reasoning) cl.push('Reasoning'); emit('model:ready', { text: 'Ready: ' + cl.join(', ') }); }
    else if (data.status === 'limited') { state.selectedModelReady = true; emit('model:ready', { text: data.limitation || 'Limited' }); }
    else { emit('model:ready', { text: data.message || 'Could not load' }); }
  } catch (e) { emit('model:ready', { text: 'Connection failed: ' + e.message }); }
  if (state.currentConvId) { try { await authFetch('/api/conversations/' + state.currentConvId, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: modelId }) }); } catch {} }
}

export function renderModelMenu() {
  const menu = document.getElementById('model-menu');
  menu.textContent = '';

  // ── Cloud Models (OAuth) section ─────────────────────────────────
  const cloudHdr = document.createElement('div'); cloudHdr.className = 'dropdown-section-header api-header';
  cloudHdr.textContent = '\uD83D\uDD11 Cloud Models \u2014 OAuth';
  menu.appendChild(cloudHdr);

  const byGroup = {};
  for (const m of MODELS) { if (!byGroup[m.group]) byGroup[m.group] = []; byGroup[m.group].push(m); }
  for (const [group, models] of Object.entries(byGroup)) {
    const groupLabel = document.createElement('div');
    groupLabel.style.cssText = 'padding:4px 12px;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;';
    groupLabel.textContent = group;
    menu.appendChild(groupLabel);
    for (const m of models) {
      const btn = document.createElement('button');
      btn.className = 'dropdown-item api-item' + (m.id === state.selectedModel ? ' active' : '');
      const row = document.createElement('div'); row.className = 'model-row';
      const ic = document.createElement('span'); ic.className = 'model-icon'; ic.textContent = m.icon;
      const nm = document.createElement('span'); nm.className = 'model-id'; nm.textContent = m.name;
      const costTag = document.createElement('span'); costTag.className = 'meta-tag cost-' + (m.cost === 'free' ? 'free' : 'paid'); costTag.textContent = m.cost.toUpperCase();
      const chk = document.createElement('span'); chk.className = 'check'; chk.textContent = m.id === state.selectedModel ? '\u2713' : '';
      row.appendChild(ic); row.appendChild(nm); row.appendChild(costTag); row.appendChild(chk); btn.appendChild(row);
      const meta = document.createElement('div'); meta.className = 'model-meta';
      const tag = document.createElement('span'); tag.className = 'meta-tag'; tag.textContent = m.desc;
      meta.appendChild(tag); btn.appendChild(meta);
      btn.onclick = (e) => { e.stopPropagation(); pickModel(m.id); menu.classList.add('hidden'); };
      menu.appendChild(btn);
    }
  }

  // ── API Models (direct API key) section ──────────────────────────
  const apiHdr = document.createElement('div'); apiHdr.className = 'dropdown-section-header';
  apiHdr.textContent = '\uD83D\uDCE1 API Models \u2014 Direct Key';
  menu.appendChild(apiHdr);
  const apiByGroup = {};
  for (const m of API_MODELS) { if (!apiByGroup[m.group]) apiByGroup[m.group] = []; apiByGroup[m.group].push(m); }
  for (const [group, models] of Object.entries(apiByGroup)) {
    const groupLabel = document.createElement('div');
    groupLabel.style.cssText = 'padding:4px 12px;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;';
    groupLabel.textContent = group;
    menu.appendChild(groupLabel);
    for (const m of models) {
      const btn = document.createElement('button');
      btn.className = 'dropdown-item api-item' + (m.id === state.selectedModel ? ' active' : '');
      const row = document.createElement('div'); row.className = 'model-row';
      const ic = document.createElement('span'); ic.className = 'model-icon'; ic.textContent = m.icon;
      const nm = document.createElement('span'); nm.className = 'model-id'; nm.textContent = m.name;
      const costTag = document.createElement('span'); costTag.className = 'meta-tag cost-paid'; costTag.textContent = 'API KEY';
      const chk = document.createElement('span'); chk.className = 'check'; chk.textContent = m.id === state.selectedModel ? '\u2713' : '';
      row.appendChild(ic); row.appendChild(nm); row.appendChild(costTag); row.appendChild(chk); btn.appendChild(row);
      const meta = document.createElement('div'); meta.className = 'model-meta';
      const tag = document.createElement('span'); tag.className = 'meta-tag'; tag.textContent = m.desc;
      meta.appendChild(tag); btn.appendChild(meta);
      btn.onclick = (e) => { e.stopPropagation(); pickModel(m.id); menu.classList.add('hidden'); };
      menu.appendChild(btn);
    }
  }

  // ── Local Models section ────────────────────────────────────────
  const divider = document.createElement('div'); divider.className = 'dropdown-section-header';
  divider.textContent = '\uD83D\uDCBB Local Models (LM Studio)';
  menu.appendChild(divider);
  if (state.availableModels.length === 0) { const empty = document.createElement('div'); empty.className = 'dropdown-empty'; empty.textContent = 'No models found. Is LM Studio running?'; menu.appendChild(empty); return; }
  const sorted = [...state.availableModels].sort((a, b) => { const al = a.state === 'loaded' ? 0 : 1; const bl = b.state === 'loaded' ? 0 : 1; return al !== bl ? al - bl : (a.id || '').localeCompare(b.id || ''); });
  for (const m of sorted) {
    if ((m.model_type || m.type) === 'embeddings') continue;
    const meta = getModelMeta(m.id);
    const btn = document.createElement('button'); btn.className = 'dropdown-item' + (m.id === state.selectedModel ? ' active' : '');
    const row = document.createElement('div'); row.className = 'model-row';
    const dot = document.createElement('span'); dot.className = 'state-dot ' + (meta.state === 'loaded' ? 'loaded' : 'unloaded'); dot.title = meta.state;
    const nm = document.createElement('span'); nm.className = 'model-id'; nm.textContent = m.id;
    const capsSpan = document.createElement('span'); capsSpan.className = 'model-caps-row'; capsSpan.innerHTML = capsHTML(m.id);
    const chk = document.createElement('span'); chk.className = 'check'; chk.textContent = m.id === state.selectedModel ? '\u2713' : '';
    row.appendChild(dot); row.appendChild(nm); row.appendChild(capsSpan); row.appendChild(chk); btn.appendChild(row);
    const metaRow = document.createElement('div'); metaRow.className = 'model-meta';
    const tags = []; if (meta.quant) tags.push(meta.quant); if (meta.ctx) tags.push(formatCtx(meta.ctx) + ' ctx'); tags.push(meta.type.toUpperCase()); tags.push(meta.toolCount + ' tools');
    for (const t of tags) { const s = document.createElement('span'); s.className = 'meta-tag'; s.textContent = t; metaRow.appendChild(s); }
    btn.appendChild(metaRow);
    btn.onclick = (e) => { e.stopPropagation(); pickModel(m.id); menu.classList.add('hidden'); };
    menu.appendChild(btn);
  }
}

export function updateModelDisplay() {
  const label = document.getElementById('model-label');
  const badge = document.getElementById('model-badge');
  const caps = document.getElementById('model-caps');
  if (isCliModel(state.selectedModel)) {
    const m = MODELS.find(m => m.id === state.selectedModel);
    label.textContent = m ? m.icon + ' ' + m.name : state.selectedModel;
    badge.textContent = m ? m.name : state.selectedModel;
  } else if (isApiModel(state.selectedModel)) {
    const m = API_MODELS.find(m => m.id === state.selectedModel);
    label.textContent = m ? m.icon + ' ' + m.name : state.selectedModel;
    badge.textContent = m ? m.name : state.selectedModel;
  } else {
    label.textContent = state.selectedModel || 'Select a model';
    badge.textContent = state.selectedModel || '';
  }
  if (caps) caps.innerHTML = capsHTML(state.selectedModel);
  emit('tools:update');
}

export function updateToolsToggle() {
  const btn = document.getElementById('tools-toggle');
  const label = document.getElementById('tools-label');
  const c = getModelCaps(state.selectedModel);
  const tc = getToolCount(state.selectedModel);
  state.toolsEnabled = true;
  if (c.embedding) { btn.classList.remove('active'); btn.classList.add('disabled'); label.textContent = 'Embed Only'; }
  else if (!c.tools || tc === 0) { btn.classList.remove('active'); btn.classList.add('disabled'); label.textContent = 'No Tools'; }
  else { btn.classList.add('active'); btn.classList.remove('disabled'); label.textContent = tc + ' Tools'; }
}
