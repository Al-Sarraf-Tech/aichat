'use strict';
import { authFetch } from './utils.js';
import { toast } from './toasts.js';

// ── State ────────────────────────────────────────────────────────
let igSelectedModel   = 'sdxl_lightning';
let igSelectedBackend = 'comfyui'; // comfyui only (cloud backends removed)
let igGenerating      = false;
let igRefImageData    = null;
let igControlNetData  = null;
let igInpaintMode     = false;
let igInpaintCtx      = null; // canvas 2d context

const MODEL_NAMES = {
  flux_schnell:    'FLUX Schnell',
  flux_dev:        'FLUX Dev',
  sdxl_lightning:  'SDXL Lightning',
  sdxl_turbo:      'SDXL Turbo',
  dreamshaper:     'DreamShaper v8',
  realistic_vision: 'Realistic Vision',
  deliberate:      'Deliberate v3',
  juggernaut_xl:   'Juggernaut XL',
  animagine_xl:    'Animagine XL',
  realvisxl:       'RealVisXL v5',
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

// ── Init ─────────────────────────────────────────────────────────
export function initImageGen() {
  window.selectIgModel         = selectIgModel;
  window.generateImage         = generateImage;
  window.downloadCurrentImage  = downloadCurrentImage;
  window.clearRefImage         = clearRefImage;
  window.searchRefImage        = searchRefImage;
  window.updateIgDimensions    = updateIgDimensions;
  window.clearControlNetImage  = clearControlNetImage;
  window.openInpaintMode       = openInpaintMode;
  window.closeInpaintMode      = closeInpaintMode;
  window.clearInpaintMask      = clearInpaintMask;
  window.generateInpaint       = generateInpaint;

  initRefDropzone();
  initControlNetDropzone();
  renderTemplates();
}

// ── Templates ────────────────────────────────────────────────────
function renderTemplates() {
  const container = document.getElementById('ig-templates');
  if (!container) return;

  container.textContent = '';

  const allTemplates = [...BUILTIN_TEMPLATES, ...getUserTemplates()];
  for (const template of allTemplates) {
    const chip = document.createElement('button');
    chip.className = 'ig-template-chip';
    chip.title     = template.prompt.substring(0, 80);
    chip.textContent = (template.icon || '\uD83C\uDFA8') + ' ' + template.name;
    chip.onclick = () => applyTemplate(template);
    container.appendChild(chip);
  }

  // "Save Current" chip
  const saveChip = document.createElement('button');
  saveChip.className   = 'ig-template-chip ig-template-save';
  saveChip.textContent = '+ Save';
  saveChip.title       = 'Save current prompt as template';
  saveChip.onclick     = saveCurrentAsTemplate;
  container.appendChild(saveChip);
}

function applyTemplate(template) {
  const promptEl   = document.getElementById('ig-prompt');
  const negativeEl = document.getElementById('ig-negative');

  promptEl.value = template.prompt;
  promptEl.focus();
  if (template.negative) negativeEl.value = template.negative;

  // Auto-select recommended model
  if (template.model) {
    const modelBtn = document.querySelector(
      '.ig-model-btn[data-model="' + template.model + '"]'
    );
    if (modelBtn) selectIgModel(modelBtn);
  }

  toast('Template applied: ' + template.name, 'success');
}

function saveCurrentAsTemplate() {
  const prompt = document.getElementById('ig-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt first', 'warning'); return; }

  const name = window.prompt('Template name:', prompt.substring(0, 30));
  if (!name) return;

  const templates = getUserTemplates();
  templates.push({
    name,
    icon:     '\u2B50',
    prompt,
    negative: document.getElementById('ig-negative').value.trim(),
    model:    igSelectedModel,
    custom:   true,
  });
  localStorage.setItem('ailab-img-templates', JSON.stringify(templates));
  renderTemplates();
  toast('Template saved: ' + name, 'success');
}

function getUserTemplates() {
  try {
    return JSON.parse(localStorage.getItem('ailab-img-templates') || '[]');
  } catch {
    return [];
  }
}

