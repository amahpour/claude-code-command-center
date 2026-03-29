/**
 * Dashboard — Session Cards & Grid
 */

const Dashboard = {
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

    return `
      <div class="card-header">
        <span class="status-dot ${status}"></span>
        <span class="card-project">${this._escapeHTML(s.project_name || s.id)}</span>
        <span class="card-status-label ${status}">${status}</span>
      </div>
      ${sessionName}
      <div class="card-meta">
        <span class="card-meta-item">&#x2387; ${this._escapeHTML(branch)}</span>
        <span class="card-meta-item">${this._escapeHTML(modelShort)}</span>
        ${effortBadge}
      </div>
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
      <div class="card-footer">
        <span class="card-cost">$${(s.cost_usd || 0).toFixed(4)}</span>
        <span>${duration}</span>
        <span class="card-ticket-area">
          ${ticketTag}
          <button class="card-ticket-edit" onclick="event.stopPropagation(); Dashboard.editTicketId('${this._escapeHTML(s.id)}', '${this._escapeHTML(s.ticket_id || '')}')" title="Edit ticket ID">&#9998;</button>
        </span>
        ${prLink}
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

  editTicketId(sessionId, currentValue) {
    const newValue = prompt('Enter Jira ticket ID (e.g., CIT-42):', currentValue);
    if (newValue === null) return;
    fetch(`/api/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket_id: newValue.trim().toUpperCase() || '' }),
    })
    .then(r => r.json())
    .then(data => {
      if (data.session) {
        App.sessions[data.session.id] = data.session;
        this.updateCard(data.session);
      }
    })
    .catch(e => console.error('Failed to update ticket ID:', e));
  },

  _escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};
