'use strict';
import { authFetch } from './utils.js';
import { toast } from './toasts.js';

// ── State ────────────────────────────────────────────────────────
let igSelectedModel = 'sdxl_lightning';
let igSelectedBackend = 'comfyui'; // comfyui | openai | gemini
let igGenerating = false;
let igCurrentPreview = null;
let igRefImageData = null;
let igControlNetData = null;
let igInpaintMode = false;
let igInpaintCtx = null; // canvas 2d context
const MODEL_NAMES = {
  flux_schnell: 'FLUX Schnell', flux_dev: 'FLUX Dev',
  sdxl_lightning: 'SDXL Lightning', sdxl_turbo: 'SDXL Turbo',
  dreamshaper: 'DreamShaper v8', realistic_vision: 'Realistic Vision', deliberate: 'Deliberate v3',
};
const SD15_MODELS = new Set(['dreamshaper', 'realistic_vision', 'deliberate']);

// ── Prompt Templates ─────────────────────────────────────────────
const BUILTIN_TEMPLATES = [
  { name: 'Cinematic', icon: '\uD83C\uDFAC', prompt: 'cinematic film still, dramatic lighting, shallow depth of field, 8k, high detail', negative: 'blurry, low quality, cartoon', model: 'sdxl_lightning' },
  { name: 'Anime', icon: '\uD83C\uDFA8', prompt: 'anime style illustration, vibrant colors, detailed, studio quality', negative: 'photorealistic, 3d render, blurry', model: 'sdxl_lightning' },
  { name: 'Photorealistic', icon: '\uD83D\uDCF7', prompt: 'photorealistic, professional photography, natural lighting, sharp focus, 8k', negative: 'cartoon, painting, illustration, blurry', model: 'sdxl_lightning' },
  { name: 'Product', icon: '\uD83D\uDED2', prompt: 'professional product photography, studio lighting, white background, commercial quality', negative: 'messy, cluttered, dark', model: 'sdxl_lightning' },
  { name: 'Landscape', icon: '\uD83C\uDF04', prompt: 'breathtaking landscape photography, golden hour, panoramic, National Geographic quality', negative: 'people, text, watermark', model: 'sdxl_lightning' },
  { name: 'Fantasy', icon: '\u2694\uFE0F', prompt: 'fantasy art, epic, magical, detailed environment, concept art quality', negative: 'photorealistic, modern, blurry', model: 'sdxl_lightning' },
  { name: 'Oil Paint', icon: '\uD83D\uDD8C\uFE0F', prompt: 'oil painting style, textured brushstrokes, classical composition, museum quality', negative: 'photo, digital, flat', model: 'sdxl_lightning' },
  { name: 'Pixel Art', icon: '\uD83D\uDC7E', prompt: 'pixel art style, retro game aesthetic, clean pixels, 16-bit color palette', negative: 'realistic, photo, smooth, blurry', model: 'sdxl_turbo' },
];

export function initImageGen() {
  window.selectIgModel = selectIgModel;
  window.generateImage = generateImage;
  window.downloadCurrentImage = downloadCurrentImage;
  window.clearRefImage = clearRefImage;
  window.searchRefImage = searchRefImage;
  window.updateIgDimensions = updateIgDimensions;
  window.clearControlNetImage = clearControlNetImage;
  window.openInpaintMode = openInpaintMode;
  window.closeInpaintMode = closeInpaintMode;
  window.clearInpaintMask = clearInpaintMask;
  window.generateInpaint = generateInpaint;
  initRefDropzone();
  initControlNetDropzone();
  renderTemplates();
}

