'use strict';
import { state, emit, on } from './state.js';
import { authFetch, esc, renderMd, scrollToBottom, cleanResponse, normalizeImageUrl } from './utils.js';
import { makeToolCard, createThinkingCard } from './tools.js';
import { openLightbox, buildImageCarousels } from './lightbox.js';
import { loadConversations } from './conversations.js';
import { toast } from './toasts.js';
import { MODELS } from './models.js';
import { tightenBubble } from './layout.js';

export function postProcess(el) {
  if (!el) return;
  try { if (typeof hljs !== 'undefined') el.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b)); } catch {}
  el.querySelectorAll('pre').forEach(pre => {
    if (pre.closest('.code-block')) return;
    const w = document.createElement('div'); w.className = 'code-block';
    pre.parentNode.insertBefore(w, pre); w.appendChild(pre);
    const h = document.createElement('div'); h.className = 'code-header';
    const code = pre.querySelector('code');
    const lm = code && code.className ? code.className.match(/language-(\S+)/) : null;
    const ls = document.createElement('span'); ls.className = 'code-lang'; ls.textContent = lm ? lm[1] : '';
    const cb = document.createElement('button'); cb.className = 'copy-btn'; cb.textContent = 'Copy';
    cb.onclick = () => navigator.clipboard.writeText(code ? code.textContent : '').then(() => { cb.textContent='Copied!'; cb.classList.add('copied'); setTimeout(()=>{cb.textContent='Copy';cb.classList.remove('copied');},2000); });
    h.appendChild(ls); h.appendChild(cb); w.insertBefore(h, pre);
  });
  buildImageCarousels(el);
}

export function renderMessages(msgs) {
  const c = document.getElementById('messages'); c.textContent = '';
  for (const m of msgs) {
    if (m.role === 'system' || m.role === 'tool') continue;
    if (m.role === 'assistant' && !(m.content||'').trim() && m.tool_calls && m.tool_calls.length > 0) continue;
    appendMessage(m.role, m.content, m.tool_calls, null, m.id, m.created_at);
  }
  scrollToBottom();
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
    const bubble = document.createElement('div'); bubble.className = 'msg-bubble';
    if (files && files.length > 0) {
      const a = document.createElement('div'); a.className = 'msg-attachments';
      for (const f of files) {
        if (f.preview) { const img = document.createElement('img'); img.src = f.preview; img.alt = f.name; a.appendChild(img); }
        else { const c = document.createElement('span'); c.className = 'msg-attach-file'; c.textContent = '\uD83D\uDCC4 ' + f.name; a.appendChild(c); }
      }
      bubble.appendChild(a);
    }
    const el = document.createElement('div'); el.className = 'msg-content';
    el.innerHTML = renderMd(content);
    bubble.appendChild(el);
    bubble.appendChild(createActions(role, content, div));
    bubble.appendChild(timeEl);
    div.appendChild(bubble); container.appendChild(div);
    // Tighten user bubble to text width (pretext)
    if (content && container.offsetWidth > 0) {
      const tight = tightenBubble(content, Math.min(container.offsetWidth * 0.75, 600));
      if (tight > 0) bubble.style.maxWidth = tight + 'px';
    }
    return { div, contentEl: el, bodyEl: bubble };
  }

  if (role === 'assistant') {
    const av = document.createElement('div'); av.className = 'msg-avatar'; av.textContent = '\uD83E\uDD16';
    const body = document.createElement('div'); body.className = 'msg-body';
    const el = document.createElement('div'); el.className = 'msg-content';
    body.appendChild(el);
    if (content) { el.innerHTML = renderMd(content); postProcess(el); }
    body.appendChild(createActions(role, content, div));
    body.appendChild(timeEl);
    div.appendChild(av); div.appendChild(body); container.appendChild(div);
    // Tighten assistant bubble to text width (pretext) — skip long/markdown-heavy content
    if (content && content.length < 500 && !content.includes('```') && container.offsetWidth > 0) {
      const tight = tightenBubble(content, Math.min(container.offsetWidth * 0.85, 800));
      if (tight > 0) body.style.maxWidth = tight + 'px';
    }
    return { div, contentEl: el, bodyEl: body };
  }
  return { div, contentEl: null, bodyEl: null };
}

