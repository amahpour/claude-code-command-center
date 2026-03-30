/**
 * Dashboard — Session Cards & Grid
 */

const Dashboard = {
  _expandedSubagentSections: new Set(),
  _expandedSubagentTranscripts: new Set(),

  _emptyHTML: `<div class="empty-state" id="empty-state">
      <div class="empty-icon">&#9678;</div>
      <h2>No active sessions</h2>
      <p>Start a new Claude Code session or wait for hook events.</p>
    </div>`,

  render(sessions) {
    const grid = document.getElementById('session-grid');
    const list = Object.values(sessions);

    if (list.length === 0) {
      grid.innerHTML = this._emptyHTML;
      return;
    }

    grid.innerHTML = '';

    // Sort: waiting > working > idle > stale > completed
    const priority = { waiting: 1, working: 2, idle: 3, stale: 4, completed: 5 };
    list.sort((a, b) => (priority[a.status] || 9) - (priority[b.status] || 9));

    list.forEach(session => {
      grid.appendChild(this.createCard(session));
    });
  },

  createCard(session) {
    const card = document.createElement('div');
    card.className = 'session-card';
    card.dataset.sessionId = session.id;
    card.innerHTML = this._cardHTML(session);
    card.addEventListener('click', () => {
      const title = session.project_name || session.id;
      if (typeof Terminal !== 'undefined') {
        Terminal.open(session.id, title);
      }
      window.history.pushState(
        { view: 'session', sessionId: session.id, sessionTitle: title },
        '', `#session/${session.id}`
      );
    });
    return card;
  },

  updateCard(session) {
    const existing = document.querySelector(`.session-card[data-session-id="${session.id}"]`);
    if (existing) {
      existing.innerHTML = this._cardHTML(session);
      // Reload transcripts for any expanded subagent transcripts
      this._expandedSubagentTranscripts.forEach(agentId => {
        const container = document.getElementById(`subagent-transcript-${agentId}`);
        if (container && container.style.display !== 'none') {
          this._loadSubagentTranscript(agentId, container);
        }
      });
    } else {
      // New session — add card
      const grid = document.getElementById('session-grid');
      const empty = document.getElementById('empty-state');
      if (empty) empty.remove();
      const card = this.createCard(session);
      grid.prepend(card);
    }

    // Remove completed sessions from active view after a delay
    if (session.status === 'completed') {
      setTimeout(() => {
        const el = document.querySelector(`.session-card[data-session-id="${session.id}"]`);
        if (el) {
          el.style.opacity = '0.5';
        }
      }, 5000);
    }
  },

  _cardHTML(s) {
    const status = s.status || 'idle';
    const contextPercent = Math.min(s.context_usage_percent || 0, 100);
    const contextClass = contextPercent > 80 ? 'critical' : contextPercent > 60 ? 'high' : '';
    const duration = this._duration(s.started_at);
    const branch = s.git_branch || '-';
    const model = s.model || '-';

    let prLink = '';
    if (s.pr_url) {
      const url = s.pr_url;
      const isGitLab = url.includes('gitlab');
      const label = isGitLab ? 'MR' : 'PR';
      // Extract short ref (e.g., #42) from URL
      const match = url.match(/\/(?:pull|merge_requests)\/(\d+)/);
      const ref = match ? `${label} #${match[1]}` : `${label} &rarr;`;
      prLink = `<a href="${this._escapeHTML(url)}" class="card-pr-tag" target="_blank" onclick="event.stopPropagation()">${ref}</a>`;
    }

    let ticketTag = '';
    if (s.ticket_id) {
      const jiraUrl = (App.settings && App.settings.jira_server_url) || '';
      if (jiraUrl) {
        ticketTag = `<a href="${this._escapeHTML(jiraUrl)}/browse/${this._escapeHTML(s.ticket_id)}" class="card-ticket-link" target="_blank" onclick="event.stopPropagation()">${this._escapeHTML(s.ticket_id)}</a>`;
      } else {
        ticketTag = `<span class="card-ticket-tag">${this._escapeHTML(s.ticket_id)}</span>`;
      }
    } else {
      ticketTag = `<span class="card-ticket-tag empty">No ticket</span>`;
    }

    const sessionName = s.session_name ? `<div class="card-session-name">${this._escapeHTML(s.session_name)}</div>` : '';
    const effort = s.effort_level || '';
    const effortBadge = effort ? `<span class="card-effort ${effort}">${effort}</span>` : '';
    // Friendly model name
    const modelShort = model.replace('claude-', '').replace('-4-6', ' 4.6').replace('-4-5', ' 4.5');

    const displayTitle = s.display_name || s.project_name || s.id;
    const projectPath = s.project_path ? s.project_path.replace(/^\/Users\/[^/]+\//, '~/') : '';

    const isLocked = s.display_name_locked;
    const lockIcon = isLocked ? '&#128274;' : '&#128275;';
    const lockTitle = isLocked ? 'Title locked (click to unlock)' : 'Title auto-updates (click to lock)';

    const previewText = s.last_activity_preview || '';
    const previewLine = previewText
      ? `<div class="card-preview" onclick="event.stopPropagation(); Dashboard.togglePreview('${this._escapeHTML(s.id)}', this)">
          <span class="preview-chevron">&#9656;</span>
          <span class="preview-text">${this._escapeHTML(previewText)}</span>
        </div>
        <div class="card-preview-expanded" id="preview-${s.id}" style="display:none"></div>`
      : '';

    return `
      <div class="card-header">
        <span class="status-dot ${status}"></span>
        <span class="card-project" onclick="event.stopPropagation(); Dashboard.editDisplayName('${this._escapeHTML(s.id)}', '${this._escapeHTML(displayTitle)}')" title="Click to rename">${this._escapeHTML(displayTitle)}</span>
        <button class="card-lock-btn ${isLocked ? 'locked' : ''}" onclick="event.stopPropagation(); Dashboard.toggleLock('${this._escapeHTML(s.id)}', ${!isLocked})" title="${lockTitle}">${lockIcon}</button>
        <span class="card-status-label ${status}">${status}</span>
      </div>
      ${sessionName}
      <div class="card-meta">
        <span class="card-meta-item">&#x2387; ${this._escapeHTML(branch)}</span>
        <span class="card-meta-item">${this._escapeHTML(modelShort)}</span>
        ${effortBadge}
      </div>
      ${previewLine}
      ${s.task_description ? `<div class="card-task">${this._escapeHTML(s.task_description)}</div>` : ''}
      <div class="context-bar">
        <div class="context-bar-label">
          <span>Context</span>
          <span>${contextPercent.toFixed(0)}%</span>
        </div>
        <div class="context-bar-track">
          <div class="context-bar-fill ${contextClass}" style="width: ${contextPercent}%"></div>
        </div>
      </div>
      ${this._subagentsHTML(s.subagents)}
      <div class="card-footer">
        <span class="card-footer-left">
          <span class="card-cost">$${(s.cost_usd || 0).toFixed(4)}</span>
          <span>${duration}</span>
          <span class="card-ticket-area">
            ${ticketTag}
            <button class="card-ticket-edit" onclick="event.stopPropagation(); Dashboard.editTicketId('${this._escapeHTML(s.id)}', '${this._escapeHTML(s.ticket_id || '')}')" title="Edit ticket ID">&#9998;</button>
          </span>
          ${prLink}
        </span>
        ${projectPath ? `<span class="card-path" title="${this._escapeHTML(s.project_path)}">${this._escapeHTML(projectPath)}</span>` : ''}
      </div>
    `;
  },

  _duration(startedAt) {
    if (!startedAt) return '-';
    try {
      const start = new Date(startedAt);
      const now = new Date();
      const diff = Math.floor((now - start) / 1000);
      if (diff < 60) return `${diff}s`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m`;
      return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
    } catch {
      return '-';
    }
  },

  _inlineEdit(title, currentValue, onSave) {
    const modal = document.getElementById('inline-edit-modal');
    const input = document.getElementById('inline-edit-input');
    const titleEl = document.getElementById('inline-edit-title');
    const saveBtn = document.getElementById('inline-edit-save');
    const cancelBtn = document.getElementById('inline-edit-cancel');

    titleEl.textContent = title;
    input.value = currentValue;
    modal.style.display = 'flex';
    input.focus();
    input.select();

    const cleanup = () => {
      modal.style.display = 'none';
      saveBtn.replaceWith(saveBtn.cloneNode(true));
      cancelBtn.replaceWith(cancelBtn.cloneNode(true));
      input.removeEventListener('keydown', onKey);
      modal.removeEventListener('click', onBackdrop);
    };

    const save = () => { cleanup(); onSave(input.value); };
    const cancel = () => { cleanup(); };

    const onKey = (e) => {
      if (e.key === 'Enter') save();
      else if (e.key === 'Escape') cancel();
    };
    const onBackdrop = (e) => { if (e.target === modal) cancel(); };

    input.addEventListener('keydown', onKey);
    modal.addEventListener('click', onBackdrop);
    document.getElementById('inline-edit-save').addEventListener('click', save);
    document.getElementById('inline-edit-cancel').addEventListener('click', cancel);
  },

  editDisplayName(sessionId, currentValue) {
    this._inlineEdit('Rename session', currentValue, (val) => {
      fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: val.trim() || '' }),
      })
      .then(r => r.json())
      .then(data => {
        if (data.session) {
          App.sessions[data.session.id] = data.session;
          this.updateCard(data.session);
        }
      })
      .catch(e => console.error('Failed to rename session:', e));
    });
  },

  editTicketId(sessionId, currentValue) {
    this._inlineEdit('Edit ticket ID', currentValue, (val) => {
      fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticket_id: val.trim().toUpperCase() || '' }),
      })
      .then(r => r.json())
      .then(data => {
        if (data.session) {
          App.sessions[data.session.id] = data.session;
          this.updateCard(data.session);
        }
      })
      .catch(e => console.error('Failed to update ticket ID:', e));
    });
  },

  toggleLock(sessionId, locked) {
    fetch(`/api/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name_locked: locked }),
    })
    .then(r => r.json())
    .then(data => {
      if (data.session) {
        App.sessions[data.session.id] = data.session;
        this.updateCard(data.session);
      }
    })
    .catch(e => console.error('Failed to toggle lock:', e));
  },

  async togglePreview(sessionId, headerEl) {
    const expanded = document.getElementById(`preview-${sessionId}`);
    if (!expanded) return;
    const isOpen = expanded.style.display !== 'none';
    const chevron = headerEl.querySelector('.preview-chevron');

    if (isOpen) {
      expanded.style.display = 'none';
      if (chevron) chevron.style.transform = '';
      return;
    }

    try {
      const resp = await fetch(`/api/sessions/${sessionId}/transcript?limit=3`);
      const data = await resp.json();
      const msgs = data.transcripts || [];
      expanded.innerHTML = msgs.map(t => {
        const roleLabel = t.role === 'user' ? 'U' : t.role === 'assistant' ? 'A' : 'T';
        const text = (t.content || '').substring(0, 200);
        return `<div class="preview-msg"><span class="preview-role ${t.role}">${roleLabel}</span> ${this._escapeHTML(text)}</div>`;
      }).join('');
      expanded.style.display = 'block';
      if (chevron) chevron.style.transform = 'rotate(90deg)';
    } catch (e) {
      console.error('Preview fetch failed:', e);
    }
  },

  _escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },

  _subagentsHTML(subagents) {
    if (!subagents || subagents.length === 0) return '';
    // Only show actively working subagents in the tile
    const running = subagents.filter(a => a.status === 'working');
    if (running.length === 0) return '';
    // Use first subagent's parent_session_id as section key, fallback to combined IDs
    const sectionKey = running[0]?.parent_session_id || running.map(a => a.id).join(',');
    const sectionOpen = this._expandedSubagentSections.has(sectionKey);
    const rows = running.map(a => {
      const type = a.agent_type || 'agent';
      const desc = a.task_description ? this._escapeHTML(a.task_description.substring(0, 100)) : '';
      const duration = this._duration(a.started_at);
      const transcriptOpen = this._expandedSubagentTranscripts.has(a.id);
      return `<div class="subagent-item">
        <div class="subagent-row" onclick="event.stopPropagation(); Dashboard.toggleSubagentTranscript('${this._escapeHTML(a.id)}', this)">
          <span class="status-dot working small"></span>
          <span class="subagent-type">${this._escapeHTML(type)}</span>
          ${desc ? `<span class="subagent-desc">${desc}</span>` : ''}
          <span class="subagent-duration">${duration}</span>
          <span class="subagent-expand preview-chevron" ${transcriptOpen ? 'style="transform:rotate(90deg)"' : ''}>&#9656;</span>
        </div>
        <div class="subagent-transcript" id="subagent-transcript-${a.id}" style="display:${transcriptOpen ? 'block' : 'none'}"></div>
      </div>`;
    }).join('');
    return `<div class="card-subagents" data-section-key="${this._escapeHTML(sectionKey)}">
      <div class="subagents-header" onclick="event.stopPropagation(); Dashboard.toggleSubagents(this)">
        <span class="preview-chevron" ${sectionOpen ? 'style="transform:rotate(90deg)"' : ''}>&#9656;</span>
        <span>${running.length} subagent${running.length !== 1 ? 's' : ''} running</span>
      </div>
      <div class="subagents-list" style="display:${sectionOpen ? 'block' : 'none'}">${rows}</div>
    </div>`;
  },

  toggleSubagents(headerEl) {
    const list = headerEl.nextElementSibling;
    const chevron = headerEl.querySelector('.preview-chevron');
    const sectionKey = headerEl.closest('.card-subagents')?.dataset.sectionKey;
    if (!list) return;
    const isOpen = list.style.display !== 'none';
    list.style.display = isOpen ? 'none' : 'block';
    if (chevron) chevron.style.transform = isOpen ? '' : 'rotate(90deg)';
    if (sectionKey) {
      if (isOpen) this._expandedSubagentSections.delete(sectionKey);
      else this._expandedSubagentSections.add(sectionKey);
    }
  },

  async toggleSubagentTranscript(agentId, rowEl) {
    const container = document.getElementById(`subagent-transcript-${agentId}`);
    if (!container) return;
    const chevron = rowEl.querySelector('.subagent-expand');
    const isOpen = container.style.display !== 'none';

    if (isOpen) {
      container.style.display = 'none';
      if (chevron) chevron.style.transform = '';
      this._expandedSubagentTranscripts.delete(agentId);
      return;
    }

    this._expandedSubagentTranscripts.add(agentId);
    this._loadSubagentTranscript(agentId, container);
    container.style.display = 'block';
    if (chevron) chevron.style.transform = 'rotate(90deg)';
  },

  async _loadSubagentTranscript(agentId, container) {
    container.innerHTML = '<div class="subagent-loading">Loading...</div>';
    try {
      const resp = await fetch(`/api/sessions/${agentId}/transcript?limit=20`);
      const data = await resp.json();
      const msgs = data.transcripts || [];
      if (msgs.length === 0) {
        container.innerHTML = '<div class="subagent-loading">No transcript available</div>';
        return;
      }
      container.innerHTML = msgs.map(t => {
        const roleLabel = t.role === 'user' ? 'U' : t.role === 'assistant' ? 'A' : 'T';
        const text = (t.content || '').substring(0, 300);
        return `<div class="preview-msg"><span class="preview-role ${t.role}">${roleLabel}</span> ${this._escapeHTML(text)}</div>`;
      }).join('');
    } catch (e) {
      container.innerHTML = '<div class="subagent-loading">Failed to load transcript</div>';
    }
  },
};
