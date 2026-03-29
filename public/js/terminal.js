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

    this._lastTranscriptId = 0;
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

      // Detect changes by comparing last entry's ID
      const lastId = transcripts.length > 0 ? transcripts[transcripts.length - 1].id : 0;
      if (lastId === this._lastTranscriptId) return;
      this._lastTranscriptId = lastId;

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
    this._lastTranscriptId = 0;
    const container = document.getElementById('terminal-container');
    if (container) container.innerHTML = '';
  },

  _renderMessage(t) {
    const role = t.role || 'unknown';
    const time = t.timestamp ? this._fmtTime(t.timestamp) : '';
    const raw = t.content || '';

    // Assistant messages with tool calls
    if (role === 'assistant' && raw.includes('[Tool:')) {
      const lines = raw.split('\n');
      const blocks = [];
      let currentBlock = null;

      for (const line of lines) {
        const toolMatch = line.match(/^\[Tool: (.+)\]$/);
        if (toolMatch) {
          if (currentBlock) blocks.push(currentBlock);
          currentBlock = { type: 'tool_call', name: toolMatch[1], lines: [] };
        } else if (currentBlock && currentBlock.type === 'tool_call') {
          currentBlock.lines.push(line);
        } else {
          if (!currentBlock || currentBlock.type !== 'text') {
            if (currentBlock) blocks.push(currentBlock);
            currentBlock = { type: 'text', lines: [] };
          }
          currentBlock.lines.push(line);
        }
      }
      if (currentBlock) blocks.push(currentBlock);

      // Separate text blocks from tool blocks
      const textBlocks = blocks.filter(b => b.type === 'text' && b.lines.join('\n').trim());
      const toolBlocks = blocks.filter(b => b.type === 'tool_call');
      let html = '';

      // Render text part as assistant message
      if (textBlocks.length > 0) {
        const textContent = textBlocks.map(b => b.lines.join('\n').trim()).join('\n');
        html += this._chatBubble('assistant', 'A', time, this._fmtText(textContent));
      }

      // Render tool calls as a grouped block
      if (toolBlocks.length > 0) {
        const toolItems = toolBlocks.map(b => {
          const content = b.lines.join('\n').trim();
          const preview = this._toolPreview(b.name, content);
          const uid = Math.random().toString(36).slice(2, 8);
          return `
            <div class="tool-item" onclick="this.classList.toggle('expanded')">
              <div class="tool-item-header">
                <span class="tool-chevron">&#9656;</span>
                <span class="tool-name-badge">${this._escapeHTML(b.name)}</span>
                <span class="tool-preview">${this._escapeHTML(preview)}</span>
              </div>
              <div class="tool-item-body"><pre><code>${this._escapeHTML(content)}</code></pre></div>
            </div>`;
        }).join('');

        html += `
          <div class="tool-group">
            <div class="tool-group-header">
              <span class="tool-group-icon">&#128295;</span>
              <span>${toolBlocks.length} tool call${toolBlocks.length > 1 ? 's' : ''}</span>
              <span class="transcript-live-time">${time}</span>
            </div>
            ${toolItems}
          </div>`;
      }
      return html;
    }

    // Tool results — collapsible
    if (role === 'tool_result') {
      const uid = Math.random().toString(36).slice(2, 8);
      const firstLine = raw.split('\n')[0].substring(0, 120);
      return `
        <div class="tool-result-block" onclick="this.classList.toggle('expanded')">
          <div class="tool-result-header">
            <span class="tool-chevron">&#9656;</span>
            <span class="tool-result-label">&#8629; output</span>
            <span class="tool-result-preview">${this._escapeHTML(firstLine)}</span>
          </div>
          <div class="tool-result-body"><pre><code>${this._escapeHTML(raw)}</code></pre></div>
        </div>`;
    }

    // User message
    if (role === 'user') {
      return this._chatBubble('user', 'U', time, this._fmtText(raw));
    }

    // Plain assistant message
    return this._chatBubble('assistant', 'A', time, this._fmtText(raw));
  },

  _chatBubble(role, avatar, time, content) {
    const avatarClass = role === 'user' ? 'avatar-user' : 'avatar-assistant';
    return `
      <div class="chat-msg ${role}">
        <div class="chat-avatar ${avatarClass}">${avatar}</div>
        <div class="chat-body">
          <div class="chat-header">
            <span class="chat-role">${role === 'user' ? 'User' : 'Assistant'}</span>
            <span class="chat-time">${time}</span>
          </div>
          <div class="chat-content">${content}</div>
        </div>
      </div>`;
  },

  _toolPreview(name, content) {
    const firstLine = content.split('\n')[0].trim();
    if (name === 'Bash' && firstLine.startsWith('$')) return firstLine;
    if (name === 'Read' || name === 'Write' || name === 'Edit') return firstLine;
    if (name === 'Grep' || name === 'Glob') return firstLine;
    return firstLine.substring(0, 100);
  },

  _fmtText(text) {
    let s = this._escapeHTML(text);
    // Code blocks first (preserve newlines inside)
    s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Split on pre blocks to only process non-code sections
    const parts = s.split(/(<pre>[\s\S]*?<\/pre>)/);
    s = parts.map(p => {
      if (p.startsWith('<pre>')) return p;
      // Tables — convert pipe-delimited rows
      p = p.replace(/((?:^\|.+\|\s*$\n?)+)/gm, (match) => {
        const rows = match.trim().split('\n').filter(r => r.trim());
        if (rows.length < 2) return match;
        // Check if second row is a separator (|---|---|)
        const isSep = (r) => /^\|[\s\-:]+\|/.test(r);
        const parseRow = (r) => r.split('|').slice(1, -1).map(c => c.trim());
        let html = '<table class="md-table">';
        let startData = 0;
        if (isSep(rows[1])) {
          // First row is header
          html += '<thead><tr>' + parseRow(rows[0]).map(c => `<th>${c}</th>`).join('') + '</tr></thead>';
          startData = 2;
        }
        html += '<tbody>';
        for (let i = startData; i < rows.length; i++) {
          if (isSep(rows[i])) continue;
          html += '<tr>' + parseRow(rows[i]).map(c => `<td>${c}</td>`).join('') + '</tr>';
        }
        html += '</tbody></table>';
        return html;
      });
      // Headings
      p = p.replace(/^### (.+)$/gm, '<h4>$1</h4>');
      p = p.replace(/^## (.+)$/gm, '<h3>$1</h3>');
      p = p.replace(/^# (.+)$/gm, '<h2>$1</h2>');
      // Horizontal rules
      p = p.replace(/^---+$/gm, '<hr>');
      // Inline formatting
      p = p.replace(/`([^`]+)`/g, '<code>$1</code>');
      p = p.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      // List items (- or * at start of line)
      p = p.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');
      p = p.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
      // Numbered lists
      p = p.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
      // Newlines to <br> for remaining text
      p = p.replace(/\n/g, '<br>');
      // Clean up extra <br> around block elements
      p = p.replace(/<br>(<h[234]>)/g, '$1');
      p = p.replace(/(<\/h[234]>)<br>/g, '$1');
      p = p.replace(/<br>(<hr>)<br>/g, '$1');
      p = p.replace(/<br>(<ul>)/g, '$1');
      p = p.replace(/(<\/ul>)<br>/g, '$1');
      return p;
    }).join('');
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