// ── Reference Image ──────────────────────────────────────────────
function initRefDropzone() {
  const dropzone = document.getElementById('ig-ref-dropzone');
  const input    = document.getElementById('ig-ref-input');
  if (!dropzone || !input) return;

  dropzone.onclick = (event) => {
    if (event.target.tagName !== 'BUTTON') input.click();
  };

  input.onchange = () => {
    if (input.files.length) loadRefImage(input.files[0]);
    input.value = '';
  };

  dropzone.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropzone.classList.add('drag-over');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('drag-over');
  });

  dropzone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropzone.classList.remove('drag-over');
    if (event.dataTransfer.files.length) loadRefImage(event.dataTransfer.files[0]);
  });
}

function loadRefImage(file) {
  if (!file.type.startsWith('image/')) { toast('Only image files', 'warning'); return; }
  if (file.size > 20 * 1024 * 1024)   { toast('Max 20MB', 'warning'); return; }

  const reader = new FileReader();
  reader.onload = (event) => {
    igRefImageData = event.target.result;

    const preview     = document.getElementById('ig-ref-preview');
    const placeholder = document.getElementById('ig-ref-placeholder');
    const clearBtn    = document.getElementById('ig-ref-clear');
    const controls    = document.getElementById('ig-ref-controls');
    const inpaintBtn  = document.getElementById('ig-inpaint-btn');

    preview.src = igRefImageData;
    preview.classList.remove('hidden');
    if (placeholder) placeholder.classList.add('hidden');
    clearBtn.classList.remove('hidden');
    if (controls) controls.style.display = '';

    // Show inpaint button now that we have an image
    if (inpaintBtn) inpaintBtn.classList.remove('hidden');

    toast('Reference image loaded', 'success');
  };
  reader.readAsDataURL(file);
}

function clearRefImage() {
  igRefImageData = null;

  const preview     = document.getElementById('ig-ref-preview');
  const placeholder = document.getElementById('ig-ref-placeholder');
  const clearBtn    = document.getElementById('ig-ref-clear');
  const controls    = document.getElementById('ig-ref-controls');

  preview.src = '';
  preview.classList.add('hidden');
  if (placeholder) placeholder.classList.remove('hidden');
  clearBtn.classList.add('hidden');
  if (controls) controls.style.display = 'none';
}

async function searchRefImage() {
  const prompt = document.getElementById('ig-prompt').value.trim();
  if (!prompt) { toast('Enter a prompt first', 'warning'); return; }

  toast('Searching...', 'info', 2000);
  try {
    const response = await authFetch('/api/image/search-reference', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt, limit: 8 }),
    });
    const data = await response.json();
    if (!data.urls || data.urls.length === 0) { toast('No images found', 'info'); return; }
    showRefPicker(data.urls);
  } catch (err) {
    toast('Search failed: ' + err.message, 'error');
  }
}

function showRefPicker(urls) {
  // Remove any existing picker
  document.querySelectorAll('.ig-ref-picker-overlay').forEach(el => el.remove());

  // ── Overlay ──
  const overlay = document.createElement('div');
  overlay.className = 'ig-ref-picker-overlay modal-overlay';
  overlay.onclick = (event) => { if (event.target === overlay) overlay.remove(); };

  // ── Panel ──
  const panel = document.createElement('div');
  panel.className = 'ig-ref-picker';

  const heading = document.createElement('h2');
  heading.textContent = 'Pick a Reference Image';
  panel.appendChild(heading);

  // ── Image grid ──
  const grid = document.createElement('div');
  grid.className = 'ig-ref-grid';

  for (const url of urls) {
    const img = document.createElement('img');
    img.src      = url;
    img.loading  = 'lazy';
    img.alt      = 'Reference';
    img.onerror  = () => img.remove();

    img.onclick = () => {
      fetch(url)
        .then(response => response.blob())
        .then(blob => {
          const reader = new FileReader();
          reader.onload = (event) => {
            igRefImageData = event.target.result;

            const preview     = document.getElementById('ig-ref-preview');
            const placeholder = document.getElementById('ig-ref-placeholder');
            const clearBtn    = document.getElementById('ig-ref-clear');
            const controls    = document.getElementById('ig-ref-controls');

            preview.src = igRefImageData;
            preview.classList.remove('hidden');
            if (placeholder) placeholder.classList.add('hidden');
            clearBtn.classList.remove('hidden');
            if (controls) controls.style.display = '';

            toast('Reference selected', 'success');
            overlay.remove();
          };
          reader.readAsDataURL(blob);
        })
        .catch(() => toast('Failed to load', 'error'));
    };

    grid.appendChild(img);
  }
  panel.appendChild(grid);

  // ── Close button ──
  const closeBtn = document.createElement('button');
  closeBtn.className   = 'modal-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.onclick     = () => overlay.remove();
  panel.appendChild(closeBtn);

  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

// ── ControlNet ───────────────────────────────────────────────────
function initControlNetDropzone() {
  const dropzone = document.getElementById('ig-cn-dropzone');
  const input    = document.getElementById('ig-cn-input');
  if (!dropzone || !input) return;

  dropzone.onclick = (event) => {
    if (event.target.tagName !== 'BUTTON') input.click();
  };

  input.onchange = () => {
    if (input.files.length) loadControlNetImage(input.files[0]);
    input.value = '';
  };

  dropzone.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropzone.classList.add('drag-over');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('drag-over');
  });

  dropzone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropzone.classList.remove('drag-over');
    if (event.dataTransfer.files.length) loadControlNetImage(event.dataTransfer.files[0]);
  });
}

