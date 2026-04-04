'use strict';
import { normalizeImageUrl } from './utils.js';

export function openLightbox(src, allSrcs, startIndex) {
  if (document.querySelector('.lightbox-overlay')) return;
  let idx = startIndex || 0;
  const srcs = allSrcs || [src];
  const overlay = document.createElement('div');
  overlay.className = 'lightbox-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeLightbox(); };
  const img = document.createElement('img');
  img.src = srcs[idx]; img.alt = 'Full size';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'lightbox-close'; closeBtn.textContent = '\u00d7';
  closeBtn.onclick = closeLightbox;
  overlay.appendChild(img); overlay.appendChild(closeBtn);
  if (srcs.length > 1) {
    const prev = document.createElement('button'); prev.className = 'lightbox-nav prev'; prev.textContent = '\u2039';
    prev.onclick = (e) => { e.stopPropagation(); idx = (idx - 1 + srcs.length) % srcs.length; img.src = srcs[idx]; updateC(); };
    const next = document.createElement('button'); next.className = 'lightbox-nav next'; next.textContent = '\u203A';
    next.onclick = (e) => { e.stopPropagation(); idx = (idx + 1) % srcs.length; img.src = srcs[idx]; updateC(); };
    const counter = document.createElement('div'); counter.className = 'lightbox-counter';
    function updateC() { counter.textContent = (idx + 1) + ' / ' + srcs.length; }
    updateC();
    overlay.appendChild(prev); overlay.appendChild(next); overlay.appendChild(counter);
  }
  document.body.appendChild(overlay);
  const onKey = (e) => {
    if (e.key === 'Escape') closeLightbox();
    else if (e.key === 'ArrowLeft' && srcs.length > 1) { idx = (idx - 1 + srcs.length) % srcs.length; img.src = srcs[idx]; }
    else if (e.key === 'ArrowRight' && srcs.length > 1) { idx = (idx + 1) % srcs.length; img.src = srcs[idx]; }
  };
  document.addEventListener('keydown', onKey);
  overlay._onKey = onKey;
}

export function closeLightbox() {
  const overlay = document.querySelector('.lightbox-overlay');
  if (!overlay) return;
  if (overlay._onKey) document.removeEventListener('keydown', overlay._onKey);
  overlay.remove();
}

export function buildImageCarousels(el) {
  const children = Array.from(el.childNodes);
  let run = []; const runs = [];
  for (const node of children) {
    const isImg = node.nodeName === 'IMG' || (node.nodeName === 'P' && node.querySelector('img') && (node.textContent || '').trim().length < 3);
    if (isImg) { run.push(node); } else { if (run.length >= 2) runs.push([...run]); run = []; }
  }
  if (run.length >= 2) runs.push([...run]);
  for (const imgNodes of runs) {
    const imgs = imgNodes.map(n => n.nodeName === 'IMG' ? n : n.querySelector('img')).filter(Boolean);
    if (imgs.length < 2) continue;
    const carousel = document.createElement('div'); carousel.className = 'img-carousel';
    const track = document.createElement('div'); track.className = 'carousel-track';
    const counter = document.createElement('div'); counter.className = 'carousel-counter';
    let cidx = 0;
    const seen = new Set(); const uniqueImgs = [];
    for (const img of imgs) { const s = img.src || ''; if (!seen.has(s)) { seen.add(s); uniqueImgs.push(img); } }
    for (const img of uniqueImgs) {
      const slide = document.createElement('div'); slide.className = 'carousel-slide';
      const clone = img.cloneNode(true); clone.loading = 'lazy';
      clone.onerror = () => { slide.remove(); updateCnt(); };
      clone.onclick = () => window.open(clone.src, '_blank');
      slide.appendChild(clone); track.appendChild(slide);
    }
    const btnL = document.createElement('button'); btnL.className = 'carousel-btn carousel-prev'; btnL.textContent = '\u2039';
    const btnR = document.createElement('button'); btnR.className = 'carousel-btn carousel-next'; btnR.textContent = '\u203A';
    function updateCnt() { const t = track.querySelectorAll('.carousel-slide').length; counter.textContent = (cidx+1)+' / '+t; btnL.style.visibility = cidx<=0?'hidden':'visible'; btnR.style.visibility = cidx>=t-1?'hidden':'visible'; }
    function goTo(i) { const t = track.querySelectorAll('.carousel-slide').length; cidx = Math.max(0, Math.min(i, t-1)); track.style.transform = 'translateX(-'+(cidx*100)+'%)'; updateCnt(); }
    btnL.onclick = () => goTo(cidx-1); btnR.onclick = () => goTo(cidx+1);
    carousel.appendChild(btnL); carousel.appendChild(track); carousel.appendChild(btnR); carousel.appendChild(counter);
    imgNodes[0].parentNode.insertBefore(carousel, imgNodes[0]);
    for (const n of imgNodes) n.remove();
    updateCnt();
  }
}