// ── Templates ────────────────────────────────────────────────────
function renderTemplates() {
  const container = document.getElementById('ig-templates');
  if (!container) return;
  container.textContent = '';
  const all = [...BUILTIN_TEMPLATES, ...getUserTemplates()];
  for (const t of all) {
    const chip = document.createElement('button');
    chip.className = 'ig-template-chip';
    chip.title = t.prompt.substring(0, 80);
    chip.textContent = (t.icon || '\uD83C\uDFA8') + ' ' + t.name;
    chip.onclick = () => applyTemplate(t);
    container.appendChild(chip);
  }
  // Add "Save Current" chip
  const saveChip = document.createElement('button');
  saveChip.className = 'ig-template-chip ig-template-save';
  saveChip.textContent = '+ Save';
  saveChip.title = 'Save current prompt as template';
  saveChip.onclick = saveCurrentAsTemplate;
  container.appendChild(saveChip);
}

function applyTemplate(t) {
  const prompt = document.getElementById('ig-prompt');
  const neg = document.getElementById('ig-negative');
  prompt.value = t.prompt; prompt.focus();
  if (t.negative) neg.value = t.negative;
  // Auto-select recommended model
  if (t.model) {
    const btn = document.querySelector('.ig-model-btn[data-model="' + t.model + '"]');
    if (btn) selectIgModel(btn);
  }
  toast('Template applied: ' + t.name, 'success');
}

function saveCurrentAsTemplate() {
  const prompt = document.getElementById('ig-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt first', 'warning'); return; }
  const name = window.prompt('Template name:', prompt.substring(0, 30));
  if (!name) return;
  const templates = getUserTemplates();
  templates.push({ name, icon: '\u2B50', prompt, negative: document.getElementById('ig-negative').value.trim(), model: igSelectedModel, custom: true });
  localStorage.setItem('ailab-img-templates', JSON.stringify(templates));
  renderTemplates();
  toast('Template saved: ' + name, 'success');
}

function getUserTemplates() {
  try { return JSON.parse(localStorage.getItem('ailab-img-templates') || '[]'); } catch { return []; }
}

// ── Reference Image ──────────────────────────────────────────────
function initRefDropzone() {
  const dz = document.getElementById('ig-ref-dropzone');
  const input = document.getElementById('ig-ref-input');
  if (!dz || !input) return;
  dz.onclick = (e) => { if (e.target.tagName !== 'BUTTON') input.click(); };
  input.onchange = () => { if (input.files.length) loadRefImage(input.files[0]); input.value = ''; };
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag-over'); if (e.dataTransfer.files.length) loadRefImage(e.dataTransfer.files[0]); });
}

function loadRefImage(file) {
  if (!file.type.startsWith('image/')) { toast('Only image files', 'warning'); return; }
  if (file.size > 20 * 1024 * 1024) { toast('Max 20MB', 'warning'); return; }
  const reader = new FileReader();
  reader.onload = (e) => {
    igRefImageData = e.target.result;
    const preview = document.getElementById('ig-ref-preview');
    const placeholder = document.getElementById('ig-ref-placeholder');
    const clearBtn = document.getElementById('ig-ref-clear');
    const controls = document.getElementById('ig-ref-controls');
    preview.src = igRefImageData; preview.classList.remove('hidden');
    if (placeholder) placeholder.classList.add('hidden');
    clearBtn.classList.remove('hidden');
    if (controls) controls.style.display = '';
    // Show inpaint button if we have an image
    const inpaintBtn = document.getElementById('ig-inpaint-btn');
    if (inpaintBtn) inpaintBtn.classList.remove('hidden');
    toast('Reference image loaded', 'success');
  };
  reader.readAsDataURL(file);
}

function clearRefImage() {
  igRefImageData = null;
  const preview = document.getElementById('ig-ref-preview');
  const placeholder = document.getElementById('ig-ref-placeholder');
  const clearBtn = document.getElementById('ig-ref-clear');
  const controls = document.getElementById('ig-ref-controls');
  preview.src = ''; preview.classList.add('hidden');
  if (placeholder) placeholder.classList.remove('hidden');
  clearBtn.classList.add('hidden');
  if (controls) controls.style.display = 'none';
}