function loadControlNetImage(file) {
  if (!file.type.startsWith('image/')) return;
  if (file.size > 20 * 1024 * 1024) { toast('Max 20MB', 'warning'); return; }

  const reader = new FileReader();
  reader.onload = (event) => {
    igControlNetData = event.target.result;

    const preview = document.getElementById('ig-cn-preview');
    preview.src = igControlNetData;
    preview.classList.remove('hidden');
    document.getElementById('ig-cn-clear').classList.remove('hidden');

    toast('Control image loaded', 'success');
  };
  reader.readAsDataURL(file);
}

function clearControlNetImage() {
  igControlNetData = null;

  const preview = document.getElementById('ig-cn-preview');
  preview.src = '';
  preview.classList.add('hidden');
  document.getElementById('ig-cn-clear').classList.add('hidden');
}

// ── Inpainting ───────────────────────────────────────────────────
function openInpaintMode() {
  const previewImg = document.getElementById('ig-preview-img');
  if (!previewImg || !previewImg.src || previewImg.classList.contains('hidden')) {
    toast('Generate or upload an image first', 'warning');
    return;
  }

  const overlay = document.getElementById('ig-inpaint-overlay');
  const canvas  = document.getElementById('ig-inpaint-canvas');
  const wrap    = canvas.parentElement;

  overlay.classList.remove('hidden');
  igInpaintMode = true;

  // Size canvas to match image, scaled to fit container
  const sourceImg = new Image();
  sourceImg.onload = () => {
    const maxWidth = wrap.clientWidth || 512;
    const scale    = Math.min(maxWidth / sourceImg.width, 1);

    canvas.width  = Math.round(sourceImg.width  * scale);
    canvas.height = Math.round(sourceImg.height * scale);

    igInpaintCtx = canvas.getContext('2d');
    igInpaintCtx.drawImage(sourceImg, 0, 0, canvas.width, canvas.height);

    // ── Brush drawing state ──
    let isDrawing = false;

    canvas.onpointerdown  = (event) => { isDrawing = true; drawBrush(event); };
    canvas.onpointermove  = (event) => { if (isDrawing) drawBrush(event); };
    canvas.onpointerup    = () => { isDrawing = false; };
    canvas.onpointerleave = () => { isDrawing = false; };

    function drawBrush(event) {
      const rect      = canvas.getBoundingClientRect();
      const canvasX   = (event.clientX - rect.left) * (canvas.width  / rect.width);
      const canvasY   = (event.clientY - rect.top)  * (canvas.height / rect.height);
      const brushSize = parseInt(document.getElementById('ig-inpaint-brush').value);

      igInpaintCtx.globalCompositeOperation = 'source-over';
      igInpaintCtx.fillStyle = 'rgba(255, 255, 255, 0.6)';
      igInpaintCtx.beginPath();
      igInpaintCtx.arc(canvasX, canvasY, brushSize / 2, 0, Math.PI * 2);
      igInpaintCtx.fill();
    }
  };
  sourceImg.src = previewImg.src;
}

