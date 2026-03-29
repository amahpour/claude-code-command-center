/**
 * Terminal / Session Viewer
 *
 * When a session was launched via the Command Center (tmux), shows an interactive terminal.
 * For all other sessions, shows a live transcript that auto-updates.
 */

const SessionViewer = {
  ws: null,
  term: null,
  fitAddon: null,
  currentSessionId: null,
  _pollInterval: null,
  _lastTranscriptCount: 0,
  _mode: null, // 'terminal' or 'transcript'

  open(sessionId, title) {
    this.currentSessionId = sessionId;
    const overlay = document.getElementById('terminal-overlay');
    const titleEl = document.getElementById('terminal-title');

    titleEl.textContent = title || sessionId;
    overlay.style.display = 'flex';

    this._cleanup();

    // Try terminal first, fall back to transcript
    this._tryTerminal(sessionId);
  },

  _tryTerminal(sessionId) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/terminal/${sessionId}`;

    this.ws = new WebSocket(url);
    let gotError = false;

    this.ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'error') {
            gotError = true;
            this.ws.close();
            // Fall back to transcript view
            this._showTranscript(sessionId);
            return;
          }
          if (msg.type === 'pong') return;
        } catch {
          // Not JSON — real terminal data
          if (!this.term) this._initXterm();
          this.term.write(event.data);
        }
      } else if (event.data instanceof Blob) {
        if (!this.term) this._initXterm();
        event.data.arrayBuffer().then(buf => {
          this.term.write(new Uint8Array(buf));
        });
      }
    };

    this.ws.onopen = () => {
      // Wait for first message to determine mode
    };

    this.ws.onclose = () => {
      if (!gotError && this.term) {
        this.term.writeln('\x1b[2m--- Disconnected ---\x1b[0m');
      }
    };

    this.ws.onerror = () => {
      this._showTranscript(sessionId);
    };
  },

  _initXterm() {
    this._mode = 'terminal';
    const container = document.getElementById('terminal-container');
    container.innerHTML = '';

    this.term = new window.Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
      theme: {
        background: '#0f0f1a',
        foreground: '#e0e0e0',
        cursor: '#7c3aed',
        selectionBackground: 'rgba(124, 58, 237, 0.3)',
      },
    });

    this.fitAddon = new window.FitAddon.FitAddon();
    this.term.loadAddon(this.fitAddon);
    this.term.open(container);
    this.fitAddon.fit();

    this._resizeHandler = () => { if (this.fitAddon) this.fitAddon.fit(); };
    window.addEventListener('resize', this._resizeHandler);

    this.term.onData((data) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(data);
      }
    });
  },

  async _showTranscript(sessionId) {
    this._mode = 'transcript';
    const container = document.getElementById('terminal-container');
    container.innerHTML = `
      <div class="transcript-live" id="transcript-live">
        <div class="transcript-live-header">Live Session Transcript</div>
        <div class="transcript-live-messages" id="transcript-live-messages"></div>
      </div>
    `;

    this._lastTranscriptCount = 0;
    await this._fetchTranscript(sessionId);

    // Poll for new transcript entries every 2 seconds
    this._pollInterval = setInterval(() => {
      this._fetchTranscript(sessionId);
    }, 2000);
  },

  async _fetchTranscript(sessionId) {
    try {
      const resp = await fetch(`/api/sessions/${sessionId}/transcript?limit=200`);
      const data = await resp.json();
      const transcripts = data.transcripts || [];

      if (transcripts.length === this._lastTranscriptCount) return;
      this._lastTranscriptCount = transcripts.length;

      const container = document.getElementById('transcript-live-messages');
      if (!container) return;

      container.innerHTML = transcripts.map(t => this._renderMessage(t)).join('');

      // Auto-scroll to bottom
      container.scrollTop = container.scrollHeight;
    } catch (e) {
      console.error('Failed to fetch transcript:', e);
    }
  },

  close() {
    const overlay = document.getElementById('terminal-overlay');
    overlay.style.display = 'none';
    this._cleanup();
    this.currentSessionId = null;
  },

  _cleanup() {
    if (this.ws) { this.ws.close(); this.ws = null; }
    if (this.term) { this.term.dispose(); this.term = null; }
    if (this.fitAddon) { this.fitAddon = null; }
    if (this._resizeHandler) {
      window.removeEventListener('resize', this._resizeHandler);
      this._resizeHandler = null;
    }
    if (this._pollInterval) {
      clearInterval(this._pollInterval);
      this._pollInterval = null;
    }
    this._mode = null;
    this._lastTranscriptCount = 0;
    const container = document.getElementById('terminal-container');
    if (container) container.innerHTML = '';
  },

  _renderMessage(t) {
    const role = t.role || 'unknown';
    const time = t.timestamp ? this._fmtTime(t.timestamp) : '';
    const raw = t.content || '';

    // Split assistant messages that contain both text and tool calls
    if (role === 'assistant' && raw.includes('[Tool:')) {
      const parts = raw.split(/(\[Tool: [^\]]+\] )/);
      const blocks = [];
      let textBuf = '';

      for (const part of parts) {
        if (part.startsWith('[Tool:')) {
          if (textBuf.trim()) {
            blocks.push({ type: 'text', content: textBuf.trim() });
            textBuf = '';
          }
          blocks.push({ type: 'tool_header', content: part });
        } else {
          // Check if this follows a tool header (is the JSON args)
          if (blocks.length > 0 && blocks[blocks.length - 1].type === 'tool_header') {
            const header = blocks.pop().content;
            const toolName = header.match(/\[Tool: ([^\]]+)\]/)?.[1] || 'unknown';
            blocks.push({ type: 'tool_call', name: toolName, args: part.trim() });
          } else {
            textBuf += part;
          }
        }
      }
      if (textBuf.trim()) blocks.push({ type: 'text', content: textBuf.trim() });

      return blocks.map(b => {
        if (b.type === 'text') {
          return `
            <div class="transcript-live-msg assistant">
              <div class="transcript-live-meta">
                <span class="transcript-live-role">assistant</span>
                <span class="transcript-live-time">${time}</span>
              </div>
              <div class="transcript-live-content">${this._formatText(b.content)}</div>
            </div>`;
        } else {
          return `
            <div class="transcript-live-msg tool_call">
              <div class="transcript-live-meta">
                <span class="transcript-live-role">${this._escapeHTML(b.name)}</span>
                <span class="transcript-live-time">${time}</span>
              </div>
              <div class="transcript-live-content">${this._formatToolArgs(b.args)}</div>
            </div>`;
        }
      }).join('');
    }

    // Tool results — format nicely
    if (role === 'tool_result') {
      return `
        <div class="transcript-live-msg tool_result">
          <div class="transcript-live-meta">
            <span class="transcript-live-role">result</span>
            <span class="transcript-live-time">${time}</span>
          </div>
          <div class="transcript-live-content">${this._formatToolResult(raw)}</div>
        </div>`;
    }

    // Regular messages (user, plain assistant)
    return `
      <div class="transcript-live-msg ${role}">
        <div class="transcript-live-meta">
          <span class="transcript-live-role">${role}</span>
          <span class="transcript-live-time">${time}</span>
        </div>
        <div class="transcript-live-content">${this._formatText(raw)}</div>
      </div>`;
  },

  _formatText(text) {
    let escaped = this._escapeHTML(text);
    // Convert markdown-style code blocks
    escaped = escaped.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Convert inline code
    escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Convert **bold**
    escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return escaped;
  },

  _formatToolArgs(argsStr) {
    // Try to parse as JSON and pretty-print
    try {
      // Handle truncated JSON
      let clean = argsStr;
      if (clean.endsWith('...')) clean = clean.slice(0, -3);
      const obj = JSON.parse(clean);
      // For Write/Edit tools, show file path prominently and truncate content
      if (obj.file_path) {
        let display = `<div class="tool-filepath">${this._escapeHTML(obj.file_path)}</div>`;
        if (obj.content) {
          const preview = obj.content.length > 300 ? obj.content.substring(0, 300) + '...' : obj.content;
          display += `<pre><code>${this._escapeHTML(preview)}</code></pre>`;
        } else if (obj.command) {
          display += `<pre><code>$ ${this._escapeHTML(obj.command)}</code></pre>`;
        } else {
          const rest = { ...obj };
          delete rest.file_path;
          display += `<pre><code>${this._escapeHTML(JSON.stringify(rest, null, 2).substring(0, 400))}</code></pre>`;
        }
        return display;
      }
      if (obj.command) {
        return `<pre><code>$ ${this._escapeHTML(obj.command)}</code></pre>`;
      }
      if (obj.pattern) {
        return `<pre><code>${this._escapeHTML(obj.pattern)}</code></pre>`;
      }
      return `<pre><code>${this._escapeHTML(JSON.stringify(obj, null, 2).substring(0, 500))}</code></pre>`;
    } catch {
      // Not valid JSON, show as-is but truncated
      const truncated = argsStr.length > 500 ? argsStr.substring(0, 500) + '...' : argsStr;
      return `<pre><code>${this._escapeHTML(truncated)}</code></pre>`;
    }
  },

  _formatToolResult(text) {
    const truncated = text.length > 1500 ? text.substring(0, 1500) + '...' : text;
    return `<pre><code>${this._escapeHTML(truncated)}</code></pre>`;
  },

  _fmtTime(ts) {
    try {
      return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return ''; }
  },

  _escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};

// Keep backward compat — dashboard.js calls Terminal.open()
const Terminal = SessionViewer;

// Wire up buttons
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('terminal-back').addEventListener('click', () => {
    SessionViewer.close();
  });

  document.getElementById('terminal-popout').addEventListener('click', () => {
    if (SessionViewer.currentSessionId) {
      window.open(`/terminal.html?session=${SessionViewer.currentSessionId}`, '_blank', 'width=1000,height=600');
    }
  });
});