async function searchRefImage() {
  const prompt = document.getElementById('ig-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt first', 'warning'); return; }
  toast('Searching...', 'info', 2000);
  try {
    const res = await authFetch('/api/image/search-reference', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt, limit: 8 }),
    });
    const data = await res.json();
    if (!data.urls || data.urls.length === 0) { toast('No images found', 'info'); return; }
    showRefPicker(data.urls);
  } catch (e) { toast('Search failed: ' + e.message, 'error'); }
}

function showRefPicker(urls) {
  document.querySelectorAll('.ig-ref-picker-overlay').forEach(el => el.remove());
  const overlay = document.createElement('div'); overlay.className = 'ig-ref-picker-overlay modal-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  const panel = document.createElement('div'); panel.className = 'ig-ref-picker';
  const h2 = document.createElement('h2'); h2.textContent = 'Pick a Reference Image'; panel.appendChild(h2);
  const grid = document.createElement('div'); grid.className = 'ig-ref-grid';
  for (const url of urls) {
    const img = document.createElement('img');
    img.src = url; img.loading = 'lazy'; img.alt = 'Reference';
    img.onerror = () => img.remove();
    img.onclick = () => {
      fetch(url).then(r => r.blob()).then(blob => {
        const reader = new FileReader();
        reader.onload = (e) => {
          igRefImageData = e.target.result;
          const preview = document.getElementById('ig-ref-preview');
          const placeholder = document.getElementById('ig-ref-placeholder');
          const clearBtn = document.getElementById('ig-ref-clear');
          const controls = document.getElementById('ig-ref-controls');
          preview.src = igRefImageData; preview.classList.remove('hidden');
          if (placeholder) placeholder.classList.add('hidden');
          clearBtn.classList.remove('hidden');
          if (controls) controls.style.display = '';
          toast('Reference selected', 'success');
          overlay.remove();
        };
        reader.readAsDataURL(blob);
      }).catch(() => toast('Failed to load', 'error'));
    };
    grid.appendChild(img);
  }
  panel.appendChild(grid);
  const closeBtn = document.createElement('button'); closeBtn.className = 'modal-close'; closeBtn.textContent = '\u00d7';
  closeBtn.onclick = () => overlay.remove(); panel.appendChild(closeBtn);
  overlay.appendChild(panel); document.body.appendChild(overlay);
}

// ── ControlNet ───────────────────────────────────────────────────
function initControlNetDropzone() {
  const dz = document.getElementById('ig-cn-dropzone');
  const input = document.getElementById('ig-cn-input');
  if (!dz || !input) return;
  dz.onclick = (e) => { if (e.target.tagName !== 'BUTTON') input.click(); };
  input.onchange = () => { if (input.files.length) loadControlNetImage(input.files[0]); input.value = ''; };
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag-over'); if (e.dataTransfer.files.length) loadControlNetImage(e.dataTransfer.files[0]); });
}

function loadControlNetImage(file) {
  if (!file.type.startsWith('image/')) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    igControlNetData = e.target.result;
    const preview = document.getElementById('ig-cn-preview');
    preview.src = igControlNetData; preview.classList.remove('hidden');
    document.getElementById('ig-cn-clear').classList.remove('hidden');
    toast('Control image loaded', 'success');
  };
  reader.readAsDataURL(file);
}

function clearControlNetImage() {
  igControlNetData = null;
  const preview = document.getElementById('ig-cn-preview');
  preview.src = ''; preview.classList.add('hidden');
  document.getElementById('ig-cn-clear').classList.add('hidden');
}

