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

    // Assistant messages with tool calls: split on [Tool: Name] lines
    if (role === 'assistant' && raw.includes('[Tool:')) {
      const lines = raw.split('\n');
      const blocks = [];
      let currentBlock = null;

      for (const line of lines) {
        const toolMatch = line.match(/^\[Tool: (.+)\]$/);
        if (toolMatch) {
          // Flush previous block
          if (currentBlock) blocks.push(currentBlock);
          currentBlock = { type: 'tool_call', name: toolMatch[1], lines: [] };
        } else if (currentBlock && currentBlock.type === 'tool_call') {
          currentBlock.lines.push(line);
        } else {
          // Text before any tool call
          if (!currentBlock || currentBlock.type !== 'text') {
            if (currentBlock) blocks.push(currentBlock);
            currentBlock = { type: 'text', lines: [] };
          }
          currentBlock.lines.push(line);
        }
      }
      if (currentBlock) blocks.push(currentBlock);

      return blocks.map(b => {
        const content = b.lines.join('\n').trim();
        if (!content && b.type === 'text') return '';
        if (b.type === 'text') {
          return this._msgHTML('assistant', time, this._fmtText(content));
        }
        return `
          <div class="transcript-live-msg tool_call">
            <div class="transcript-live-meta">
              <span class="transcript-live-role">${this._escapeHTML(b.name)}</span>
              <span class="transcript-live-time">${time}</span>
            </div>
            <div class="transcript-live-content"><pre><code>${this._escapeHTML(content)}</code></pre></div>
          </div>`;
      }).join('');
    }

    // Tool results
    if (role === 'tool_result') {
      const truncated = raw.length > 1500 ? raw.substring(0, 1500) + '...' : raw;
      return `
        <div class="transcript-live-msg tool_result">
          <div class="transcript-live-meta">
            <span class="transcript-live-role">result</span>
            <span class="transcript-live-time">${time}</span>
          </div>
          <div class="transcript-live-content"><pre><code>${this._escapeHTML(truncated)}</code></pre></div>
        </div>`;
    }

    // Regular messages (user, plain assistant)
    return this._msgHTML(role, time, this._fmtText(raw));
  },

  _msgHTML(role, time, content) {
    return `
      <div class="transcript-live-msg ${role}">
        <div class="transcript-live-meta">
          <span class="transcript-live-role">${role}</span>
          <span class="transcript-live-time">${time}</span>
        </div>
        <div class="transcript-live-content">${content}</div>
      </div>`;
  },

  _fmtText(text) {
    let s = this._escapeHTML(text);
    s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return s;
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