function formatMsgTime(iso) {
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  const today = now.toDateString() === d.toDateString();
  if (today) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function createActions(role, content, msgDiv) {
  const bar = document.createElement('div'); bar.className = 'msg-actions';
  // Copy
  const copyBtn = document.createElement('button'); copyBtn.className = 'msg-action-btn'; copyBtn.title = 'Copy';
  copyBtn.textContent = '\uD83D\uDCCB';
  copyBtn.onclick = (e) => { e.stopPropagation(); const t = msgDiv.querySelector('.msg-content'); navigator.clipboard.writeText(t ? t.textContent : content || '').then(() => toast('Copied', 'success')); };
  bar.appendChild(copyBtn);
  // Regenerate + Read aloud (assistant)
  if (role === 'assistant') {
    const regenBtn = document.createElement('button'); regenBtn.className = 'msg-action-btn'; regenBtn.title = 'Regenerate'; regenBtn.textContent = '\uD83D\uDD04';
    regenBtn.onclick = (e) => { e.stopPropagation(); regenLast(); };
    bar.appendChild(regenBtn);
    // TTS read-aloud
    if ('speechSynthesis' in window) {
      const ttsBtn = document.createElement('button'); ttsBtn.className = 'msg-action-btn'; ttsBtn.title = 'Read aloud'; ttsBtn.textContent = '\uD83D\uDD0A';
      ttsBtn.onclick = (e) => {
        e.stopPropagation();
        if (speechSynthesis.speaking) { speechSynthesis.cancel(); ttsBtn.textContent = '\uD83D\uDD0A'; return; }
        const t = msgDiv.querySelector('.msg-content');
        const text = t ? t.textContent : content || '';
        if (!text.trim()) return;
        const utt = new SpeechSynthesisUtterance(text.substring(0, 5000));
        utt.rate = 1.05; utt.pitch = 1.0;
        utt.onend = () => { ttsBtn.textContent = '\uD83D\uDD0A'; };
        utt.onerror = () => { ttsBtn.textContent = '\uD83D\uDD0A'; };
        ttsBtn.textContent = '\u23F9';
        speechSynthesis.speak(utt);
      };
      bar.appendChild(ttsBtn);
    }
  }
  // Edit (user) — forks conversation from this point
  if (role === 'user') {
    const editBtn = document.createElement('button'); editBtn.className = 'msg-action-btn'; editBtn.title = 'Edit & Fork'; editBtn.textContent = '\u270F\uFE0F';
    editBtn.onclick = (e) => {
      e.stopPropagation();
      const t = msgDiv.querySelector('.msg-content');
      const text = t ? t.textContent : content || '';
      // Remove this message and all messages after it from the DOM
      const allMsgs = Array.from(document.querySelectorAll('#messages .msg'));
      const idx = allMsgs.indexOf(msgDiv);
      if (idx >= 0) {
        for (let i = allMsgs.length - 1; i >= idx; i--) allMsgs[i].remove();
      }
      // Fill the input with the original text for editing
      const input = document.getElementById('input');
      input.value = text; input.focus();
      input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 200) + 'px';
      emit('input:changed');
      toast('Forked \u2014 edit and press Enter to continue from here', 'info');
    };
    bar.appendChild(editBtn);
  }
  return bar;
}