// ── Inpainting ───────────────────────────────────────────────────
function openInpaintMode() {
  const previewImg = document.getElementById('ig-preview-img');
  if (!previewImg || !previewImg.src || previewImg.classList.contains('hidden')) {
    toast('Generate or upload an image first', 'warning'); return;
  }
  const overlay = document.getElementById('ig-inpaint-overlay');
  const canvas = document.getElementById('ig-inpaint-canvas');
  const wrap = canvas.parentElement;
  overlay.classList.remove('hidden');
  igInpaintMode = true;

  // Size canvas to match image
  const img = new Image();
  img.onload = () => {
    const maxW = wrap.clientWidth || 512;
    const scale = Math.min(maxW / img.width, 1);
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    igInpaintCtx = canvas.getContext('2d');
    igInpaintCtx.drawImage(img, 0, 0, canvas.width, canvas.height);
    // Drawing state
    let drawing = false;
    canvas.onpointerdown = (e) => { drawing = true; draw(e); };
    canvas.onpointermove = (e) => { if (drawing) draw(e); };
    canvas.onpointerup = () => drawing = false;
    canvas.onpointerleave = () => drawing = false;
    function draw(e) {
      const rect = canvas.getBoundingClientRect();
      const x = (e.clientX - rect.left) * (canvas.width / rect.width);
      const y = (e.clientY - rect.top) * (canvas.height / rect.height);
      const size = parseInt(document.getElementById('ig-inpaint-brush').value);
      igInpaintCtx.globalCompositeOperation = 'source-over';
      igInpaintCtx.fillStyle = 'rgba(255, 255, 255, 0.6)';
      igInpaintCtx.beginPath();
      igInpaintCtx.arc(x, y, size / 2, 0, Math.PI * 2);
      igInpaintCtx.fill();
    }
  };
  img.src = previewImg.src;
}

function closeInpaintMode() {
  document.getElementById('ig-inpaint-overlay').classList.add('hidden');
  igInpaintMode = false;
  igInpaintCtx = null;
}

function clearInpaintMask() {
  if (!igInpaintCtx) return;
  const canvas = document.getElementById('ig-inpaint-canvas');
  const previewImg = document.getElementById('ig-preview-img');
  const img = new Image();
  img.onload = () => { igInpaintCtx.clearRect(0, 0, canvas.width, canvas.height); igInpaintCtx.drawImage(img, 0, 0, canvas.width, canvas.height); };
  img.src = previewImg.src;
}

