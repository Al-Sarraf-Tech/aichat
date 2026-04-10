'use strict';
import { state, emit } from './state.js';
import { authFetch, esc, renderMd, scrollToBottom, cleanResponse, normalizeImageUrl } from './utils.js';
import { makeToolCard, createThinkingCard } from './tools.js';
import { openLightbox, buildImageCarousels } from './lightbox.js';
import { loadConversations } from './conversations.js';
import { toast } from './toasts.js';
import { MODELS } from './models.js';
import { tightenBubble, estimateMessageHeight } from './layout.js';

export function postProcess(el) {
  if (!el) return;

  // Phase 1 (sync): structural wrapping — runs before paint to prevent layout shift
  el.querySelectorAll('pre').forEach(pre => {
    if (pre.closest('.code-block')) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'code-block';
    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(pre);

    const header = document.createElement('div');
    header.className = 'code-header';

    const codeEl = pre.querySelector('code');
    const langMatch = codeEl && codeEl.className
      ? codeEl.className.match(/language-(\S+)/)
      : null;

    const langSpan = document.createElement('span');
    langSpan.className = 'code-lang';
    langSpan.textContent = langMatch ? langMatch[1] : '';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-btn';
    copyBtn.textContent = 'Copy';
    copyBtn.onclick = () => {
      navigator.clipboard.writeText(codeEl ? codeEl.textContent : '').then(() => {
        copyBtn.textContent = 'Copied!';
        copyBtn.classList.add('copied');
        setTimeout(() => {
          copyBtn.textContent = 'Copy';
          copyBtn.classList.remove('copied');
        }, 2000);
      });
    };

    header.appendChild(langSpan);
    header.appendChild(copyBtn);
    wrapper.insertBefore(header, pre);
  });

  buildImageCarousels(el);

  // Phase 2 (async): syntax highlighting — deferred to idle time
  const codeBlocks = Array.from(el.querySelectorAll('pre code'));
  if (codeBlocks.length && typeof hljs !== 'undefined') {
    const highlight = () => {
      try { codeBlocks.forEach(block => hljs.highlightElement(block)); } catch {}
    };
    if (typeof requestIdleCallback !== 'undefined') {
      requestIdleCallback(highlight, { timeout: 2000 });
    } else {
      requestAnimationFrame(highlight);
    }
  }
}

// ── Virtual Scroll State ────────────────────────────────────────
const VBUFFER = 5;            // render this many extra messages above/below viewport
const VIRTUAL_THRESHOLD = 30; // only virtualize conversations with 30+ messages
let _vs = null;               // virtual scroll state (null = non-virtual mode)

export function renderMessages(msgs) {
  const container = document.getElementById('messages');
  container.textContent = '';
  container.classList.remove('virtual');
  _vs = null;

  // Clean up any stale virtual scroll listener
  const chatEl = document.getElementById('chat-view');
  if (chatEl) chatEl.removeEventListener('scroll', _vsOnScroll);

  // Filter displayable messages
  const visible = msgs.filter(m => {
    if (m.role === 'system' || m.role === 'tool') return false;
    if (m.role === 'assistant' && !(m.content || '').trim() && m.tool_calls && m.tool_calls.length > 0) return false;
    return true;
  });

  // Small conversations: render all directly (fast path)
  if (visible.length < VIRTUAL_THRESHOLD) {
    for (const m of visible) {
      appendMessage(m.role, m.content, m.tool_calls, null, m.id, m.created_at);
    }
    scrollToBottom({ immediate: true });
    return;
  }

  // Large conversations: virtual scroll
  const containerWidth = container.offsetWidth || 740;
  const heights = visible.map(m => estimateMessageHeight(m, containerWidth));

  const offsets = [];
  let cumulative = 0;
  for (const h of heights) {
    offsets.push(cumulative);
    cumulative += h;
  }

  _vs = {
    msgs: visible,
    heights,
    offsets,
    totalHeight: cumulative,
    rendered: new Map(),
    container,
  };

  container.classList.add('virtual');
  container.style.height = cumulative + 'px';

  // Scroll to bottom, then render visible window
  const scrollEl = document.getElementById('chat-view');
  if (scrollEl) {
    scrollEl.scrollTop = cumulative; // jump to bottom
    _vsRenderWindow(scrollEl);
    scrollEl.removeEventListener('scroll', _vsOnScroll);
    scrollEl.addEventListener('scroll', _vsOnScroll, { passive: true });
  }
}