function closeInpaintMode() {
  document.getElementById('ig-inpaint-overlay').classList.add('hidden');
  igInpaintMode = false;
  igInpaintCtx  = null;
}

function clearInpaintMask() {
  if (!igInpaintCtx) return;

  const canvas     = document.getElementById('ig-inpaint-canvas');
  const previewImg = document.getElementById('ig-preview-img');
  const sourceImg  = new Image();

  sourceImg.onload = () => {
    igInpaintCtx.clearRect(0, 0, canvas.width, canvas.height);
    igInpaintCtx.drawImage(sourceImg, 0, 0, canvas.width, canvas.height);
  };
  sourceImg.src = previewImg.src;
}

async function generateInpaint() {
  const canvas = document.getElementById('ig-inpaint-canvas');
  if (!canvas || !igInpaintCtx || igGenerating) return;

  // ── Setup loading state ──
  igGenerating = true;
  const generateBtn = document.getElementById('ig-generate-btn');
  const loadingEl   = document.getElementById('ig-loading');

  if (generateBtn) { generateBtn.disabled = true; generateBtn.textContent = 'Inpainting...'; }
  if (loadingEl)   loadingEl.classList.remove('hidden');

  // ── Build mask canvas ──
  // White = regions to edit, black = regions to keep
  const maskCanvas = document.createElement('canvas');
  maskCanvas.width  = canvas.width;
  maskCanvas.height = canvas.height;
  const maskCtx = maskCanvas.getContext('2d');

  const paintedPixels = igInpaintCtx.getImageData(0, 0, canvas.width, canvas.height);

  // Use the selected target dimensions, not canvas preview dimensions
  const targetWidth  = parseInt(document.getElementById('ig-width').value)  || 1024;
  const targetHeight = parseInt(document.getElementById('ig-height').value) || 1024;

  // ── Diff painted canvas against original to derive mask ──
  const previewImg = document.getElementById('ig-preview-img');
  const origImg    = new Image();

  origImg.onload = async () => {
    const origCanvas = document.createElement('canvas');
    origCanvas.width  = canvas.width;
    origCanvas.height = canvas.height;
    const origCtx = origCanvas.getContext('2d');
    origCtx.drawImage(origImg, 0, 0, canvas.width, canvas.height);
    const origPixels = origCtx.getImageData(0, 0, canvas.width, canvas.height);

    // Pixels that differ from original become white (edit region)
    const maskData = maskCtx.createImageData(canvas.width, canvas.height);
    for (let pixelIndex = 0; pixelIndex < paintedPixels.data.length; pixelIndex += 4) {
      const diff = Math.abs(paintedPixels.data[pixelIndex]   - origPixels.data[pixelIndex])
                 + Math.abs(paintedPixels.data[pixelIndex+1] - origPixels.data[pixelIndex+1])
                 + Math.abs(paintedPixels.data[pixelIndex+2] - origPixels.data[pixelIndex+2]);

      const isMasked = diff > 30; // threshold
      maskData.data[pixelIndex]   = isMasked ? 255 : 0;
      maskData.data[pixelIndex+1] = isMasked ? 255 : 0;
      maskData.data[pixelIndex+2] = isMasked ? 255 : 0;
      maskData.data[pixelIndex+3] = 255;
    }
    maskCtx.putImageData(maskData, 0, 0);

    const maskDataUri = maskCanvas.toDataURL('image/png');
    const srcDataUri  = previewImg.src.startsWith('blob:')
      ? origCanvas.toDataURL('image/png')
      : previewImg.src;

    // ── Submit inpaint request ──
    const prompt = document.getElementById('ig-prompt').value.trim()
      || 'fill in the masked region naturally';

    toast('Generating inpaint...', 'info', 2000);
    try {
      const body = {
        prompt,
        model:           igSelectedModel,
        backend:         igSelectedBackend,
        width:           targetWidth,
        height:          targetHeight,
        reference_image: srcDataUri,
        mask:            maskDataUri,
        denoise:         parseFloat(document.getElementById('ig-denoise').value || '0.75'),
      };

      const response = await authFetch('/api/image/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);

      const submitted = await response.json();
      if (submitted.jobId) await pollAndDisplay(submitted.jobId, prompt);
    } catch (err) {
      toast('Inpaint failed: ' + err.message, 'error');
    } finally {
      igGenerating = false;
      closeInpaintMode();
      if (generateBtn) { generateBtn.disabled = false; generateBtn.textContent = 'Generate'; }
      if (loadingEl)   loadingEl.classList.add('hidden');
    }
  };
  origImg.src = previewImg.src;
}

// ── Aspect Ratio / Resolution ────────────────────────────────────
function updateIgDimensions() {
  const aspect    = document.getElementById('ig-aspect').value;
  const customRow = document.getElementById('ig-custom-dims');

  if (aspect === 'custom') {
    customRow.style.display = '';
    return;
  }
  customRow.style.display = 'none';

  const base = igSelectedModel === 'sdxl_turbo' ? 512 : 1024;
  const dims = {
    '1:1':  [base,                          base],
    '16:9': [base,                          Math.round(base * 9  / 16)],
    '9:16': [Math.round(base * 9 / 16),     base],
    '4:3':  [base,                          Math.round(base * 3  / 4)],
    '3:2':  [base,                          Math.round(base * 2  / 3)],
  };
  const [width, height] = dims[aspect] || [base, base];

  document.getElementById('ig-width').value  = width;
  document.getElementById('ig-height').value = height;
}

// ── Model Selection ──────────────────────────────────────────────
function selectIgModel(btn) {
  if (btn.classList.contains('disabled')) {
    toast('Model unavailable — requires HuggingFace token', 'warning');
    return;
  }

  document.querySelectorAll('.ig-model-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  igSelectedModel   = btn.dataset.model;
  igSelectedBackend = 'comfyui';
  checkComfyUIStatus();

  // Adjust resolution defaults for lower-capability models
  if (igSelectedModel === 'sdxl_turbo' || SD15_MODELS.has(igSelectedModel)) {
    document.getElementById('ig-width').value  = '512';
    document.getElementById('ig-height').value = '512';
    const res4kOption = document.getElementById('ig-resolution').querySelector('[value="4096"]');
    if (res4kOption) res4kOption.disabled = SD15_MODELS.has(igSelectedModel);
  } else {
    document.getElementById('ig-width').value  = '1024';
    document.getElementById('ig-height').value = '1024';
    const res4kOption = document.getElementById('ig-resolution').querySelector('[value="4096"]');
    if (res4kOption) res4kOption.disabled = false;
  }

  updateIgDimensions();
}

// ── ComfyUI Status ───────────────────────────────────────────────
export async function checkComfyUIStatus() {
  const statusDot  = document.querySelector('.ig-status-dot');
  const statusText = document.getElementById('ig-status-text');

  try {
    const response = await authFetch('/api/image/status');
    const data     = await response.json();

    if (data.ok) {
      statusDot.className = 'ig-status-dot';

      if (data.backend === 'huggingface') {
        // HF fallback — enable all models (mapped server-side)
        document.querySelectorAll('.ig-model-btn[data-model]').forEach(btn => {
          if (!btn.dataset.backend) btn.classList.remove('disabled');
        });
        statusText.textContent = 'HuggingFace API ready';

      } else {
        // ComfyUI — query available models and enable/disable buttons accordingly
        try {
          const modelsResponse = await authFetch('/api/image/models');
          if (modelsResponse.ok) {
            const modelData   = await modelsResponse.json();
            const checkpoints = new Set(modelData.checkpoints || []);
            const unets       = new Set(modelData.unets       || []);
            let availableCount = 0;

            document.querySelectorAll('.ig-model-btn[data-model]').forEach(btn => {
              if (btn.dataset.backend) return;
              const ckpt  = btn.dataset.ckpt;
              const unet  = btn.dataset.unet;
              const found = (ckpt && checkpoints.has(ckpt)) || (unet && unets.has(unet));
              btn.classList.toggle('disabled', !found);
              if (found) availableCount++;
            });

            statusText.textContent = availableCount + ' models ready'
              + (data.gpu ? ' \u2014 ' + data.gpu : '');
          } else {
            statusText.textContent = 'ComfyUI ready' + (data.gpu ? ' \u2014 ' + data.gpu : '');
          }
        } catch {
          statusText.textContent = 'ComfyUI ready' + (data.gpu ? ' \u2014 ' + data.gpu : '');
        }
      }

    } else {
      statusDot.className    = 'ig-status-dot error';
      statusText.textContent = data.error || 'No image backend';
    }
  } catch {
    statusDot.className    = 'ig-status-dot error';
    statusText.textContent = 'Cannot reach backend';
  }
}

// ── Generate ─────────────────────────────────────────────────────
async function generateImage() {
  const prompt = document.getElementById('ig-prompt').value.trim();
  if (!prompt || igGenerating) return;

  // ── Setup loading state ──
  igGenerating = true;
  const generateBtn  = document.getElementById('ig-generate-btn');
  const loadingEl    = document.getElementById('ig-loading');
  const loadingText  = document.getElementById('ig-loading-text');
  const statusDot    = document.querySelector('.ig-status-dot');

  if (generateBtn)  { generateBtn.disabled = true; generateBtn.textContent = 'Generating...'; }
  if (loadingEl)    loadingEl.classList.remove('hidden');
  if (statusDot)    statusDot.className = 'ig-status-dot busy';
  loadingText.textContent = 'Generating with '
    + (MODEL_NAMES[igSelectedModel] || igSelectedModel) + '...';

  // ── Build request body ──
  const count = parseInt(document.getElementById('ig-count').value) || 1;
  const body  = {
    prompt,
    model:   igSelectedModel,
    backend: igSelectedBackend,
    count,
    width:   parseInt(document.getElementById('ig-width').value),
    height:  parseInt(document.getElementById('ig-height').value),
  };

  const negativePrompt = document.getElementById('ig-negative').value.trim();
  if (negativePrompt) body.negative_prompt = negativePrompt;

  if (igSelectedBackend === 'comfyui') {
    const steps = document.getElementById('ig-steps').value;
    if (steps) body.steps = parseInt(steps);
    const seed = document.getElementById('ig-seed').value;
    if (seed)  body.seed  = parseInt(seed);
  }

  if (igRefImageData) {
    body.reference_image = igRefImageData;
    body.denoise = parseFloat(document.getElementById('ig-denoise').value);
  }

  const targetResolution = parseInt(document.getElementById('ig-resolution').value);
  if (targetResolution > 1024) body.upscale_to = targetResolution;

  // ControlNet
  const controlNetType = document.getElementById('ig-cn-type').value;
  if (controlNetType !== 'none' && igControlNetData && igSelectedBackend === 'comfyui') {
    body.controlnet_type     = controlNetType;
    body.controlnet_image    = igControlNetData;
    body.controlnet_strength = parseFloat(document.getElementById('ig-cn-strength').value);
  }

  // ── Submit and poll ──
  try {
    const response = await authFetch('/api/image/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const errorText = await response.text();
      let errorMessage;
      try { errorMessage = JSON.parse(errorText).error; }
      catch { errorMessage = errorText.slice(0, 200); }
      throw new Error(errorMessage || 'HTTP ' + response.status);
    }

    const submitted = await response.json();
    if (!submitted.jobId) throw new Error(submitted.error || 'No job ID');

    // Capture model at submission time so poll labels stay correct
    await pollAndDisplay(submitted.jobId, prompt, igSelectedModel);
    statusDot.className = 'ig-status-dot';

  } catch (err) {
    if (err.message === 'Not authenticated') return;
    statusDot.className = 'ig-status-dot error';
    document.getElementById('ig-status-text').textContent = err.message || 'Failed';

  } finally {
    igGenerating            = false;
    generateBtn.disabled    = false;
    generateBtn.textContent = 'Generate';
    loadingEl.classList.add('hidden');
  }
}

// ── Poll + Display ───────────────────────────────────────────────
async function pollAndDisplay(jobId, prompt, submittedModel) {
  const loadingText      = document.getElementById('ig-loading-text');
  const targetResolution = parseInt(document.getElementById('ig-resolution').value);
  const modelForLabels   = submittedModel || igSelectedModel;

  let job = null;
  const maxPolls = 180;

  // ── Poll until done or timeout ──
  for (let pollCount = 0; pollCount < maxPolls; pollCount++) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    if (!igGenerating && !igInpaintMode) break;

    try {
      const pollResponse = await authFetch('/api/image/job/' + jobId);
      job = await pollResponse.json();

      if (job.status === 'done' || job.status === 'error') break;

      if (job.status === 'upscaling') {
        loadingText.textContent = 'Upscaling to ' + targetResolution + 'px...';
      } else {
        loadingText.textContent = 'Generating with '
          + (MODEL_NAMES[modelForLabels] || modelForLabels)
          + '... (' + (pollCount * 2) + 's)';
      }
    } catch { /* ignore transient poll errors */ }
  }

  if (!job || job.status !== 'done') {
    toast(job ? job.error || 'Timed out' : 'No response', 'error');
    return;
  }

  // ── Render results ──
  const gallery = document.getElementById('ig-gallery');
  const images  = job.images || [];
  const count   = parseInt(document.getElementById('ig-count').value) || 1;

  if (count > 1 && images.length > 1) {
    // Batch: wrap multiple results in a comparison grid
    const batchGrid = document.createElement('div');
    batchGrid.className = 'ig-batch-grid ig-batch-' + Math.min(images.length, 4);
    for (let index = 0; index < images.length; index++) {
      const card = await makeImageCard(images[index], prompt, index, modelForLabels);
      batchGrid.appendChild(card);
    }
    gallery.prepend(batchGrid);
  } else {
    for (let index = 0; index < images.length; index++) {
      const card = await makeImageCard(images[index], prompt, index, modelForLabels);
      gallery.prepend(card);
    }
  }

  // Show inpaint button now that we have a generated image
  const inpaintBtn = document.getElementById('ig-inpaint-btn');
  if (inpaintBtn && images.length > 0) inpaintBtn.classList.remove('hidden');

  // Cap gallery at 50 entries, revoking blob URLs to avoid memory leaks
  while (gallery.children.length > 50) {
    const oldCard  = gallery.lastElementChild;
    const oldImage = oldCard ? oldCard.querySelector('img') : null;
    if (oldImage && oldImage.src && oldImage.src.startsWith('blob:')) {
      URL.revokeObjectURL(oldImage.src);
    }
    if (oldCard) oldCard.remove();
  }
}

// ── Image Card ───────────────────────────────────────────────────
async function makeImageCard(imageData, prompt, index, modelId) {
  // ── Card container ──
  const card = document.createElement('div');
  card.className = 'ig-image-card';

  // ── Image element ──
  const imgEl = document.createElement('img');
  if (imageData.url) {
    try {
      const blobResponse = await authFetch(imageData.url);
      const blob         = await blobResponse.blob();
      imgEl.src          = URL.createObjectURL(blob);
    } catch {
      imgEl.src = imageData.url;
    }
  } else if (imageData.data) {
    imgEl.src = 'data:' + (imageData.mimeType || 'image/jpeg') + ';base64,' + imageData.data;
  }
  imgEl.loading = 'lazy';
  imgEl.alt     = prompt.slice(0, 100);
  card.appendChild(imgEl);

  // ── Metadata label ──
  const infoEl = document.createElement('div');
  infoEl.className   = 'ig-image-info';
  const displayModel = modelId || igSelectedModel;
  infoEl.textContent = (MODEL_NAMES[displayModel] || displayModel)
    + ' \u00b7 ' + prompt.slice(0, 60);
  card.appendChild(infoEl);

  // ── Preview sync ──
  const savedFilename = imageData.savedAs || displayModel + '_' + Date.now() + '.jpg';
  if (index === 0) {
    setPreviewImage(imgEl.src, savedFilename, displayModel, prompt);
  }
  card.addEventListener('dblclick', () => {
    setPreviewImage(imgEl.src, savedFilename, displayModel, prompt);
  });

  // ── "Use as Reference" button ──
  const useRefBtn = document.createElement('button');
  useRefBtn.className   = 'ig-use-ref-btn';
  useRefBtn.textContent = 'Use as Ref';
  useRefBtn.onclick = (event) => {
    event.stopPropagation();

    if (!imgEl.src.startsWith('blob:')) {
      igRefImageData = imgEl.src;
      toast('Set as reference', 'success');
    } else {
      // Convert blob URL to data URI before storing
      fetch(imgEl.src)
        .then(response => response.blob())
        .then(blob => {
          const reader = new FileReader();
          reader.onload = () => {
            igRefImageData = reader.result;
            toast('Set as reference', 'success');
          };
          reader.readAsDataURL(blob);
        });
    }
  };
  card.appendChild(useRefBtn);

  return card;
}

// ── Preview + Download ───────────────────────────────────────────
function setPreviewImage(src, filename, model, prompt) {
  const previewContainer = document.getElementById('ig-preview');
  const previewImg       = document.getElementById('ig-preview-img');
  const actionsEl        = document.getElementById('ig-preview-actions');
  const placeholderEl    = document.getElementById('ig-preview-placeholder');
  const metaEl           = document.getElementById('ig-preview-meta');
  const inpaintBtn       = document.getElementById('ig-inpaint-btn');

  // Revoke any previous blob URL
  if (previewImg.src && previewImg.src.startsWith('blob:')) {
    URL.revokeObjectURL(previewImg.src);
  }

  // Load image — proxy through authFetch if it's an API path
  if (src.startsWith('/api/')) {
    authFetch(src)
      .then(response => response.blob())
      .then(blob => { previewImg.src = URL.createObjectURL(blob); })
      .catch(() => { previewImg.src = src; });
  } else {
    previewImg.src = src;
  }

  previewImg.dataset.filename = filename || '';
  previewImg.classList.remove('hidden');

  // ── Lightbox on click ──
  previewImg.onclick = () => {
    document.querySelectorAll('.ig-lightbox').forEach(el => el.remove());

    const lightbox = document.createElement('div');
    lightbox.className = 'ig-lightbox';
    lightbox.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:9999',
      'background:rgba(0,0,0,.9)', 'display:flex',
      'align-items:center', 'justify-content:center', 'cursor:pointer',
    ].join(';');

    const lightboxImg = document.createElement('img');
    lightboxImg.src        = src;
    lightboxImg.style.cssText = 'max-width:90vw;max-height:90vh;border-radius:8px';
    lightbox.appendChild(lightboxImg);

    const onEscKey = (event) => {
      if (event.key === 'Escape') {
        lightbox.remove();
        document.removeEventListener('keydown', onEscKey);
      }
    };
    document.addEventListener('keydown', onEscKey);
    lightbox.onclick = () => {
      lightbox.remove();
      document.removeEventListener('keydown', onEscKey);
    };

    document.body.appendChild(lightbox);
  };

  // ── Update surrounding UI ──
  actionsEl.classList.remove('hidden');
  if (placeholderEl) placeholderEl.classList.add('hidden');
  previewContainer.classList.remove('ig-preview-empty');
  metaEl.textContent = (MODEL_NAMES[model] || model) + ' \u00b7 ' + (prompt || '').slice(0, 60);

  if (inpaintBtn) inpaintBtn.classList.remove('hidden');
}

