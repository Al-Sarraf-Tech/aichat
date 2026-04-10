'use strict';
// (no external utils needed)

export function openLightbox(src, allSrcs, startIndex) {
  if (document.querySelector('.lightbox-overlay')) return;

  let currentIndex = startIndex || 0;
  const srcs = allSrcs || [src];

  const overlay = document.createElement('div');
  overlay.className = 'lightbox-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) closeLightbox(); };

  const img = document.createElement('img');
  img.src = srcs[currentIndex];
  img.alt = 'Full size';

  const closeBtn = document.createElement('button');
  closeBtn.className = 'lightbox-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.onclick = closeLightbox;

  overlay.appendChild(img);
  overlay.appendChild(closeBtn);

  let updateCounter = () => {};

  if (srcs.length > 1) {
    const counter = document.createElement('div');
    counter.className = 'lightbox-counter';

    updateCounter = () => {
      counter.textContent = (currentIndex + 1) + ' / ' + srcs.length;
    };

    const prev = document.createElement('button');
    prev.className = 'lightbox-nav prev';
    prev.textContent = '\u2039';
    prev.onclick = (e) => {
      e.stopPropagation();
      currentIndex = (currentIndex - 1 + srcs.length) % srcs.length;
      img.src = srcs[currentIndex];
      updateCounter();
    };

    const next = document.createElement('button');
    next.className = 'lightbox-nav next';
    next.textContent = '\u203A';
    next.onclick = (e) => {
      e.stopPropagation();
      currentIndex = (currentIndex + 1) % srcs.length;
      img.src = srcs[currentIndex];
      updateCounter();
    };

    updateCounter();
    overlay.appendChild(prev);
    overlay.appendChild(next);
    overlay.appendChild(counter);
  }

  document.body.appendChild(overlay);

  const onKey = (e) => {
    if (e.key === 'Escape') {
      closeLightbox();
    } else if (e.key === 'ArrowLeft' && srcs.length > 1) {
      currentIndex = (currentIndex - 1 + srcs.length) % srcs.length;
      img.src = srcs[currentIndex];
      updateCounter();
    } else if (e.key === 'ArrowRight' && srcs.length > 1) {
      currentIndex = (currentIndex + 1) % srcs.length;
      img.src = srcs[currentIndex];
      updateCounter();
    }
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
  let currentRun = [];
  const runs = [];

  for (const node of children) {
    const isImg = node.nodeName === 'IMG' ||
      (node.nodeName === 'P' &&
        node.querySelector('img') &&
        (node.textContent || '').trim().length < 3);

    if (isImg) {
      currentRun.push(node);
    } else {
      if (currentRun.length >= 2) runs.push([...currentRun]);
      currentRun = [];
    }
  }
  if (currentRun.length >= 2) runs.push([...currentRun]);

  for (const imgNodes of runs) {
    const imgs = imgNodes
      .map(node => node.nodeName === 'IMG' ? node : node.querySelector('img'))
      .filter(Boolean);

    if (imgs.length < 2) continue;

    const carousel = document.createElement('div');
    carousel.className = 'img-carousel';

    const track = document.createElement('div');
    track.className = 'carousel-track';

    const counter = document.createElement('div');
    counter.className = 'carousel-counter';

    let carouselIndex = 0;

    const seen = new Set();
    const uniqueImgs = [];
    for (const img of imgs) {
      const src = img.src || '';
      if (!seen.has(src)) {
        seen.add(src);
        uniqueImgs.push(img);
      }
    }

    for (const img of uniqueImgs) {
      const slide = document.createElement('div');
      slide.className = 'carousel-slide';
      const clone = img.cloneNode(true);
      clone.loading = 'lazy';
      clone.onerror = () => { slide.remove(); updateCounter(); };
      clone.onclick = () => window.open(clone.src, '_blank');
      slide.appendChild(clone);
      track.appendChild(slide);
    }

    const btnPrev = document.createElement('button');
    btnPrev.className = 'carousel-btn carousel-prev';
    btnPrev.textContent = '\u2039';

    const btnNext = document.createElement('button');
    btnNext.className = 'carousel-btn carousel-next';
    btnNext.textContent = '\u203A';

    function updateCounter() {
      const total = track.querySelectorAll('.carousel-slide').length;
      counter.textContent = (carouselIndex + 1) + ' / ' + total;
      btnPrev.style.visibility = carouselIndex <= 0 ? 'hidden' : 'visible';
      btnNext.style.visibility = carouselIndex >= total - 1 ? 'hidden' : 'visible';
    }

    function goTo(index) {
      const total = track.querySelectorAll('.carousel-slide').length;
      carouselIndex = Math.max(0, Math.min(index, total - 1));
      track.style.transform = 'translateX(-' + (carouselIndex * 100) + '%)';
      updateCounter();
    }

    btnPrev.onclick = () => goTo(carouselIndex - 1);
    btnNext.onclick = () => goTo(carouselIndex + 1);

    carousel.appendChild(btnPrev);
    carousel.appendChild(track);
    carousel.appendChild(btnNext);
    carousel.appendChild(counter);

    imgNodes[0].parentNode.insertBefore(carousel, imgNodes[0]);
    for (const node of imgNodes) node.remove();

    updateCounter();
  }
}