function _vsOnScroll() {
  if (!_vs) return;
  const scrollEl = document.getElementById('chat-view');
  if (scrollEl) requestAnimationFrame(() => _vsRenderWindow(scrollEl));
}

function _vsRenderWindow(scrollEl) {
  if (!_vs) return;
  const { msgs, heights, offsets, rendered, container } = _vs;
  const viewTop = scrollEl.scrollTop;
  const viewBottom = viewTop + scrollEl.clientHeight;

  // Binary search for first visible message
  let lo = 0, hi = msgs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (offsets[mid] + heights[mid] < viewTop) lo = mid + 1;
    else hi = mid;
  }
  const windowStart = Math.max(0, lo - VBUFFER);

  // Find last visible message
  let end = lo;
  while (end < msgs.length && offsets[end] < viewBottom) end++;
  const windowEnd = Math.min(msgs.length, end + VBUFFER);

  // Remove out-of-range nodes
  for (const [idx, node] of rendered) {
    if (idx < windowStart || idx >= windowEnd) {
      node.remove();
      rendered.delete(idx);
    }
  }

  // Add newly in-range nodes
  for (let i = windowStart; i < windowEnd; i++) {
    if (rendered.has(i)) continue;

    const m = msgs[i];
    const result = appendMessage(m.role, m.content, m.tool_calls, null, m.id, m.created_at);

    if (result && result.div) {
      result.div.style.position = 'absolute';
      result.div.style.top = offsets[i] + 'px';
      result.div.style.width = '100%';
      result.div.style.left = '0';
      rendered.set(i, result.div);

      // Correct height estimate after real render
      const realHeight = result.div.offsetHeight;
      if (Math.abs(realHeight - heights[i]) > 4) {
        const delta = realHeight - heights[i];
        heights[i] = realHeight;
        for (let j = i + 1; j < offsets.length; j++) offsets[j] += delta;
        _vs.totalHeight += delta;
        container.style.height = _vs.totalHeight + 'px';
      }
    }
  }
}

export function appendMessage(role, content, toolCalls, files, msgId, timestamp) {
  const container = document.getElementById('messages');

  const div = document.createElement('div');
  div.className = 'msg ' + role + ' msg-enter';
  if (msgId) div.dataset.msgId = msgId;

  // Timestamp element (hover = exact, always shown on load)
  const ts = timestamp || new Date().toISOString();
  const timeEl = document.createElement('time');
  timeEl.className = 'msg-time';
  timeEl.dateTime = ts;
  timeEl.textContent = formatMsgTime(ts);
  timeEl.title = new Date(ts).toLocaleString();

  if (role === 'user') {
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    if (files && files.length > 0) {
      const attachmentsEl = document.createElement('div');
      attachmentsEl.className = 'msg-attachments';

      for (const f of files) {
        if (f.preview) {
          const img = document.createElement('img');
          img.src = f.preview;
          img.alt = f.name;
          attachmentsEl.appendChild(img);
        } else {
          const chip = document.createElement('span');
          chip.className = 'msg-attach-file';
          chip.textContent = '\uD83D\uDCC4 ' + f.name;
          attachmentsEl.appendChild(chip);
        }
      }

      bubble.appendChild(attachmentsEl);
    }

    const contentEl = document.createElement('div');
    contentEl.className = 'msg-content';
    contentEl.innerHTML = renderMd(content);

    bubble.appendChild(contentEl);
    bubble.appendChild(createActions(role, content, div));
    bubble.appendChild(timeEl);
    div.appendChild(bubble);
    container.appendChild(div);

    // Tighten user bubble to text width (pretext)
    if (content && container.offsetWidth > 0) {
      const tightWidth = tightenBubble(content, Math.min(container.offsetWidth * 0.75, 600));
      if (tightWidth > 0) bubble.style.maxWidth = tightWidth + 'px';
    }

    return { div, contentEl, bodyEl: bubble };
  }

  if (role === 'assistant') {
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = '\uD83E\uDD16';

    const body = document.createElement('div');
    body.className = 'msg-body';

    const contentEl = document.createElement('div');
    contentEl.className = 'msg-content';

    body.appendChild(contentEl);

    if (content) {
      contentEl.innerHTML = renderMd(content);
      postProcess(contentEl);
    }

    body.appendChild(createActions(role, content, div));
    body.appendChild(timeEl);

    div.appendChild(avatar);
    div.appendChild(body);
    container.appendChild(div);

    // Tighten assistant bubble to text width (pretext) — skip long/markdown-heavy content
    if (content && content.length < 500 && !content.includes('```') && container.offsetWidth > 0) {
      const tightWidth = tightenBubble(content, Math.min(container.offsetWidth * 0.85, 800));
      if (tightWidth > 0) body.style.maxWidth = tightWidth + 'px';
    }

    return { div, contentEl, bodyEl: body };
  }

  return { div, contentEl: null, bodyEl: null };
}

