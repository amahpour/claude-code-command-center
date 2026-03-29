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

      const planGroups = this._detectPlanGroups(transcripts);
      container.innerHTML = this._renderTranscriptWithPlans(transcripts, planGroups);

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
    if (typeof marked !== 'undefined') {
      return marked.parse(text, { breaks: true, gfm: true });
    }
    // Fallback if marked.js not loaded
    let s = this._escapeHTML(text);
    s = s.replace(/\n/g, '<br>');
    return s;
  },

  _fmtTime(ts) {
    try {
      return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return ''; }
  },

  _detectPlanGroups(transcripts) {
    const groups = [];
    let current = null;

    for (let i = 0; i < transcripts.length; i++) {
      const t = transcripts[i];
      const content = t.content || '';

      if (!current) {
        // Look for EnterPlanMode in assistant messages
        if (t.role === 'assistant' && content.includes('[Tool: EnterPlanMode]')) {
          current = {
            startIndex: i,
            endIndex: null,
            planContent: null,
            planFilePath: null,
            status: 'in_progress',
            memberIndices: [i],
          };
        }
      } else {
        current.memberIndices.push(i);

        // Check for plan file Write
        if (t.role === 'assistant' && content.includes('[Tool: Write]')) {
          const extracted = this._extractPlanContent(content);
          if (extracted) {
            current.planContent = extracted.content;
            current.planFilePath = extracted.filePath;
          }
        }

        // Check for ExitPlanMode
        if (t.role === 'assistant' && content.includes('[Tool: ExitPlanMode]')) {
          current.endIndex = i;
          current.status = 'approved';
          groups.push(current);
          current = null;
        }
      }
    }

    // Plan still in progress (no ExitPlanMode yet)
    if (current) {
      current.endIndex = transcripts.length - 1;
      groups.push(current);
    }

    return groups;
  },

  _extractPlanContent(content) {
    // Find the Write tool block that targets a plans directory
    const lines = content.split('\n');
    let inWriteBlock = false;
    let filePath = null;
    const planLines = [];

    for (const line of lines) {
      if (line.match(/^\[Tool: Write\]$/)) {
        inWriteBlock = true;
        filePath = null;
        planLines.length = 0;
        continue;
      }
      if (line.match(/^\[Tool: .+\]$/)) {
        // Hit a different tool block — if we found a plan, stop
        if (inWriteBlock && filePath) break;
        inWriteBlock = false;
        continue;
      }
      if (inWriteBlock) {
        if (!filePath) {
          const trimmed = line.trim();
          if (trimmed.includes('.claude/plans/') && trimmed.endsWith('.md')) {
            filePath = trimmed;
          } else {
            inWriteBlock = false; // Not a plan write
          }
        } else {
          planLines.push(line);
        }
      }
    }

    if (filePath) {
      return { filePath, content: planLines.join('\n').trim() };
    }
    return null;
  },

  _renderTranscriptWithPlans(transcripts, planGroups) {
    if (planGroups.length === 0) {
      return transcripts.map(t => this._renderMessage(t)).join('');
    }

    // Build membership lookup
    const memberOf = {};
    for (let g = 0; g < planGroups.length; g++) {
      for (const idx of planGroups[g].memberIndices) {
        memberOf[idx] = g;
      }
    }

    const parts = [];
    const rendered = new Set();

    for (let i = 0; i < transcripts.length; i++) {
      if (memberOf[i] !== undefined) {
        const groupIdx = memberOf[i];
        if (!rendered.has(groupIdx)) {
          rendered.add(groupIdx);
          parts.push(this._renderPlanBlock(planGroups[groupIdx], transcripts));
        }
        // Skip individual entries that belong to a plan group
      } else {
        parts.push(this._renderMessage(transcripts[i]));
      }
    }

    return parts.join('');
  },

  _renderPlanBlock(group, transcripts) {
    const statusClass = group.status === 'approved' ? 'approved' : 'in-progress';
    const statusLabel = group.status === 'approved' ? 'Approved' : 'In Progress';
    const time = transcripts[group.startIndex].timestamp
      ? this._fmtTime(transcripts[group.startIndex].timestamp) : '';
    const fileDisplay = group.planFilePath
      ? group.planFilePath.replace(/^.*\.claude\/plans\//, '') : '';

    // Render plan content as markdown
    const planHtml = group.planContent
      ? this._fmtText(group.planContent)
      : '<em style="color: var(--text-dim)">No plan content found</em>';

    // Render raw tool calls for the expandable section
    const rawHtml = group.memberIndices
      .map(idx => this._renderMessage(transcripts[idx]))
      .join('');
    const callCount = group.memberIndices.length;

    const uid = Math.random().toString(36).slice(2, 8);

    return `
      <div class="plan-block" data-plan-status="${group.status}">
        <div class="plan-header">
          <span class="plan-icon">&#128203;</span>
          <span class="plan-title">Plan</span>
          <span class="plan-status-badge ${statusClass}">${statusLabel}</span>
          <span class="plan-file" title="${this._escapeHTML(group.planFilePath || '')}">${this._escapeHTML(fileDisplay)}</span>
          <span class="transcript-live-time">${time}</span>
        </div>
        <div class="plan-content chat-content">${planHtml}</div>
        <div class="plan-raw-toggle" onclick="this.classList.toggle('expanded'); document.getElementById('plan-raw-${uid}').classList.toggle('expanded')">
          <span class="tool-chevron">&#9656;</span>
          <span>Show raw tool calls (${callCount} entries)</span>
        </div>
        <div class="plan-raw" id="plan-raw-${uid}">
          ${rawHtml}
        </div>
      </div>`;
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
    window.history.back();
  });

});
