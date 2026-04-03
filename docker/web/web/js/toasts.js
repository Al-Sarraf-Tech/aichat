'use strict';

// ── Toast Notification System ────────────────────────────────────
// Usage: toast('Message saved', 'success')
// Types: success, error, info, warning

let _container = null;

function getContainer() {
  if (_container) return _container;
  _container = document.createElement('div');
  _container.id = 'toast-container';
  _container.setAttribute('role', 'status');
  _container.setAttribute('aria-live', 'polite');
  _container.setAttribute('aria-atomic', 'false');
  document.body.appendChild(_container);
  return _container;
}

const ICONS = {
  success: '\u2713',
  error: '\u2717',
  warning: '\u26A0',
  info: '\u2139',
};

const DURATIONS = {
  success: 3000,
  error: 5000,
  warning: 4000,
  info: 3000,
};

export function toast(message, type = 'info', duration) {
  const container = getContainer();
  const el = document.createElement('div');
  el.className = `toast toast-${type} toast-enter`;

  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.textContent = ICONS[type] || ICONS.info;

  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = message;

  const close = document.createElement('button');
  close.className = 'toast-close';
  close.textContent = '\u00d7';
  close.onclick = () => dismiss(el);

  el.appendChild(icon);
  el.appendChild(text);
  el.appendChild(close);
  container.appendChild(el);

  // Trigger enter animation
  requestAnimationFrame(() => {
    el.classList.remove('toast-enter');
    el.classList.add('toast-visible');
  });

  // Auto dismiss
  const ms = duration || DURATIONS[type] || 3000;
  const timer = setTimeout(() => dismiss(el), ms);
  el._timer = timer;

  // Pause on hover
  el.addEventListener('mouseenter', () => clearTimeout(el._timer));
  el.addEventListener('mouseleave', () => {
    el._timer = setTimeout(() => dismiss(el), 2000);
  });

  // Cap at 5 toasts
  while (container.children.length > 5) {
    dismiss(container.firstElementChild);
  }

  return el;
}

function dismiss(el) {
  if (!el || el._dismissed) return;
  el._dismissed = true;
  clearTimeout(el._timer);
  el.classList.remove('toast-visible');
  el.classList.add('toast-exit');
  el.addEventListener('animationend', () => el.remove(), { once: true });
  // Fallback removal
  setTimeout(() => { if (el.parentNode) el.remove(); }, 500);
}
