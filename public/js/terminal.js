/**
 * Session Viewer — Live Transcript Display
 *
 * Shows a live-updating transcript when clicking a session card.
 * Uses incremental fetching (after_id) to append new messages
 * without re-rendering the entire DOM.
 */

const SessionViewer = {
  currentSessionId: null,
  _pollInterval: null,
  _lastTranscriptId: 0,

  open(sessionId, title) {
    this.currentSessionId = sessionId;
    const overlay = document.getElementById('terminal-overlay');
    const titleEl = document.getElementById('terminal-title');

    titleEl.textContent = title || sessionId;
    overlay.style.display = 'flex';

    this._cleanup();
    this._showTranscript(sessionId);
  },

  async _showTranscript(sessionId) {
    const container = document.getElementById('terminal-container');
    container.innerHTML = `
      <div class="transcript-live" id="transcript-live">
        <div class="transcript-live-header">Live Session Transcript</div>
        <div class="transcript-live-messages" id="transcript-live-messages"></div>
      </div>
    `;

    this._lastTranscriptId = 0;
    await this._fetchTranscript(sessionId, true);

    // Poll for new transcript entries every 2 seconds
    this._pollInterval = setInterval(() => {
      this._fetchTranscript(sessionId, false);
    }, 2000);
  },

  async _fetchTranscript(sessionId, isInitial) {
    try {
      const url = isInitial
        ? `/api/sessions/${sessionId}/transcript?limit=1000`
        : `/api/sessions/${sessionId}/transcript?after_id=${this._lastTranscriptId}&limit=200`;

      const resp = await fetch(url);
      const data = await resp.json();
      const transcripts = data.transcripts || [];

      if (transcripts.length === 0) return;

      const lastId = transcripts[transcripts.length - 1].id;
      this._lastTranscriptId = lastId;

      const container = document.getElementById('transcript-live-messages');
      if (!container) return;

      if (isInitial) {
        // Full render for initial load (supports plan/agent grouping)
        const planGroups = this._detectPlanGroups(transcripts);
        const agentGroups = this._detectAgentGroups(transcripts);
        container.innerHTML = this._renderTranscriptWithGroups(transcripts, planGroups, agentGroups);
        container.scrollTop = container.scrollHeight;
      } else {
        // Incremental: append only new messages without touching existing DOM
        const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;
        const html = transcripts.map(t => this._renderMessage(t)).join('');
        container.insertAdjacentHTML('beforeend', html);
        if (wasAtBottom) {
          container.scrollTop = container.scrollHeight;
        }
      }
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
    if (this._pollInterval) {
      clearInterval(this._pollInterval);
      this._pollInterval = null;
    }
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

        if (t.role === 'assistant' && content.includes('[Tool: Write]')) {
          const extracted = this._extractPlanContent(content);
          if (extracted) {
            current.planContent = extracted.content;
            current.planFilePath = extracted.filePath;
          }
        }

        if (t.role === 'assistant' && content.includes('[Tool: ExitPlanMode]')) {
          current.endIndex = i;
          current.status = 'approved';
          groups.push(current);
          current = null;
        }
      }
    }

    if (current) {
      current.endIndex = transcripts.length - 1;
      groups.push(current);
    }

    return groups;
  },

  _extractPlanContent(content) {
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
            inWriteBlock = false;
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

  _detectAgentGroups(transcripts) {
    const groups = [];
    for (let i = 0; i < transcripts.length; i++) {
      const t = transcripts[i];
      if (t.role !== 'assistant' || !t.content) continue;
      if (!t.content.includes('[Tool: Agent]')) continue;

      const lines = t.content.split('\n');
      const toolIdx = lines.findIndex(l => l.trim() === '[Tool: Agent]');
      if (toolIdx < 0) continue;

      const promptLines = [];
      for (let j = toolIdx + 1; j < lines.length; j++) {
        if (lines[j].match(/^\[Tool: .+\]$/)) break;
        promptLines.push(lines[j]);
      }
      const prompt = promptLines.join('\n').trim();

      let agentId = null;
      let agentType = null;
      let resultSummary = '';
      let resultIdx = -1;
      for (let j = i + 1; j < Math.min(i + 10, transcripts.length); j++) {
        const r = transcripts[j];
        if (r.role === 'tool_result' && r.content) {
          try {
            const parsed = JSON.parse(r.content);
            if (parsed.agentId) {
              agentId = 'agent-' + parsed.agentId;
              agentType = parsed.agentType || 'agent';
              resultSummary = parsed.content || parsed.result || '';
              resultIdx = j;
              break;
            }
          } catch {
            // Not JSON or not an agent result
          }
        }
        if (r.role === 'assistant') break;
      }

      const memberIndices = [i];
      if (resultIdx >= 0) memberIndices.push(resultIdx);

      groups.push({
        startIndex: i,
        endIndex: resultIdx >= 0 ? resultIdx : i,
        memberIndices,
        prompt: prompt.substring(0, 200),
        agentId,
        agentType: agentType || 'agent',
        resultSummary,
        timestamp: t.timestamp,
      });
    }
    return groups;
  },

  _renderAgentBlock(group) {
    const time = group.timestamp ? this._fmtTime(group.timestamp) : '';
    const uid = Math.random().toString(36).slice(2, 8);
    const promptPreview = group.prompt.split('\n')[0].substring(0, 100);

    let transcriptSection = '';
    if (group.agentId) {
      transcriptSection = `
        <div class="agent-transcript-toggle" onclick="event.stopPropagation(); SessionViewer.toggleAgentTranscript('${group.agentId}', '${uid}')">
          <span class="tool-chevron" id="agent-chevron-${uid}">&#9656;</span>
          <span>View full subagent conversation</span>
        </div>
        <div class="agent-transcript-content" id="agent-transcript-${uid}" style="display:none"></div>`;
    }

    return `
      <div class="agent-block">
        <div class="agent-block-header">
          <span class="agent-block-icon">&#129302;</span>
          <span class="agent-block-title">Spawned Agent</span>
          <span class="agent-type-badge">${this._escapeHTML(group.agentType)}</span>
          <span class="transcript-live-time">${time}</span>
        </div>
        <div class="agent-block-prompt">${this._escapeHTML(promptPreview)}</div>
        ${group.resultSummary ? `<div class="agent-block-result">${this._fmtText(String(group.resultSummary).substring(0, 500))}</div>` : ''}
        ${transcriptSection}
      </div>`;
  },

  async toggleAgentTranscript(agentId, uid) {
    const container = document.getElementById(`agent-transcript-${uid}`);
    const chevron = document.getElementById(`agent-chevron-${uid}`);
    if (!container) return;
    const isOpen = container.style.display !== 'none';
    if (isOpen) {
      container.style.display = 'none';
      if (chevron) chevron.style.transform = '';
      return;
    }
    container.style.display = 'block';
    if (chevron) chevron.style.transform = 'rotate(90deg)';
    container.innerHTML = '<div style="color: var(--text-dim); font-style: italic; padding: 8px;">Loading subagent transcript...</div>';
    try {
      const resp = await fetch(`/api/sessions/${agentId}/transcript?limit=200`);
      const data = await resp.json();
      const msgs = data.transcripts || [];
      if (msgs.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-style: italic; padding: 8px;">No transcript available</div>';
        return;
      }
      container.innerHTML = msgs.map(t => this._renderMessage(t)).join('');
    } catch {
      container.innerHTML = '<div style="color: var(--text-dim); font-style: italic; padding: 8px;">Failed to load transcript</div>';
    }
  },

  _renderTranscriptWithGroups(transcripts, planGroups, agentGroups) {
    const allGroups = planGroups.length + agentGroups.length;
    if (allGroups === 0) {
      return transcripts.map(t => this._renderMessage(t)).join('');
    }

    const memberOf = {};
    for (let g = 0; g < planGroups.length; g++) {
      for (const idx of planGroups[g].memberIndices) {
        memberOf[idx] = { type: 'plan', groupIdx: g };
      }
    }
    for (let g = 0; g < agentGroups.length; g++) {
      for (const idx of agentGroups[g].memberIndices) {
        memberOf[idx] = { type: 'agent', groupIdx: g };
      }
    }

    const parts = [];
    const rendered = new Set();

    for (let i = 0; i < transcripts.length; i++) {
      if (memberOf[i] !== undefined) {
        const { type, groupIdx } = memberOf[i];
        const key = `${type}-${groupIdx}`;
        if (!rendered.has(key)) {
          rendered.add(key);
          if (type === 'plan') {
            parts.push(this._renderPlanBlock(planGroups[groupIdx], transcripts));
          } else {
            parts.push(this._renderAgentBlock(agentGroups[groupIdx]));
          }
        }
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

    const planHtml = group.planContent
      ? this._fmtText(group.planContent)
      : '<em style="color: var(--text-dim)">No plan content found</em>';

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

// Backward compat — dashboard.js calls Terminal.open()
const Terminal = SessionViewer;

// Wire up back button
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('terminal-back').addEventListener('click', () => {
    SessionViewer.close();
    window.history.back();
  });
});