async function generateInpaint() {
  const canvas = document.getElementById('ig-inpaint-canvas');
  if (!canvas || !igInpaintCtx) return;
  // Extract mask: white where user painted, black elsewhere
  const maskCanvas = document.createElement('canvas');
  maskCanvas.width = canvas.width;
  maskCanvas.height = canvas.height;
  const maskCtx = maskCanvas.getContext('2d');
  const imgData = igInpaintCtx.getImageData(0, 0, canvas.width, canvas.height);
  // Create mask from painted regions (white = edit, black = keep)
  const previewImg = document.getElementById('ig-preview-img');
  const origImg = new Image();
  origImg.onload = async () => {
    const origCanvas = document.createElement('canvas');
    origCanvas.width = canvas.width; origCanvas.height = canvas.height;
    const origCtx = origCanvas.getContext('2d');
    origCtx.drawImage(origImg, 0, 0, canvas.width, canvas.height);
    const origData = origCtx.getImageData(0, 0, canvas.width, canvas.height);
    // Diff: where canvas differs from original = mask
    const maskData = maskCtx.createImageData(canvas.width, canvas.height);
    for (let i = 0; i < imgData.data.length; i += 4) {
      const diff = Math.abs(imgData.data[i] - origData.data[i]) + Math.abs(imgData.data[i+1] - origData.data[i+1]) + Math.abs(imgData.data[i+2] - origData.data[i+2]);
      const isMasked = diff > 30; // threshold
      maskData.data[i] = isMasked ? 255 : 0;
      maskData.data[i+1] = isMasked ? 255 : 0;
      maskData.data[i+2] = isMasked ? 255 : 0;
      maskData.data[i+3] = 255;
    }
    maskCtx.putImageData(maskData, 0, 0);
    const maskDataUri = maskCanvas.toDataURL('image/png');
    closeInpaintMode();
    // Use the preview image as source + mask for inpaint generation
    const srcDataUri = previewImg.src.startsWith('blob:') ? origCanvas.toDataURL('image/png') : previewImg.src;
    // Trigger generation with mask
    const prompt = document.getElementById('ig-prompt').value.trim() || 'fill in the masked region naturally';
    toast('Generating inpaint...', 'info', 2000);
    try {
      const body = {
        prompt, model: igSelectedModel, backend: igSelectedBackend,
        width: canvas.width, height: canvas.height,
        reference_image: srcDataUri,
        mask: maskDataUri,
        denoise: parseFloat(document.getElementById('ig-denoise').value || '0.75'),
      };
      const r = await authFetch('/api/image/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const submit = await r.json();
      if (submit.jobId) pollAndDisplay(submit.jobId, prompt);
    } catch (e) { toast('Inpaint failed: ' + e.message, 'error'); }
  };
  origImg.src = previewImg.src;
}

// ── Aspect Ratio / Resolution ────────────────────────────────────
function updateIgDimensions() {
  const aspect = document.getElementById('ig-aspect').value;
  const customRow = document.getElementById('ig-custom-dims');
  if (aspect === 'custom') { customRow.style.display = ''; return; }
  customRow.style.display = 'none';
  const base = igSelectedModel === 'sdxl_turbo' ? 512 : 1024;
  const dims = { '1:1': [base, base], '16:9': [base, Math.round(base*9/16)], '9:16': [Math.round(base*9/16), base], '4:3': [base, Math.round(base*3/4)], '3:2': [base, Math.round(base*2/3)] };
  const d = dims[aspect] || [base, base];
  document.getElementById('ig-width').value = d[0];
  document.getElementById('ig-height').value = d[1];
}

// ── Model Selection ──────────────────────────────────────────────
function selectIgModel(btn) {
  if (btn.classList.contains('disabled')) {
    toast('Model unavailable — requires HuggingFace token', 'warning'); return;
  }
  document.querySelectorAll('.ig-model-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  igSelectedModel = btn.dataset.model;
  igSelectedBackend = btn.dataset.backend || 'comfyui';

  // Toggle visibility of ComfyUI-specific controls
  const isApi = igSelectedBackend !== 'comfyui';
  const stepsField = document.getElementById('ig-steps').closest('.ig-field');
  const seedField = document.getElementById('ig-seed').closest('.ig-field');
  const cnSection = document.getElementById('ig-controlnet-section');
  if (stepsField) stepsField.style.opacity = isApi ? '0.3' : '1';
  if (seedField) seedField.style.opacity = isApi ? '0.3' : '1';
  if (cnSection) cnSection.style.display = isApi ? 'none' : '';
  // Update status text to reflect selected backend
  const statusText = document.getElementById('ig-status-text');
  if (statusText) {
    if (isApi) {
      const names = { openai: 'GPT-5.4 (OpenAI)', gemini: 'Gemini 2.5 Flash' };
      statusText.textContent = (names[igSelectedBackend] || igSelectedBackend) + ' selected';
      document.querySelector('.ig-status-dot').className = 'ig-status-dot cloud';
    } else {
      checkComfyUIStatus();
    }
  }

  if (igSelectedModel === 'sdxl_turbo' || SD15_MODELS.has(igSelectedModel)) {
    document.getElementById('ig-width').value = '512';
    document.getElementById('ig-height').value = '512';
    const r4k = document.getElementById('ig-resolution').querySelector('[value="4096"]');
    if (r4k) r4k.disabled = SD15_MODELS.has(igSelectedModel);
  } else if (!isApi) {
    document.getElementById('ig-width').value = '1024';
    document.getElementById('ig-height').value = '1024';
    const r4k = document.getElementById('ig-resolution').querySelector('[value="4096"]');
    if (r4k) r4k.disabled = false;
  }
  updateIgDimensions();
}

// ── ComfyUI Status ───────────────────────────────────────────────
export async function checkComfyUIStatus() {
  const dot = document.querySelector('.ig-status-dot');
  const txt = document.getElementById('ig-status-text');
  try {
    const r = await authFetch('/api/image/status');
    const d = await r.json();
    if (d.ok) {
      dot.className = 'ig-status-dot';
      // Query available models from ComfyUI and enable/disable buttons
      try {
        const modelsRes = await authFetch('/api/image/models');
        if (modelsRes.ok) {
          const md = await modelsRes.json();
          const ckpts = new Set(md.checkpoints || []);
          const unets = new Set(md.unets || []);
          let available = 0;
          document.querySelectorAll('.ig-model-btn[data-model]').forEach(btn => {
            if (btn.dataset.backend) return; // Skip cloud models
            const ckpt = btn.dataset.ckpt;
            const unet = btn.dataset.unet;
            const found = (ckpt && ckpts.has(ckpt)) || (unet && unets.has(unet));
            btn.classList.toggle('disabled', !found);
            if (found) available++;
          });
          txt.textContent = available + ' models ready' + (d.gpu ? ' \u2014 ' + d.gpu : '');
        } else {
          txt.textContent = 'ComfyUI ready' + (d.gpu ? ' \u2014 ' + d.gpu : '');
        }
      } catch { txt.textContent = 'ComfyUI ready' + (d.gpu ? ' \u2014 ' + d.gpu : ''); }
    }
    else { dot.className = 'ig-status-dot error'; txt.textContent = d.error || 'ComfyUI unreachable'; }
  } catch { dot.className = 'ig-status-dot error'; txt.textContent = 'Cannot reach backend'; }
}

// ── Generate ─────────────────────────────────────────────────────
async function generateImage() {
  const prompt = document.getElementById('ig-prompt').value.trim();
  if (!prompt || igGenerating) return;
  igGenerating = true;
  const btn = document.getElementById('ig-generate-btn');
  const loading = document.getElementById('ig-loading');
  const loadingText = document.getElementById('ig-loading-text');
  btn.disabled = true; btn.textContent = 'Generating...'; loading.classList.remove('hidden');
  const dot = document.querySelector('.ig-status-dot'); dot.className = 'ig-status-dot busy';
  const modelNames = MODEL_NAMES;
  loadingText.textContent = 'Generating with ' + (modelNames[igSelectedModel] || igSelectedModel) + '...';

  const count = parseInt(document.getElementById('ig-count').value) || 1;
  const body = {
    prompt, model: igSelectedModel, backend: igSelectedBackend, count,
    width: parseInt(document.getElementById('ig-width').value),
    height: parseInt(document.getElementById('ig-height').value),
  };
  const neg = document.getElementById('ig-negative').value.trim();
  if (neg) body.negative_prompt = neg;
  if (igSelectedBackend === 'comfyui') {
    const steps = document.getElementById('ig-steps').value;
    if (steps) body.steps = parseInt(steps);
    const seed = document.getElementById('ig-seed').value;
    if (seed) body.seed = parseInt(seed);
  }
  if (igRefImageData) {
    body.reference_image = igRefImageData;
    body.denoise = parseFloat(document.getElementById('ig-denoise').value);
  }
  const resolution = parseInt(document.getElementById('ig-resolution').value);
  if (resolution > 1024) body.upscale_to = resolution;
  // ControlNet
  const cnType = document.getElementById('ig-cn-type').value;
  if (cnType !== 'none' && igControlNetData && igSelectedBackend === 'comfyui') {
    body.controlnet_type = cnType;
    body.controlnet_image = igControlNetData;
    body.controlnet_strength = parseFloat(document.getElementById('ig-cn-strength').value);
  }

  try {
    const r = await authFetch('/api/image/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const et = await r.text();
      let em; try { em = JSON.parse(et).error; } catch { em = et.slice(0, 200); }
      throw new Error(em || 'HTTP ' + r.status);
    }
    const submit = await r.json();
    if (!submit.jobId) throw new Error(submit.error || 'No job ID');
    await pollAndDisplay(submit.jobId, prompt);
    // Restore status to reflect current backend selection
    if (igSelectedBackend !== 'comfyui') {
      const names = { openai: 'GPT-5.4 (OpenAI)', gemini: 'Gemini 2.5 Flash' };
      dot.className = 'ig-status-dot cloud';
      document.getElementById('ig-status-text').textContent = (names[igSelectedBackend] || igSelectedBackend) + ' \u2014 done';
    } else {
      dot.className = 'ig-status-dot';
    }
  } catch (e) {
    if (e.message === 'Not authenticated') return;
    dot.className = 'ig-status-dot error';
    document.getElementById('ig-status-text').textContent = e.message || 'Failed';
  } finally {
    igGenerating = false; btn.disabled = false; btn.textContent = 'Generate'; loading.classList.add('hidden');
  }
}

// ── Poll + Display ───────────────────────────────────────────────
async function pollAndDisplay(jobId, prompt) {
  const loadingText = document.getElementById('ig-loading-text');
  const modelNames = MODEL_NAMES;
  const resolution = parseInt(document.getElementById('ig-resolution').value);
  let job = null;
  for (let poll = 0; poll < 180; poll++) {
    await new Promise(ok => setTimeout(ok, 2000));
    if (!igGenerating && !igInpaintMode) break;
    try {
      const pr = await authFetch('/api/image/job/' + jobId);
      job = await pr.json();
      if (job.status === 'done' || job.status === 'error') break;
      if (job.status === 'upscaling') loadingText.textContent = 'Upscaling to ' + resolution + 'px...';
      else loadingText.textContent = 'Generating with ' + (modelNames[igSelectedModel] || igSelectedModel) + '... (' + (poll * 2) + 's)';
    } catch {}
  }
  if (!job || job.status !== 'done') {
    toast(job ? job.error || 'Timed out' : 'No response', 'error'); return;
  }
  const gallery = document.getElementById('ig-gallery');
  const images = job.images || [];
  const count = parseInt(document.getElementById('ig-count').value) || 1;
  // Batch: wrap in comparison grid if >1
  if (count > 1 && images.length > 1) {
    const batchGrid = document.createElement('div');
    batchGrid.className = 'ig-batch-grid ig-batch-' + Math.min(images.length, 4);
    for (let i = 0; i < images.length; i++) {
      const card = await makeImageCard(images[i], prompt, i);
      batchGrid.appendChild(card);
    }
    gallery.prepend(batchGrid);
  } else {
    for (let i = 0; i < images.length; i++) {
      const card = await makeImageCard(images[i], prompt, i);
      gallery.prepend(card);
    }
  }
  // Show inpaint button since we now have a generated image
  const inpaintBtn = document.getElementById('ig-inpaint-btn');
  if (inpaintBtn && images.length > 0) inpaintBtn.classList.remove('hidden');
  // Cap gallery
  while (gallery.children.length > 50) {
    const old = gallery.lastElementChild;
    const oi = old ? old.querySelector('img') : null;
    if (oi && oi.src && oi.src.startsWith('blob:')) URL.revokeObjectURL(oi.src);
    if (old) old.remove();
  }
}

async function makeImageCard(img, prompt, idx) {
  const modelNames = MODEL_NAMES;
  const card = document.createElement('div'); card.className = 'ig-image-card';
  const imgEl = document.createElement('img');
  if (img.url) {
    try { const r = await authFetch(img.url); const blob = await r.blob(); imgEl.src = URL.createObjectURL(blob); }
    catch { imgEl.src = img.url; }
  } else if (img.data) {
    imgEl.src = 'data:' + (img.mimeType || 'image/jpeg') + ';base64,' + img.data;
  }
  imgEl.loading = 'lazy'; imgEl.alt = prompt.slice(0, 100);
  card.appendChild(imgEl);
  const info = document.createElement('div'); info.className = 'ig-image-info';
  info.textContent = (modelNames[igSelectedModel] || igSelectedModel) + ' \u00b7 ' + prompt.slice(0, 60);
  card.appendChild(info);
  if (idx === 0) setPreviewImage(imgEl.src, img.savedAs || igSelectedModel + '_' + Date.now() + '.jpg', igSelectedModel, prompt);
  card.addEventListener('dblclick', () => setPreviewImage(imgEl.src, img.savedAs || igSelectedModel + '_' + Date.now() + '.jpg', igSelectedModel, prompt));
  // "Use as reference" button
  const useRefBtn = document.createElement('button');
  useRefBtn.className = 'ig-use-ref-btn';
  useRefBtn.textContent = 'Use as Ref';
  useRefBtn.onclick = (e) => {
    e.stopPropagation();
    igRefImageData = imgEl.src.startsWith('blob:') ? null : imgEl.src;
    if (!igRefImageData) {
      // Convert blob URL to data URI
      fetch(imgEl.src).then(r => r.blob()).then(b => {
        const fr = new FileReader(); fr.onload = () => { igRefImageData = fr.result; toast('Set as reference', 'success'); }; fr.readAsDataURL(b);
      });
    } else { toast('Set as reference', 'success'); }
  };
  card.appendChild(useRefBtn);
  return card;
}

// ── Preview + Download ───────────────────────────────────────────
function setPreviewImage(src, filename, model, prompt) {
  const preview = document.getElementById('ig-preview');
  const img = document.getElementById('ig-preview-img');
  if (img.src && img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
  const actions = document.getElementById('ig-preview-actions');
  const placeholder = document.getElementById('ig-preview-placeholder');
  const meta = document.getElementById('ig-preview-meta');
  const modelNames = MODEL_NAMES;
  if (src.startsWith('/api/')) { authFetch(src).then(r => r.blob()).then(blob => { img.src = URL.createObjectURL(blob); }).catch(() => { img.src = src; }); }
  else { img.src = src; }
  img.dataset.filename = filename || '';
  img.classList.remove('hidden');
  img.onclick = () => {
    document.querySelectorAll('.ig-lightbox').forEach(el => el.remove());
    const lb = document.createElement('div'); lb.className = 'ig-lightbox';
    lb.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.9);display:flex;align-items:center;justify-content:center;cursor:pointer';
    const lbImg = document.createElement('img'); lbImg.src = src;
    lbImg.style.cssText = 'max-width:90vw;max-height:90vh;border-radius:8px';
    lb.appendChild(lbImg);
    const _esc = (e) => { if (e.key === 'Escape') { lb.remove(); document.removeEventListener('keydown', _esc); } };
    document.addEventListener('keydown', _esc);
    lb.onclick = () => { lb.remove(); document.removeEventListener('keydown', _esc); };
    document.body.appendChild(lb);
  };
  actions.classList.remove('hidden');
  if (placeholder) placeholder.classList.add('hidden');
  preview.classList.remove('ig-preview-empty');
  meta.textContent = (modelNames[model] || model) + ' \u00b7 ' + (prompt || '').slice(0, 60);
  igCurrentPreview = { src, filename, model, prompt };
  // Show inpaint button
  const inpaintBtn = document.getElementById('ig-inpaint-btn');
  if (inpaintBtn) inpaintBtn.classList.remove('hidden');
}

function downloadCurrentImage() {
  const previewImg = document.getElementById('ig-preview-img');
  if (!previewImg || !previewImg.src) return;
  const filename = previewImg.dataset.filename || 'image_' + Date.now() + '.jpg';
  if (previewImg.dataset.filename) {
    authFetch('/api/image/download/' + encodeURIComponent(previewImg.dataset.filename))
      .then(r => r.blob()).then(blob => {
        const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
      }).catch(() => dlSrc(previewImg.src, filename));
  } else { dlSrc(previewImg.src, filename); }
}

function dlSrc(src, fn) {
  if (src.startsWith('blob:') || src.startsWith('data:')) { const a = document.createElement('a'); a.href = src; a.download = fn; a.click(); }
  else { const f = src.startsWith('/api/') ? authFetch(src) : fetch(src); f.then(r => r.blob()).then(b => { const u = URL.createObjectURL(b); const a = document.createElement('a'); a.href = u; a.download = fn; a.click(); URL.revokeObjectURL(u); }); }
}
