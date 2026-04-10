'use strict';
import { state, emit } from './state.js';
import { authFetch, formatCtx } from './utils.js';

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
  return id && (
    MODELS.some(model => model.id === id) ||
    API_MODELS.some(model => model.id === id)
  );
}

export function isApiModel(id) {
  return id && id.startsWith('api:');
}

// CLI models use these prefixes for MCP routing
const _CLOUD_PREFIXES = ['claude:', 'codex:', 'gemini:'];

export function isCliModel(id) {
  if (!id) return false;
  if (id === 'qwen') return true;
  return _CLOUD_PREFIXES.some(prefix => id.startsWith(prefix));
}

// Check model health by probing /api/model-status
export async function checkModelHealth() {
  try {
    const res = await authFetch('/api/model-status');
    const data = await res.json();
    Object.assign(_modelHealth, data.status || {});
  } catch {}
}

export function getModelHealth(provider) {
  return _modelHealth[provider] || 'unknown';
}

export function getModelCaps(id) {
  if (!id) return { vision: false, tools: false, reasoning: false, embedding: false };
  if (state.validatedCaps[id]) return state.validatedCaps[id];

  const lower = id.toLowerCase();

  if (/embed|embedding/.test(lower)) {
    return { vision: false, tools: false, reasoning: false, embedding: true };
  }

  const caps = { vision: false, tools: true, reasoning: false, embedding: false };

  if (/\bvl\b|-vl-|vision|4v/.test(lower)) caps.vision = true;

  const model = state.availableModels.find(model => model.id === id);
  if (model && model.model_type === 'vlm') caps.vision = true;

  if (/reasoning|qwen3\.5|magistral|think|phi-4|gemma-4/.test(lower)) caps.reasoning = true;

  return caps;
}

export function getModelMeta(id) {
  const model = state.availableModels.find(model => model.id === id);

  return {
    state:     model ? model.state || 'unknown' : 'unknown',
    quant:     model ? model.quantization || '' : '',
    ctx:       model
               ? model.max_context_length || (model.profile ? model.profile.max_tokens : 0) || 0
               : 0,
    type:      model ? model.model_type || (model.type === 'vlm' ? 'vlm' : 'llm') : 'llm',
    toolCount: model && model.profile ? model.profile.tool_count || 9 : 9,
  };
}

export function capsHTML(id) {
  const caps = getModelCaps(id);

  if (caps.embedding) {
    return '<span class="cap-badge cap-embed" title="Embedding">&#128202;</span>';
  }

  let html = '';
  if (caps.vision) {
    html += '<span class="cap-badge cap-vision" title="Vision">&#128065;&#65039;</span>';
  }
  if (caps.reasoning) {
    html += '<span class="cap-badge cap-reason" title="Reasoning">&#129504;</span>';
  }
  if (caps.tools) {
    html += '<span class="cap-badge cap-tools" title="Tools">&#128296;</span>';
  } else {
    html += '<span class="cap-badge cap-notools" title="No tools">&#128683;</span>';
  }

  return html;
}

export function getToolCount(modelId) {
  if (!modelId) return 0;
  const model = state.availableModels.find(model => model.id === modelId);
  if (!model || !model.profile) return 9;
  return model.profile.tool_count || 9;
}

