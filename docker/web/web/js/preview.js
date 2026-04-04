'use strict';
import { renderMd } from './utils.js';

let previewActive = false;

export function initPreview() {
  const btn = document.getElementById('preview-btn');
  if (!btn) return;
  btn.onclick = togglePreview;
}

function togglePreview() {
  const btn = document.getElementById('preview-btn');
  const input = document.getElementById('input');
  let previewEl = document.getElementById('input-preview');
  if (previewActive) {
    if (previewEl) previewEl.classList.add('hidden');
    input.classList.remove('hidden');
    btn.classList.remove('active');
    previewActive = false;
  } else {
    if (!previewEl) {
      previewEl = document.createElement('div');
      previewEl.id = 'input-preview'; previewEl.className = 'input-preview';
      input.parentNode.insertBefore(previewEl, input.nextSibling);
    }
    const text = input.value.trim();
    if (text) {
      previewEl.innerHTML = renderMd(text);
    } else {
      previewEl.textContent = 'Nothing to preview';
      previewEl.style.color = 'var(--text-muted)';
      previewEl.style.fontStyle = 'italic';
    }
    previewEl.classList.remove('hidden');
    input.classList.add('hidden');
    btn.classList.add('active');
    previewActive = true;
  }
}

export function isPreviewActive() { return previewActive; }
