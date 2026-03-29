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
      if (typeof Terminal !== 'undefined') {
        Terminal.open(session.id, session.project_name || session.id);
      }
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
      prLink = `<a href="${this._escapeHTML(s.pr_url)}" class="card-pr-link" target="_blank" onclick="event.stopPropagation()">PR &rarr;</a>`;
    }

    return `
      <div class="card-header">
        <span class="status-dot ${status}"></span>
        <span class="card-project">${this._escapeHTML(s.project_name || s.id)}</span>
        <span class="card-status-label ${status}">${status}</span>
      </div>
      <div class="card-meta">
        <span class="card-meta-item">&#x2387; ${this._escapeHTML(branch)}</span>
        <span class="card-meta-item">${this._escapeHTML(model)}</span>
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

  _escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};