export async function loadModels() {
  try {
    const res = await authFetch('/api/models');
    const data = await res.json();

    state.availableModels = (data.models || []).filter(model => model.id);

    for (const model of state.availableModels) {
      if (model.validated) {
        state.validatedCaps[model.id] = {
          chat:       model.chat !== false,
          tools:      model.tools !== false,
          reasoning:  model.reasoning || false,
          embedding:  model.embedding || false,
          vision:     model.vision || false,
          limitation: model.limitation || null,
        };
      }
    }

    const stored = localStorage.getItem('dartboard-model');
    if (stored && (
      state.availableModels.some(model => model.id === stored) ||
      stored.startsWith('api:') ||
      isCliModel(stored)
    )) {
      state.selectedModel = stored;
    }

    emit('models:loaded');
    emit('conn:status', true);
  } catch (e) {
    console.error('loadModels:', e);
    emit('conn:status', false);
  }
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
    const model = MODELS.find(model => model.id === modelId);
    state.validatedCaps[modelId] = {
      chat:       true,
      tools:      true,
      reasoning:  true,
      embedding:  false,
      vision:     modelId.startsWith('claude:') ||
                  modelId.startsWith('gemini:') ||
                  modelId.startsWith('codex:'),
      limitation: null,
    };
    state.selectedModelReady = true;
    emit('model:ready', { text: (model ? model.icon + ' ' + model.name : modelId) + ' ready' });
    return;
  }

  if (isApiModel(modelId)) {
    const model = API_MODELS.find(model => model.id === modelId);
    state.validatedCaps[modelId] = {
      chat:       true,
      tools:      false,
      reasoning:  modelId.includes('o4-') || modelId.includes('opus'),
      embedding:  false,
      vision:     !modelId.includes('o4-'),
      limitation: null,
    };
    state.selectedModelReady = true;
    emit('model:ready', { text: (model ? model.icon + ' ' + model.name : modelId) + ' ready' });
    return;
  }

  emit('model:loading', { text: 'Loading ' + modelId + '...' });

  try {
    const res = await authFetch('/api/warmup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelId }),
    });
    const data = await res.json();

    state.validatedCaps[modelId] = {
      chat:       data.chat !== false,
      tools:      data.tools !== false,
      reasoning:  data.reasoning || false,
      embedding:  data.embedding || false,
      vision:     false,
      limitation: data.limitation || null,
    };
    emit('model:changed');

    if (data.status === 'ready') {
      state.selectedModelReady = true;
      const capList = [];
      if (data.chat)      capList.push('Chat');
      if (data.tools)     capList.push('Tools');
      if (data.reasoning) capList.push('Reasoning');
      emit('model:ready', { text: 'Ready: ' + capList.join(', ') });
    } else if (data.status === 'limited') {
      state.selectedModelReady = true;
      emit('model:ready', { text: data.limitation || 'Limited' });
    } else {
      emit('model:ready', { text: data.message || 'Could not load' });
    }
  } catch (e) {
    emit('model:ready', { text: 'Connection failed: ' + e.message });
  }

  // Only PATCH conversation model if this model is still the selected one
  // (guards against stale warmup responses from rapid model switching)
  if (state.currentConvId && state.selectedModel === modelId) {
    try {
      await authFetch('/api/conversations/' + state.currentConvId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      });
    } catch {}
  }
}

// ── renderModelMenu helpers ──────────────────────────────────────

function makeGroupLabel(text) {
  const label = document.createElement('div');
  label.style.cssText =
    'padding:4px 12px;font-size:10px;color:var(--text-muted);' +
    'text-transform:uppercase;letter-spacing:0.05em;';
  label.textContent = text;
  return label;
}

function makeCloudModelButton(model, menu) {
  const btn = document.createElement('button');
  btn.className = 'dropdown-item api-item' + (model.id === state.selectedModel ? ' active' : '');

  const row = document.createElement('div');
  row.className = 'model-row';

  const iconSpan = document.createElement('span');
  iconSpan.className = 'model-icon';
  iconSpan.textContent = model.icon;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'model-id';
  nameSpan.textContent = model.name;

  const costTag = document.createElement('span');
  costTag.className = 'meta-tag cost-' + (model.cost === 'free' ? 'free' : 'paid');
  costTag.textContent = model.cost.toUpperCase();

  const checkSpan = document.createElement('span');
  checkSpan.className = 'check';
  checkSpan.textContent = model.id === state.selectedModel ? '\u2713' : '';

  row.appendChild(iconSpan);
  row.appendChild(nameSpan);
  row.appendChild(costTag);
  row.appendChild(checkSpan);
  btn.appendChild(row);

  const metaDiv = document.createElement('div');
  metaDiv.className = 'model-meta';

  const descTag = document.createElement('span');
  descTag.className = 'meta-tag';
  descTag.textContent = model.desc;

  metaDiv.appendChild(descTag);
  btn.appendChild(metaDiv);

  btn.onclick = (e) => {
    e.stopPropagation();
    pickModel(model.id);
    menu.classList.add('hidden');
  };

  return btn;
}

