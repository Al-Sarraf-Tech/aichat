'use strict';
import { authFetch } from './utils.js';
import { toast } from './toasts.js';

// ── Conversation Export ──────────────────────────────────────────
export async function exportConversation(convId, format = 'markdown') {
  if (!convId) { toast('No conversation to export', 'warning'); return; }

  try {
    const res = await authFetch(`/api/conversations/${convId}`);
    const data = await res.json();
    const messages = data.messages || [];
    const title = data.title || 'Chat';
    const model = data.model || 'unknown';
    const date = new Date(data.created_at || Date.now()).toISOString().split('T')[0];

    let content, ext, mime;

    if (format === 'json') {
      content = JSON.stringify(data, null, 2);
      ext = 'json';
      mime = 'application/json';
    } else if (format === 'text') {
      const lines = [`${title}\nModel: ${model}\nDate: ${date}\n${'='.repeat(50)}\n`];
      for (const m of messages) {
        if (m.role === 'system') continue;
        lines.push(`[${m.role.toUpperCase()}]`);
        lines.push(m.content || '');
        lines.push('');
      }
      content = lines.join('\n');
      ext = 'txt';
      mime = 'text/plain';
    } else {
      // Markdown (default)
      const lines = [`# ${title}\n\n> Model: \`${model}\` | Date: ${date}\n\n---\n`];
      for (const m of messages) {
        if (m.role === 'system') continue;
        if (m.role === 'user') {
          lines.push(`## You\n\n${m.content || ''}\n`);
        } else if (m.role === 'assistant') {
          lines.push(`## Assistant\n\n${m.content || ''}\n`);
        }
      }
      content = lines.join('\n');
      ext = 'md';
      mime = 'text/markdown';
    }

    const slug = title.replace(/[^a-zA-Z0-9]+/g, '-').substring(0, 40);
    const filename = `${slug}-${date}.${ext}`;
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast(`Exported as ${ext.toUpperCase()}`, 'success');
  } catch (e) {
    toast('Export failed: ' + e.message, 'error');
  }
}