function regenLast() {
  if (state.isStreaming || !state.currentConvId) return;
  const msgs = document.querySelectorAll('.msg.user'); if (!msgs.length) return;
  const last = msgs[msgs.length - 1];
  const text = last.querySelector('.msg-content') ? last.querySelector('.msg-content').textContent : '';
  if (!text.trim()) return;
  const all = document.querySelectorAll('.msg');
  const lastMsg = all[all.length - 1];
  if (lastMsg && lastMsg.classList.contains('assistant')) lastMsg.remove();
  const input = document.getElementById('input'); input.value = text;
  emit('input:changed'); emit('send:trigger');
}

export function handleFiles(input) {
  for (const file of Array.from(input.files)) {
    if (file.size > 10*1024*1024) { toast('"' + file.name + '" too large (max 10MB)', 'warning'); continue; }
    const r = new FileReader();
    if (file.type.startsWith('image/')) { r.onload=e=>{state.pendingFiles.push({name:file.name,type:file.type,data:e.target.result,preview:e.target.result});renderAttachments();}; r.readAsDataURL(file); }
    else { r.onload=e=>{state.pendingFiles.push({name:file.name,type:file.type||'text/plain',data:e.target.result,preview:null});renderAttachments();}; r.readAsText(file); }
  }
  input.value = '';
}

export function renderAttachments() {
  const c = document.getElementById('attachments'); c.textContent = '';
  if (!state.pendingFiles.length) { c.classList.add('hidden'); emit('input:changed'); return; }
  c.classList.remove('hidden');
  state.pendingFiles.forEach((f, i) => {
    const ch = document.createElement('div'); ch.className = 'attach-chip';
    if (f.preview) { const img = document.createElement('img'); img.src = f.preview; img.className = 'attach-thumb'; ch.appendChild(img); }
    else {
      const ext = f.name.split('.').pop();
      const codeExts = ['js','ts','py','dart','go','rs','java','c','cpp','sh','bash','sql','json','yaml','yml','toml','xml','html','css'];
      if (codeExts.includes(ext) && f.data) {
        const pre = document.createElement('div'); pre.className = 'attach-code-preview';
        pre.textContent = f.data.substring(0, 200) + (f.data.length > 200 ? '...' : '');
        ch.appendChild(pre);
      } else { const ic = document.createElement('span'); ic.className = 'attach-icon'; ic.textContent = '\uD83D\uDCC4'; ch.appendChild(ic); }
    }
    const n = document.createElement('span'); n.className = 'attach-name'; n.textContent = f.name; ch.appendChild(n);
    const rm = document.createElement('button'); rm.className = 'attach-remove'; rm.textContent = '\u00d7';
    rm.onclick = () => { state.pendingFiles.splice(i, 1); renderAttachments(); }; ch.appendChild(rm);
    c.appendChild(ch);
  });
  emit('input:changed');
}

export function setupDragDrop() {
  const m = document.getElementById('main'), r = document.getElementById('input-row');
  m.addEventListener('dragover', e => { e.preventDefault(); r.classList.add('drag-over'); });
  m.addEventListener('dragleave', e => { if (!m.contains(e.relatedTarget)) r.classList.remove('drag-over'); });
  m.addEventListener('drop', e => { e.preventDefault(); r.classList.remove('drag-over'); if (e.dataTransfer && e.dataTransfer.files.length) handleFiles({ files: e.dataTransfer.files, value: '' }); });
}