export function renderModelMenu() {
  const menu = document.getElementById('model-menu');
  menu.textContent = '';

  // ── Cloud Models (OAuth) section ─────────────────────────────────
  const cloudHdr = document.createElement('div');
  cloudHdr.className = 'dropdown-section-header api-header';
  cloudHdr.textContent = '\uD83D\uDD11 Cloud Models \u2014 OAuth';
  menu.appendChild(cloudHdr);

  const byGroup = {};
  for (const model of MODELS) {
    if (!byGroup[model.group]) byGroup[model.group] = [];
    byGroup[model.group].push(model);
  }

  for (const [group, models] of Object.entries(byGroup)) {
    menu.appendChild(makeGroupLabel(group));
    for (const model of models) {
      menu.appendChild(makeCloudModelButton(model, menu));
    }
  }

  // ── API Models (direct API key) section — only shown when configured ──
  if (API_MODELS.length > 0) {
    const apiHdr = document.createElement('div');
    apiHdr.className = 'dropdown-section-header';
    apiHdr.textContent = '\uD83D\uDCE1 API Models \u2014 Direct Key';
    menu.appendChild(apiHdr);

    const apiByGroup = {};
    for (const model of API_MODELS) {
      if (!apiByGroup[model.group]) apiByGroup[model.group] = [];
      apiByGroup[model.group].push(model);
    }

    for (const [group, models] of Object.entries(apiByGroup)) {
      menu.appendChild(makeGroupLabel(group));
      for (const model of models) {
        const btn = makeCloudModelButton(model, menu);
        const costTag = btn.querySelector('.meta-tag');
        if (costTag) {
          costTag.className = 'meta-tag cost-paid';
          costTag.textContent = 'API KEY';
        }
        menu.appendChild(btn);
      }
    }
  }

  // ── Local Models section ────────────────────────────────────────
  const localHdr = document.createElement('div');
  localHdr.className = 'dropdown-section-header';
  localHdr.textContent = '\uD83D\uDCBB Local Models (LM Studio)';
  menu.appendChild(localHdr);

  if (state.availableModels.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'dropdown-empty';
    empty.textContent = 'No models found. Is LM Studio running?';
    menu.appendChild(empty);
    return;
  }

  const sorted = [...state.availableModels].sort((a, b) => {
    const aRank = a.state === 'loaded' ? 0 : 1;
    const bRank = b.state === 'loaded' ? 0 : 1;
    return aRank !== bRank ? aRank - bRank : (a.id || '').localeCompare(b.id || '');
  });

  for (const model of sorted) {
    if ((model.model_type || model.type) === 'embeddings') continue;

    const meta = getModelMeta(model.id);

    const btn = document.createElement('button');
    btn.className = 'dropdown-item' + (model.id === state.selectedModel ? ' active' : '');

    const row = document.createElement('div');
    row.className = 'model-row';

    const stateDot = document.createElement('span');
    stateDot.className = 'state-dot ' + (meta.state === 'loaded' ? 'loaded' : 'unloaded');
    stateDot.title = meta.state;

    const nameSpan = document.createElement('span');
    nameSpan.className = 'model-id';
    nameSpan.textContent = model.id;

    const capsSpan = document.createElement('span');
    capsSpan.className = 'model-caps-row';
    capsSpan.innerHTML = capsHTML(model.id);

    const checkSpan = document.createElement('span');
    checkSpan.className = 'check';
    checkSpan.textContent = model.id === state.selectedModel ? '\u2713' : '';

    row.appendChild(stateDot);
    row.appendChild(nameSpan);
    row.appendChild(capsSpan);
    row.appendChild(checkSpan);
    btn.appendChild(row);

    const metaRow = document.createElement('div');
    metaRow.className = 'model-meta';

    const tags = [];
    if (meta.quant) tags.push(meta.quant);
    if (meta.ctx)   tags.push(formatCtx(meta.ctx) + ' ctx');
    tags.push(meta.type.toUpperCase());
    tags.push(meta.toolCount + ' tools');

    for (const tag of tags) {
      const tagSpan = document.createElement('span');
      tagSpan.className = 'meta-tag';
      tagSpan.textContent = tag;
      metaRow.appendChild(tagSpan);
    }

    btn.appendChild(metaRow);
    btn.onclick = (e) => {
      e.stopPropagation();
      pickModel(model.id);
      menu.classList.add('hidden');
    };

    menu.appendChild(btn);
  }
}

export function updateModelDisplay() {
  const label = document.getElementById('model-label');
  const badge = document.getElementById('model-badge');
  const caps  = document.getElementById('model-caps');

  if (isCliModel(state.selectedModel)) {
    const model = MODELS.find(model => model.id === state.selectedModel);
    label.textContent = model ? model.icon + ' ' + model.name : state.selectedModel;
    badge.textContent = model ? model.name : state.selectedModel;
  } else if (isApiModel(state.selectedModel)) {
    const model = API_MODELS.find(model => model.id === state.selectedModel);
    label.textContent = model ? model.icon + ' ' + model.name : state.selectedModel;
    badge.textContent = model ? model.name : state.selectedModel;
  } else {
    label.textContent = state.selectedModel || 'Select a model';
    badge.textContent = state.selectedModel || '';
  }

  if (caps) caps.innerHTML = capsHTML(state.selectedModel);
  emit('tools:update');
}

export function updateToolsToggle() {
  const btn   = document.getElementById('tools-toggle');
  const label = document.getElementById('tools-label');
  const caps  = getModelCaps(state.selectedModel);
  const count = getToolCount(state.selectedModel);

  state.toolsEnabled = true;

  if (caps.embedding) {
    btn.classList.remove('active');
    btn.classList.add('disabled');
    label.textContent = 'Embed Only';
  } else if (!caps.tools || count === 0) {
    btn.classList.remove('active');
    btn.classList.add('disabled');
    label.textContent = 'No Tools';
  } else {
    btn.classList.add('active');
    btn.classList.remove('disabled');
    label.textContent = count + ' Tools';
  }
}
