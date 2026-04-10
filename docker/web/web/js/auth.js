'use strict';
import { state, emit } from './state.js';
import { toast } from './toasts.js';

export function showAuthScreen() {
  document.getElementById('auth-screen').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
}

export function hideAuthScreen() {
  document.getElementById('auth-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
}

export function showLogin() {
  document.getElementById('auth-login').classList.remove('hidden');
  document.getElementById('auth-register').classList.add('hidden');
  document.getElementById('auth-sub').textContent = 'Sign in to continue';
  clearAuthMsg();
}

export function showRegister() {
  document.getElementById('auth-login').classList.add('hidden');
  document.getElementById('auth-register').classList.remove('hidden');
  document.getElementById('auth-sub').textContent = 'Request access';
  clearAuthMsg();
}

function showAuthMsg(text, isError) {
  const el = document.getElementById('auth-msg');
  el.textContent = text;
  el.className = isError ? 'error' : 'success';
}

function clearAuthMsg() {
  const el = document.getElementById('auth-msg');
  el.textContent = '';
  el.className = 'hidden';
}

export async function checkAuth() {
  const token = localStorage.getItem('dartboard-jwt');
  if (!token) {
    showAuthScreen();
    return false;
  }

  try {
    const res = await fetch('/auth/verify', {
      headers: { 'Authorization': 'Bearer ' + token },
    });
    if (res.ok) {
      hideAuthScreen();
      return true;
    }
  } catch {}

  localStorage.removeItem('dartboard-jwt');
  showAuthScreen();
  return false;
}

export async function authLogin() {
  const username = document.getElementById('login-user').value.trim();
  const password = document.getElementById('login-pass').value;

  if (!username || !password) {
    showAuthMsg('Enter username and password', true);
    return;
  }

  const btn = document.getElementById('login-btn');
  btn.disabled = true;
  let banned = false;

  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();

    if (!res.ok) {
      showAuthMsg(data.error || 'Login failed', true);
      if (res.status === 403 && (data.error || '').includes('banned')) {
        banned = true;
        document.getElementById('login-user').disabled = true;
        document.getElementById('login-pass').disabled = true;
      }
      return;
    }

    if (!data.token || data.token.length < 10) {
      showAuthMsg('Server returned invalid token', true);
      return;
    }

    localStorage.setItem('dartboard-jwt', data.token);
    document.cookie = 'dartboard_token=' + data.token
      + '; path=/; SameSite=Strict; Secure; max-age=' + (60 * 60 * 24 * 7);

    hideAuthScreen();
    emit('auth:login');
    toast('Welcome back, ' + username, 'success');
  } catch (err) {
    showAuthMsg('Connection error', true);
  } finally {
    if (!banned) btn.disabled = false;
  }
}

export async function authRegister() {
  const username = document.getElementById('reg-user').value.trim();
  const password = document.getElementById('reg-pass').value;
  const passwordConfirm = document.getElementById('reg-pass2').value;

  if (!username || !password) {
    showAuthMsg('Fill in all fields', true);
    return;
  }
  if (password !== passwordConfirm) {
    showAuthMsg('Passwords do not match', true);
    return;
  }

  const btn = document.getElementById('reg-btn');
  btn.disabled = true;

  try {
    const res = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();

    if (!res.ok) {
      showAuthMsg(data.error || 'Registration failed', true);
      return;
    }

    showAuthMsg(data.message || 'Registration submitted', false);
    document.getElementById('reg-user').value = '';
    document.getElementById('reg-pass').value = '';
    document.getElementById('reg-pass2').value = '';
  } catch (err) {
    showAuthMsg('Connection error', true);
  } finally {
    btn.disabled = false;
  }
}

export function authLogout() {
  emit('auth:beforeLogout');

  localStorage.removeItem('dartboard-jwt');
  document.cookie = 'dartboard_token=; path=/; max-age=0';

  // Reset application state
  state.currentConvId = null;
  state.allConversations = [];
  state.availableModels = [];
  state.selectedModel = null;
  state.selectedModelReady = false;
  state.pendingFiles = [];

  // Clear UI
  document.getElementById('messages').textContent = '';
  document.getElementById('conv-list').textContent = '';

  // Reset login form
  document.getElementById('login-user').value = '';
  document.getElementById('login-pass').value = '';
  document.getElementById('login-user').disabled = false;
  document.getElementById('login-pass').disabled = false;
  document.getElementById('login-btn').disabled = false;

  clearAuthMsg();
  showAuthScreen();
  emit('view:welcome');
}

export function initAuthKeys() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    if (document.getElementById('auth-screen').classList.contains('hidden')) return;

    if (!document.getElementById('auth-register').classList.contains('hidden')) {
      authRegister();
    } else {
      authLogin();
    }
  });
}