function formatMsgTime(iso) {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now - d;

  if (diffMs < 60000) return 'just now';
  if (diffMs < 3600000) return Math.floor(diffMs / 60000) + 'm ago';

  const isToday = now.toDateString() === d.toDateString();
  if (isToday) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    + ' '
    + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function createActions(role, content, msgDiv) {
  const bar = document.createElement('div');
  bar.className = 'msg-actions';

  // Copy button
  const copyBtn = document.createElement('button');
  copyBtn.className = 'msg-action-btn';
  copyBtn.title = 'Copy';
  copyBtn.textContent = '\uD83D\uDCCB';
  copyBtn.onclick = (e) => {
    e.stopPropagation();
    const contentEl = msgDiv.querySelector('.msg-content');
    navigator.clipboard
      .writeText(contentEl ? contentEl.textContent : content || '')
      .then(() => toast('Copied', 'success'));
  };
  bar.appendChild(copyBtn);

  // Regenerate + Read aloud (assistant only)
  if (role === 'assistant') {
    const regenBtn = document.createElement('button');
    regenBtn.className = 'msg-action-btn';
    regenBtn.title = 'Regenerate';
    regenBtn.textContent = '\uD83D\uDD04';
    regenBtn.onclick = (e) => { e.stopPropagation(); regenLast(); };
    bar.appendChild(regenBtn);

    if ('speechSynthesis' in window) {
      const ttsBtn = document.createElement('button');
      ttsBtn.className = 'msg-action-btn';
      ttsBtn.title = 'Read aloud';
      ttsBtn.textContent = '\uD83D\uDD0A';
      ttsBtn.onclick = (e) => {
        e.stopPropagation();
        if (speechSynthesis.speaking) {
          speechSynthesis.cancel();
          ttsBtn.textContent = '\uD83D\uDD0A';
          return;
        }
        const contentEl = msgDiv.querySelector('.msg-content');
        const text = contentEl ? contentEl.textContent : content || '';
        if (!text.trim()) return;
        const utterance = new SpeechSynthesisUtterance(text.substring(0, 5000));
        utterance.rate = 1.05;
        utterance.pitch = 1.0;
        utterance.onend = () => { ttsBtn.textContent = '\uD83D\uDD0A'; };
        utterance.onerror = () => { ttsBtn.textContent = '\uD83D\uDD0A'; };
        ttsBtn.textContent = '\u23F9';
        speechSynthesis.speak(utterance);
      };
      bar.appendChild(ttsBtn);
    }
  }

  // Edit button (user only) — forks conversation from this point
  if (role === 'user') {
    const editBtn = document.createElement('button');
    editBtn.className = 'msg-action-btn';
    editBtn.title = 'Edit & Fork';
    editBtn.textContent = '\u270F\uFE0F';
    editBtn.onclick = (e) => {
      e.stopPropagation();
      const contentEl = msgDiv.querySelector('.msg-content');
      const text = contentEl ? contentEl.textContent : content || '';

      // Remove this message and all messages after it from the DOM
      const allMsgs = Array.from(document.querySelectorAll('#messages .msg'));
      const idx = allMsgs.indexOf(msgDiv);
      if (idx >= 0) {
        for (let i = allMsgs.length - 1; i >= idx; i--) allMsgs[i].remove();
      }

      // Fill the input with the original text for editing
      const inputEl = document.getElementById('input');
      inputEl.value = text;
      inputEl.focus();
      inputEl.style.height = 'auto';
      inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
      emit('input:changed');
      toast('Forked \u2014 edit and press Enter to continue from here', 'info');
    };
    bar.appendChild(editBtn);
  }

  return bar;
}

function regenLast() {
  if (state.isStreaming || !state.currentConvId) return;
  const userMsgs = document.querySelectorAll('.msg.user');
  if (!userMsgs.length) return;

  const lastUserMsg = userMsgs[userMsgs.length - 1];
  const contentEl = lastUserMsg.querySelector('.msg-content');
  const text = contentEl ? contentEl.textContent : '';
  if (!text.trim()) return;

  const allMsgs = document.querySelectorAll('.msg');
  const lastMsg = allMsgs[allMsgs.length - 1];
  if (lastMsg && lastMsg.classList.contains('assistant')) lastMsg.remove();

  const inputEl = document.getElementById('input');
  inputEl.value = text;
  emit('input:changed');
  emit('send:trigger');
}

export function handleFiles(input) {
  for (const file of Array.from(input.files)) {
    if (file.size > 10 * 1024 * 1024) {
      toast('"' + file.name + '" too large (max 10MB)', 'warning');
      continue;
    }
    const reader = new FileReader();
    reader.onerror = () => {
      toast('Failed to read "' + file.name + '"', 'error');
    };
    if (file.type.startsWith('image/')) {
      reader.onload = e => {
        state.pendingFiles.push({
          name: file.name,
          type: file.type,
          data: e.target.result,
          preview: e.target.result,
        });
        renderAttachments();
      };
      reader.readAsDataURL(file);
    } else {
      reader.onload = e => {
        state.pendingFiles.push({
          name: file.name,
          type: file.type || 'text/plain',
          data: e.target.result,
          preview: null,
        });
        renderAttachments();
      };
      reader.readAsText(file);
    }
  }
  input.value = '';
}

export function renderAttachments() {
  const container = document.getElementById('attachments');
  container.textContent = '';

  if (!state.pendingFiles.length) {
    container.classList.add('hidden');
    emit('input:changed');
    return;
  }

  container.classList.remove('hidden');

  const codeExts = ['js', 'ts', 'py', 'dart', 'go', 'rs', 'java', 'c', 'cpp', 'sh',
    'bash', 'sql', 'json', 'yaml', 'yml', 'toml', 'xml', 'html', 'css'];

  state.pendingFiles.forEach((f, i) => {
    const chip = document.createElement('div');
    chip.className = 'attach-chip';

    if (f.preview) {
      const img = document.createElement('img');
      img.src = f.preview;
      img.className = 'attach-thumb';
      chip.appendChild(img);
    } else {
      const ext = f.name.split('.').pop();
      if (codeExts.includes(ext) && f.data) {
        const preview = document.createElement('div');
        preview.className = 'attach-code-preview';
        preview.textContent = f.data.substring(0, 200) + (f.data.length > 200 ? '...' : '');
        chip.appendChild(preview);
      } else {
        const icon = document.createElement('span');
        icon.className = 'attach-icon';
        icon.textContent = '\uD83D\uDCC4';
        chip.appendChild(icon);
      }
    }

    const nameSpan = document.createElement('span');
    nameSpan.className = 'attach-name';
    nameSpan.textContent = f.name;
    chip.appendChild(nameSpan);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'attach-remove';
    removeBtn.textContent = '\u00d7';
    removeBtn.onclick = () => { state.pendingFiles.splice(i, 1); renderAttachments(); };
    chip.appendChild(removeBtn);

    container.appendChild(chip);
  });

  emit('input:changed');
}

export function setupDragDrop() {
  const main = document.getElementById('main');
  const inputRow = document.getElementById('input-row');

  main.addEventListener('dragover', e => {
    e.preventDefault();
    inputRow.classList.add('drag-over');
  });

  main.addEventListener('dragleave', e => {
    if (!main.contains(e.relatedTarget)) inputRow.classList.remove('drag-over');
  });

  main.addEventListener('drop', e => {
    e.preventDefault();
    inputRow.classList.remove('drag-over');
    if (e.dataTransfer && e.dataTransfer.files.length) {
      handleFiles({ files: e.dataTransfer.files, value: '' });
    }
  });
}

export async function send() {
  // ── Setup ──────────────────────────────────────────────────────
  const inputEl = document.getElementById('input');
  if (!inputEl) return;

  const text = inputEl.value.trim();
  if ((!text && !state.pendingFiles.length) || state.isStreaming || state.sendLock) return;

  state.sendLock = true;
  if (!state.selectedModel || !state.selectedModelReady) {
    state.sendLock = false;
    const { toast } = await import('./toasts.js');
    toast('Select and load a model first', 'warning');
    return;
  }

  // ── Create conversation (if needed) ───────────────────────────
  if (!state.currentConvId) {
    try {
      const convBody = { model: state.selectedModel || '' };
      if (state.customSystemPrompt) {
        convBody.system_prompt = state.customSystemPrompt;
      } else if (state.selectedPersonality.id) {
        convBody.personality_id = state.selectedPersonality.id;
      }
      const convRes = await authFetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(convBody),
      });
      if (!convRes.ok) { state.sendLock = false; return; }
      state.currentConvId = (await convRes.json()).id;
    } catch {
      state.sendLock = false;
      return;
    }
  }

  // ── Render user message ────────────────────────────────────────
  const files = [...state.pendingFiles];
  state.pendingFiles = [];
  renderAttachments();

  inputEl.value = '';
  inputEl.style.height = 'auto';
  localStorage.removeItem('ailab-draft-' + (state.currentConvId || 'new'));
  emit('view:chat');

  state.isStreaming = true;
  state.generationEpoch++;
  state.abortController = new AbortController();
  emit('input:changed');

  let contentEl, bodyEl, assistantDiv, spinner;
  try {
    appendMessage('user', text || (files.length ? 'What is this?' : ''), null, files);
    scrollToBottom();

    const result = appendMessage('assistant', '');
    assistantDiv = result.div;
    contentEl = result.contentEl;
    bodyEl = result.bodyEl;
    assistantDiv.classList.add('streaming');

    spinner = document.createElement('div');
    spinner.className = 'typing-indicator';
    const modelInfo = MODELS.find(m => m.id === state.selectedModel);
    const spinnerLabel = modelInfo
      ? modelInfo.icon + ' ' + modelInfo.name + ' is thinking\u2026'
      : 'Thinking\u2026';
    spinner.innerHTML = '<span class="typing-dot"></span>'
      + '<span class="typing-dot"></span>'
      + '<span class="typing-dot"></span>'
      + '<span class="typing-text">' + esc(spinnerLabel) + '</span>';
    bodyEl.insertBefore(spinner, contentEl);
    scrollToBottom();
  } catch {
    state.isStreaming = false;
    state.sendLock = false;
    state.abortController = null;
    emit('input:changed');
    return;
  }

  // ── Start streaming ────────────────────────────────────────────
  let fullContent = '';
  let thinkingCard = null, thinkingText = '', thinkingStart = 0;
  let gotFirst = false, hasError = false, isDone = false;

  const collectedImages = [];
  const collectedBase64 = [];
  const seenImageBases = new Set();

  const streamConvId = state.currentConvId;
  const streamModel = state.selectedModel;

  let tokenCount = 0, streamStartTime = 0;
  let statsEl = null, statsInterval = null, toolTimerInterval = null;
  const activeToolCards = new Map();
  let toolStartTime = 0;

  // Progressive markdown: throttle re-renders to avoid jank
  const MD_RENDER_INTERVAL = 120; // ms between renders
  let mdRenderPending = false, lastMdRender = 0;

  // Incremental render: only re-parse the tail after safe cutpoints
  let lastSafeOffset = 0, committedHtml = '';

  // ── Handle SSE events ──────────────────────────────────────────
  try {
    const payload = {
      content: text || 'Describe the attached file(s).',
      tools_enabled: state.toolsEnabled,
    };
    if (files.length) {
      payload.files = files.map(f => ({ name: f.name, type: f.type, data: f.data }));
    }

    const res = await authFetch('/api/conversations/' + streamConvId + '/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: state.abortController.signal,
    });

    if (!res.ok) {
      spinner.remove();
      const errMsg = res.status === 429
        ? 'Too many requests \u2014 please wait a moment'
        : res.status >= 500
          ? 'Backend error \u2014 try again in a few seconds'
          : 'Request failed (' + res.status + ')';

      const retryCard = document.createElement('div');
      retryCard.className = 'retry-card';
      retryCard.innerHTML = '<span class="retry-msg">' + errMsg + '</span>'
        + '<button class="retry-btn">Retry</button>';
      retryCard.querySelector('.retry-btn').onclick = () => {
        retryCard.remove();
        assistantDiv.remove();
        // Restore text + files so send() can pick them up
        const inputEl = document.getElementById('input');
        if (inputEl && text) { inputEl.value = text; }
        state.pendingFiles = files.slice();
        emit('input:changed');
        send();
      };
      contentEl.appendChild(retryCard);

      assistantDiv.classList.remove('streaming');
      state.isStreaming = false;
      state.sendLock = false;
      state.abortController = null;
      emit('input:changed');
      return;
    }

    if (!res.body) {
      spinner.remove();
      contentEl.style.color = 'var(--text-muted)';
      contentEl.textContent = 'Empty response from server.';
      assistantDiv.classList.remove('streaming');
      state.isStreaming = false;
      state.sendLock = false;
      state.abortController = null;
      emit('input:changed');
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      // Abort if user switched conversation or model mid-stream
      if (state.currentConvId !== streamConvId || state.selectedModel !== streamModel) {
        try { reader.cancel(); } catch {}
        break;
      }

      const { done, value } = await reader.read();
      if (done) break;

      buf += dec.decode(value, { stream: true });
      if (buf.length > 65536) buf = buf.substring(buf.length - 65536); // 64KB safety cap

      const lines = buf.split('\n');
      buf = lines.pop() || '';

      let currentEvent = null;

      for (const line of lines) {
        if (isDone) continue;

        if (line.startsWith('event: ')) {
          currentEvent = line.substring(7).trim();
          continue;
        }
        if (line.startsWith(':')) continue;
        if (!line.startsWith('data: ') || !currentEvent) continue;

        let eventData;
        try {
          eventData = JSON.parse(line.substring(6));
        } catch {
          currentEvent = null;
          continue;
        }

        if (!gotFirst) {
          gotFirst = true;
          spinner.remove();
          streamStartTime = Date.now();
        }

        if (currentEvent === 'thinking') {
          if (!thinkingCard) {
            thinkingCard = createThinkingCard();
            thinkingStart = Date.now();
            bodyEl.insertBefore(thinkingCard, contentEl);
          }
          thinkingText += eventData.text || '';
          const thinkingBody = thinkingCard.querySelector('.thinking-body');
          if (thinkingBody) thinkingBody.textContent = thinkingText;

        } else if (currentEvent === 'token') {
          fullContent += eventData.text || '';
          tokenCount += (eventData.text || '').split(/\s+/).length;

          // Incremental markdown rendering: only re-parse the tail
          const now = Date.now();
          if (now - lastMdRender > MD_RENDER_INTERVAL) {
            const cutIdx = fullContent.lastIndexOf('\n\n', fullContent.length - 4);
            if (cutIdx > lastSafeOffset) {
              committedHtml = renderMd(fullContent.substring(0, cutIdx + 2));
              lastSafeOffset = cutIdx + 2;
            }
            const tail = fullContent.substring(lastSafeOffset);
            contentEl.innerHTML = committedHtml + (tail ? renderMd(tail) : '');
            lastMdRender = now;
            mdRenderPending = false;
          } else if (!mdRenderPending) {
            mdRenderPending = true;
            setTimeout(() => {
              if (!isDone) {
                const cutIdx = fullContent.lastIndexOf('\n\n', fullContent.length - 4);
                if (cutIdx > lastSafeOffset) {
                  committedHtml = renderMd(fullContent.substring(0, cutIdx + 2));
                  lastSafeOffset = cutIdx + 2;
                }
                contentEl.innerHTML = committedHtml + renderMd(fullContent.substring(lastSafeOffset));
                lastMdRender = Date.now();
              }
              mdRenderPending = false;
            }, MD_RENDER_INTERVAL);
          }

          if (!statsEl && state.settings.showStreamStats) {
            statsEl = document.createElement('div');
            statsEl.className = 'stream-stats';
            bodyEl.insertBefore(statsEl, contentEl);
            statsInterval = setInterval(() => {
              if (!statsEl) return;
              const elapsed = ((Date.now() - streamStartTime) / 1000).toFixed(0);
              const tps = streamStartTime
                ? (tokenCount / ((Date.now() - streamStartTime) / 1000)).toFixed(0)
                : '0';
              statsEl.textContent = tps + ' tok/s \u2022 '
                + fullContent.split('\n').length + ' lines \u2022 '
                + elapsed + 's';
            }, 500);
          }

          scrollToBottom();

        } else if (currentEvent === 'tool_start') {
          toolStartTime = Date.now();
          const suppressedTools = new Set(['browser', 'image', 'web', 'memory', 'vector']);

          if (!suppressedTools.has(eventData.name) && state.settings.showToolCards) {
            const toolCard = makeToolCard({
              id: eventData.id || '',
              name: eventData.name || 'tool',
              arguments: eventData.arguments || '{}',
            }, 'running');
            bodyEl.insertBefore(toolCard, contentEl);
            activeToolCards.set(eventData.id || eventData.name, toolCard);
          }

          if (toolTimerInterval) clearInterval(toolTimerInterval);
          toolTimerInterval = setInterval(() => {
            for (const [, card] of activeToolCards) {
              const statusEl = card.querySelector('.tool-status');
              if (statusEl && statusEl.classList.contains('running')) {
                statusEl.textContent = 'running '
                  + Math.round((Date.now() - toolStartTime) / 1000) + 's\u2026';
              }
            }
          }, 1000);

        } else if (currentEvent === 'tool_result') {
          const cardKey = eventData.id || eventData.name;
          const toolCard = activeToolCards.get(cardKey);

          if (toolCard) {
            const statusEl = toolCard.querySelector('.tool-status');
            if (statusEl) {
              const elapsed = Math.round((Date.now() - toolStartTime) / 1000);
              if (eventData.isError) {
                statusEl.textContent = 'error (' + elapsed + 's)';
                statusEl.className = 'tool-status error';
              } else {
                statusEl.textContent = 'done (' + elapsed + 's)';
                statusEl.className = 'tool-status done';
              }
            }
            if (eventData.text) {
              const toolBody = toolCard.querySelector('.tool-body');
              if (toolBody) {
                const resultPreview = document.createElement('div');
                resultPreview.style.cssText = 'margin-top:4px;font-size:11px;color:var(--text-muted);'
                  + 'max-height:60px;overflow:hidden;';
                resultPreview.textContent = (eventData.text || '').substring(0, 200);
                toolBody.appendChild(resultPreview);
              }
            }
            activeToolCards.delete(cardKey);
          }

          if (toolTimerInterval && activeToolCards.size === 0) {
            clearInterval(toolTimerInterval);
            toolTimerInterval = null;
          }

          if (eventData.images && eventData.images.length) {
            for (const img of eventData.images) collectedBase64.push(img);
          }
          if (eventData.imageUrls && eventData.imageUrls.length) {
            for (const url of eventData.imageUrls) {
              const normalized = normalizeImageUrl(url);
              if (!seenImageBases.has(normalized)) {
                seenImageBases.add(normalized);
                collectedImages.push(url);
              }
            }
          }

        } else if (currentEvent === 'error') {
          hasError = true;
          const errPara = document.createElement('p');
          errPara.style.color = 'var(--error)';
          errPara.textContent = eventData.message;
          contentEl.appendChild(errPara);

        } else if (currentEvent === 'status') {
          // Update spinner text if still visible, otherwise show subtle status line
          const spinnerText = spinner && spinner.parentNode
            ? spinner.querySelector('.typing-text')
            : null;
          if (spinnerText) spinnerText.textContent = eventData.text || 'Loading\u2026';
        }

        currentEvent = null;
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      const errMsg = e.message.includes('network') || e.message.includes('Failed to fetch')
        ? 'Connection lost \u2014 check your internet'
        : 'Error: ' + e.message;

      const retryCard = document.createElement('div');
      retryCard.className = 'retry-card';
      retryCard.innerHTML = '<span class="retry-msg">' + errMsg + '</span>'
        + '<button class="retry-btn">Retry</button>';
      retryCard.querySelector('.retry-btn').onclick = () => {
        retryCard.remove();
        assistantDiv.remove();
        // Restore text + files so send() can pick them up
        const inputEl = document.getElementById('input');
        if (inputEl && text) { inputEl.value = text; }
        state.pendingFiles = files.slice();
        emit('input:changed');
        send();
      };
      contentEl.appendChild(retryCard);
    }
  }

  // ── Cleanup ────────────────────────────────────────────────────
  if (statsInterval) { clearInterval(statsInterval); statsInterval = null; }
  if (toolTimerInterval) { clearInterval(toolTimerInterval); toolTimerInterval = null; }
  isDone = true;

  if (thinkingCard) {
    const elapsed = Math.round((Date.now() - thinkingStart) / 1000);
    const thinkingLabel = thinkingCard.querySelector('.thinking-label');
    if (thinkingLabel) thinkingLabel.textContent = 'Thought for ' + elapsed + 's';
    thinkingCard.classList.add('done');
  }

  if (!gotFirst) spinner.remove();
  if (statsEl) { statsEl.remove(); statsEl = null; }

  // Render collected images from tool results
  const totalImages = collectedBase64.length + collectedImages.length;
  if (totalImages > 0) {
    const imgGrid = document.createElement('div');
    imgGrid.className = 'tool-images' + (totalImages === 1 ? ' single-image' : '');
    const allSrcs = [];

    for (const img of collectedBase64) {
      const src = 'data:' + img.mimeType + ';base64,' + img.data;
      allSrcs.push(src);
      const imgEl = document.createElement('img');
      imgEl.src = src;
      imgEl.loading = 'lazy';
      imgEl.alt = 'Generated';
      imgEl.onerror = () => { imgEl.style.display = 'none'; };
      imgGrid.appendChild(imgEl);
    }

    for (const url of collectedImages) {
      allSrcs.push(url);
      const imgEl = document.createElement('img');
      imgEl.src = url;
      imgEl.loading = 'lazy';
      imgEl.alt = 'Result';
      imgEl.onerror = () => { imgEl.style.display = 'none'; };
      imgGrid.appendChild(imgEl);
    }

    imgGrid.querySelectorAll('img').forEach((img, i) => {
      img.onclick = () => openLightbox(img.src, allSrcs, i);
    });

    bodyEl.insertBefore(imgGrid, contentEl);
  }

  // Final content render
  if (fullContent && !hasError) {
    const hasToolImages = collectedImages.length > 0 || collectedBase64.length > 0;
    contentEl.innerHTML = renderMd(cleanResponse(fullContent, hasToolImages));
  } else if (!fullContent && !hasError && !contentEl.textContent.trim()) {
    contentEl.style.color = 'var(--text-muted)';
    contentEl.style.fontStyle = 'italic';
    contentEl.textContent = 'Model returned no response.';
  }

  assistantDiv.classList.remove('streaming');
  document.querySelectorAll('.loading-spinner').forEach(el => el.remove());
  postProcess(bodyEl);
  scrollToBottom({ immediate: true });

  state.isStreaming = false;
  state.sendLock = false;
  state.abortController = null;
  emit('input:changed');

  await loadConversations();
}

export function stopGeneration() {
  if (!state.abortController) return;

  const epoch = state.generationEpoch;
  try { state.abortController.abort(); } catch {}

  setTimeout(() => {
    if (state.isStreaming && state.generationEpoch === epoch) {
      state.isStreaming = false;
      state.sendLock = false;
      state.abortController = null;
      emit('input:changed');
      document.querySelectorAll('.streaming').forEach(el => el.classList.remove('streaming'));
      document.querySelectorAll('.loading-spinner').forEach(el => el.remove());
    }
  }, 500);
}