function downloadCurrentImage() {
  const previewImg = document.getElementById('ig-preview-img');
  if (!previewImg || !previewImg.src) return;

  const filename = previewImg.dataset.filename || 'image_' + Date.now() + '.jpg';

  if (previewImg.dataset.filename) {
    authFetch('/api/image/download/' + encodeURIComponent(previewImg.dataset.filename))
      .then(response => response.blob())
      .then(blob => {
        const objectUrl = URL.createObjectURL(blob);
        const anchor    = document.createElement('a');
        anchor.href     = objectUrl;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(objectUrl);
      })
      .catch(() => downloadFromSrc(previewImg.src, filename));
  } else {
    downloadFromSrc(previewImg.src, filename);
  }
}

function downloadFromSrc(src, filename) {
  if (src.startsWith('blob:') || src.startsWith('data:')) {
    const anchor    = document.createElement('a');
    anchor.href     = src;
    anchor.download = filename;
    anchor.click();
  } else {
    const fetchPromise = src.startsWith('/api/') ? authFetch(src) : fetch(src);
    fetchPromise
      .then(response => response.blob())
      .then(blob => {
        const objectUrl = URL.createObjectURL(blob);
        const anchor    = document.createElement('a');
        anchor.href     = objectUrl;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(objectUrl);
      });
  }
}