export async function send() {
  const input = document.getElementById('input'); if (!input) return;
  const text = input.value.trim();
  if ((!text && !state.pendingFiles.length) || state.isStreaming || state.sendLock) return;
  state.sendLock = true;
  if (!state.selectedModel || !state.selectedModelReady) { state.sendLock = false; return; }
  if (!state.currentConvId) {
    try {
      const convBody = { model: state.selectedModel || '' };
      if (state.customSystemPrompt) convBody.system_prompt = state.customSystemPrompt;
      else if (state.selectedPersonality.id) convBody.personality_id = state.selectedPersonality.id;
      const r = await authFetch('/api/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(convBody) });
      if (!r.ok) { state.sendLock = false; return; }
      state.currentConvId = (await r.json()).id;
    } catch (e) { state.sendLock = false; return; }
  }
  const files = [...state.pendingFiles]; state.pendingFiles = []; renderAttachments();
  input.value = ''; input.style.height = 'auto';
  localStorage.removeItem('ailab-draft-' + (state.currentConvId || 'new'));
  emit('view:chat');
  state.isStreaming = true; state.generationEpoch++; state.abortController = new AbortController();
  emit('input:changed');

  let contentEl, bodyEl, aDiv, spinner;
  try {
    appendMessage('user', text || (files.length ? 'What is this?' : ''), null, files);
    scrollToBottom();
    const result = appendMessage('assistant', '');
    aDiv = result.div; contentEl = result.contentEl; bodyEl = result.bodyEl;
    aDiv.classList.add('streaming');
    spinner = document.createElement('div'); spinner.className = 'typing-indicator';
    const _tm = MODELS.find(m => m.id === state.selectedModel);
    const _tLabel = _tm ? _tm.icon + ' ' + _tm.name + ' is thinking\u2026' : 'Thinking\u2026';
    spinner.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-text">' + esc(_tLabel) + '</span>';
    bodyEl.insertBefore(spinner, contentEl); scrollToBottom();
  } catch (e) { state.isStreaming = false; state.sendLock = false; state.abortController = null; emit('input:changed'); return; }

  let fullContent = '', thinkingCard = null, thinkingText = '', thinkingStart = 0, gotFirst = false;
  const collectedImages = [], collectedBase64 = [], seenImageBases = new Set();
  let hasError = false, isDone = false;
  const streamConvId = state.currentConvId, streamModel = state.selectedModel;
  let tokenCount = 0, streamStartTime = 0, statsEl = null, statsInterval = null, toolTimerInterval = null;
  const activeToolCards = new Map(); let toolStartTime = 0;
  // Progressive markdown: throttle re-renders to avoid jank
  let mdRenderPending = false, lastMdRender = 0;
  const MD_RENDER_INTERVAL = 120; // ms between renders

  try {
    const payload = { content: text || 'Describe the attached file(s).', tools_enabled: state.toolsEnabled };
    if (files.length) payload.files = files.map(f => ({ name: f.name, type: f.type, data: f.data }));
    const res = await authFetch('/api/conversations/' + streamConvId + '/messages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: state.abortController.signal,
    });
    if (!res.ok) {
      spinner.remove();
      const errMsg = res.status === 429 ? 'Too many requests \u2014 please wait a moment'
        : res.status >= 500 ? 'Backend error \u2014 try again in a few seconds'
        : 'Request failed (' + res.status + ')';
      const retryCard = document.createElement('div'); retryCard.className = 'retry-card';
      retryCard.innerHTML = '<span class="retry-msg">' + errMsg + '</span><button class="retry-btn">Retry</button>';
      retryCard.querySelector('.retry-btn').onclick = () => { retryCard.remove(); aDiv.remove(); send(text, files); };
      contentEl.appendChild(retryCard);
      aDiv.classList.remove('streaming'); state.isStreaming=false; state.sendLock=false; state.abortController=null; emit('input:changed'); return;
    }
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    while (true) {
      if (state.currentConvId !== streamConvId || state.selectedModel !== streamModel) { try { reader.cancel(); } catch {} break; }
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      if (buf.length > 65536) buf = buf.substring(buf.length - 65536); // 64KB safety cap
      const lines = buf.split('\n'); buf = lines.pop() || '';
      let ev = null;
      for (const line of lines) {
        if (isDone) continue;
        if (line.startsWith('event: ')) { ev = line.substring(7).trim(); continue; }
        if (line.startsWith(':')) continue;
        if (!line.startsWith('data: ') || !ev) continue;
        let d; try { d = JSON.parse(line.substring(6)); } catch { ev=null; continue; }
        if (!gotFirst) { gotFirst = true; spinner.remove(); streamStartTime = Date.now(); }
        if (ev === 'thinking') {
          if (!thinkingCard) { thinkingCard = createThinkingCard(); thinkingStart = Date.now(); bodyEl.insertBefore(thinkingCard, contentEl); }
          thinkingText += d.text || ''; const tb = thinkingCard.querySelector('.thinking-body'); if (tb) tb.textContent = thinkingText;
        } else if (ev === 'token') {
          fullContent += d.text || ''; tokenCount += (d.text||'').split(/\s+/).length;
          // Progressive markdown rendering (throttled)
          const now = Date.now();
          if (now - lastMdRender > MD_RENDER_INTERVAL) {
            contentEl.innerHTML = renderMd(fullContent);
            lastMdRender = now; mdRenderPending = false;
          } else if (!mdRenderPending) {
            mdRenderPending = true;
            setTimeout(() => { if (!isDone) { contentEl.innerHTML = renderMd(fullContent); lastMdRender = Date.now(); } mdRenderPending = false; }, MD_RENDER_INTERVAL);
          }
          if (!statsEl && state.settings.showStreamStats) {
            statsEl = document.createElement('div'); statsEl.className = 'stream-stats'; bodyEl.insertBefore(statsEl, contentEl);
            statsInterval = setInterval(() => { if (!statsEl) return; const el = ((Date.now()-streamStartTime)/1000).toFixed(0);
              const tps = streamStartTime ? (tokenCount/((Date.now()-streamStartTime)/1000)).toFixed(0) : '0';
              statsEl.textContent = tps + ' tok/s \u2022 ' + fullContent.split('\n').length + ' lines \u2022 ' + el + 's';
            }, 500);
          }
          scrollToBottom();
        } else if (ev === 'tool_start') {
          toolStartTime = Date.now();
          const suppressed = new Set(['browser','image','web','memory','vector']);
          if (!suppressed.has(d.name) && state.settings.showToolCards) {
            const tc = makeToolCard({ id: d.id||'', name: d.name||'tool', arguments: d.arguments||'{}' }, 'running');
            bodyEl.insertBefore(tc, contentEl); activeToolCards.set(d.id||d.name, tc);
          }
          if (toolTimerInterval) clearInterval(toolTimerInterval);
          toolTimerInterval = setInterval(() => { for (const [,card] of activeToolCards) { const s = card.querySelector('.tool-status');
            if (s && s.classList.contains('running')) s.textContent = 'running ' + Math.round((Date.now()-toolStartTime)/1000) + 's\u2026'; } }, 1000);
        } else if (ev === 'tool_result') {
          const ck = d.id || d.name; const card = activeToolCards.get(ck);
          if (card) { const s = card.querySelector('.tool-status'); if (s) { const el = Math.round((Date.now()-toolStartTime)/1000);
            if (d.isError) { s.textContent = 'error ('+el+'s)'; s.className = 'tool-status error'; } else { s.textContent = 'done ('+el+'s)'; s.className = 'tool-status done'; } }
            if (d.text) { const b = card.querySelector('.tool-body'); if (b) { const p = document.createElement('div'); p.style.cssText='margin-top:4px;font-size:11px;color:var(--text-muted);max-height:60px;overflow:hidden;'; p.textContent=(d.text||'').substring(0,200); b.appendChild(p); } }
            activeToolCards.delete(ck); }
          if (toolTimerInterval && activeToolCards.size === 0) { clearInterval(toolTimerInterval); toolTimerInterval = null; }
          if (d.images && d.images.length) for (const img of d.images) collectedBase64.push(img);
          if (d.imageUrls && d.imageUrls.length) for (const url of d.imageUrls) { const norm = normalizeImageUrl(url); if (!seenImageBases.has(norm)) { seenImageBases.add(norm); collectedImages.push(url); } }
        } else if (ev === 'error') {
          hasError = true; const ep = document.createElement('p'); ep.style.color = 'var(--error)'; ep.textContent = d.message; contentEl.appendChild(ep);
        } else if (ev === 'status') {
          // Update spinner text if still visible, otherwise show subtle status line
          const _st = spinner && spinner.parentNode ? spinner.querySelector('.typing-text') : null;
          if (_st) { _st.textContent = d.text || 'Loading\u2026'; }
        }
        ev = null;
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      const errMsg = e.message.includes('network') || e.message.includes('Failed to fetch')
        ? 'Connection lost \u2014 check your internet' : 'Error: ' + e.message;
      const retryCard = document.createElement('div'); retryCard.className = 'retry-card';
      retryCard.innerHTML = '<span class="retry-msg">' + errMsg + '</span><button class="retry-btn">Retry</button>';
      retryCard.querySelector('.retry-btn').onclick = () => { retryCard.remove(); aDiv.remove(); send(text, files); };
      contentEl.appendChild(retryCard);
    }
  }

  if (statsInterval) { clearInterval(statsInterval); statsInterval = null; }
  if (toolTimerInterval) { clearInterval(toolTimerInterval); toolTimerInterval = null; }
  isDone = true;
  if (thinkingCard) { const s = Math.round((Date.now()-thinkingStart)/1000); const l = thinkingCard.querySelector('.thinking-label'); if (l) l.textContent = 'Thought for '+s+'s'; thinkingCard.classList.add('done'); }
  if (!gotFirst) spinner.remove();
  if (statsEl) { statsEl.remove(); statsEl = null; }

  const totalImages = collectedBase64.length + collectedImages.length;
  if (totalImages > 0) {
    const imgGrid = document.createElement('div'); imgGrid.className = 'tool-images' + (totalImages === 1 ? ' single-image' : '');
    const allSrcs = [];
    for (const img of collectedBase64) { const src = 'data:'+img.mimeType+';base64,'+img.data; allSrcs.push(src); const el = document.createElement('img'); el.src=src; el.loading='lazy'; el.alt='Generated'; el.onerror=()=>{el.style.display='none';}; imgGrid.appendChild(el); }
    for (const url of collectedImages) { allSrcs.push(url); const el = document.createElement('img'); el.src=url; el.loading='lazy'; el.alt='Result'; el.onerror=()=>{el.style.display='none';}; imgGrid.appendChild(el); }
    imgGrid.querySelectorAll('img').forEach((img,i) => { img.onclick = () => openLightbox(img.src, allSrcs, i); });
    bodyEl.insertBefore(imgGrid, contentEl);
  }

  if (fullContent && !hasError) { const hasToolImages = collectedImages.length > 0 || collectedBase64.length > 0; contentEl.innerHTML = renderMd(cleanResponse(fullContent, hasToolImages)); }
  else if (!fullContent && !hasError && !contentEl.textContent.trim()) { contentEl.style.color='var(--text-muted)'; contentEl.style.fontStyle='italic'; contentEl.textContent='Model returned no response.'; }
  aDiv.classList.remove('streaming');
  document.querySelectorAll('.loading-spinner').forEach(el => el.remove());
  postProcess(bodyEl); scrollToBottom();
  state.isStreaming=false; state.sendLock=false; state.abortController=null; emit('input:changed');
  await loadConversations();
}

export function stopGeneration() {
  if (state.abortController) {
    const epoch = state.generationEpoch; try { state.abortController.abort(); } catch {}
    setTimeout(() => { if (state.isStreaming && state.generationEpoch === epoch) {
      state.isStreaming=false; state.sendLock=false; state.abortController=null; emit('input:changed');
      document.querySelectorAll('.streaming').forEach(el => el.classList.remove('streaming'));
      document.querySelectorAll('.loading-spinner').forEach(el => el.remove());
    } }, 500);
  }
}
