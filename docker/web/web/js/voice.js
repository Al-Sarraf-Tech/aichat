'use strict';
import { emit } from './state.js';
import { toast } from './toasts.js';

// ── Voice Input (Web Speech API) ─────────────────────────────────
let recognition = null;
let isListening = false;
let _baseText = '';      // text in the input before voice session started

export function initVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return; // Not supported

  const btn = document.getElementById('voice-btn');
  if (!btn) return;
  btn.classList.remove('hidden');

  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  recognition.onresult = (e) => {
    const input = document.getElementById('input');
    // Rebuild full transcript from all results (handles interim updates correctly)
    let transcript = '';
    for (let i = 0; i < e.results.length; i++) {
      transcript += e.results[i][0].transcript;
    }
    if (transcript) {
      // Replace interim text each time; _baseText holds what was in the box before
      const sep = _baseText && !_baseText.endsWith(' ') ? ' ' : '';
      input.value = _baseText + sep + transcript;
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 200) + 'px';
      emit('input:changed');
    }
  };

  recognition.onend = () => {
    isListening = false;
    btn.classList.remove('listening');
  };

  recognition.onerror = (e) => {
    isListening = false;
    btn.classList.remove('listening');
    if (e.error === 'not-allowed') {
      toast('Microphone access denied', 'error');
    } else if (e.error !== 'aborted') {
      toast('Voice recognition error: ' + e.error, 'warning');
    }
  };

  btn.onclick = toggleVoice;
}

function toggleVoice() {
  const btn = document.getElementById('voice-btn');
  if (isListening) {
    recognition.abort();
    isListening = false;
    btn.classList.remove('listening');
  } else {
    const input = document.getElementById('input');
    _baseText = input ? input.value : '';
    recognition.start();
    isListening = true;
    btn.classList.add('listening');
    toast('Listening...', 'info', 1500);
  }
}
