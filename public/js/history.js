/**
 * History — Session History Browser with Search and Transcript Viewer
 */

const History = {
  currentOffset: 0,
  pageSize: 20,
  loaded: false,

  async load() {
    const container = document.getElementById('view-history');
    if (!this.loaded) {
      container.innerHTML = this._layoutHTML();
      this._setupSearch();
      this.loaded = true;
    }
    await this.fetchSessions();
  },

  _layoutHTML() {
    return `
      <div class="history-header">
        <input type="text" class="input search-input" id="history-search"
               placeholder="Search transcripts..." />
        <button class="btn" id="history-search-btn">Search</button>
      </div>
      <div id="history-content">
        <table class="history-table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Branch</th>
              <th>Ticket</th>
              <th>Model</th>
              <th>Status</th>
              <th>Cost</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody id="history-tbody"></tbody>
        </table>
        <div class="pagination" id="history-pagination"></div>
      </div>
      <div id="transcript-view" style="display:none">
        <div class="transcript-container">
          <div class="transcript-header">
            <button class="btn btn-sm" id="transcript-back">&larr; Back to History</button>
            <h2 id="transcript-title"></h2>
          </div>
          <div id="transcript-messages"></div>
          <div class="pagination" id="transcript-pagination"></div>
        </div>
      </div>
    `;
  },

  _setupSearch() {
    const input = document.getElementById('history-search');
    const btn = document.getElementById('history-search-btn');

    const doSearch = () => {
      const q = input.value.trim();
      if (q) {
        this.search(q);
      } else {
        this.fetchSessions();
      }
    };

    btn.addEventListener('click', doSearch);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') doSearch();
    });

    document.getElementById('transcript-back').addEventListener('click', () => {
      document.getElementById('transcript-view').style.display = 'none';
      document.getElementById('history-content').style.display = 'block';
    });
  },

  async fetchSessions() {
    try {
      const resp = await fetch(`/api/history?limit=${this.pageSize}&offset=${this.currentOffset}`);
      const data = await resp.json();
      this.renderTable(data.sessions);
      this.renderPagination(data.sessions.length);
    } catch (e) {
      console.error('Failed to load history:', e);
    }
  },

  async search(query) {
    try {
      const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const data = await resp.json();
      this.renderSearchResults(data.results, query);
    } catch (e) {
      console.error('Search failed:', e);
    }
  },

  renderTable(sessions) {
    const tbody = document.getElementById('history-tbody');
    if (!sessions.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:40px">No sessions found</td></tr>`;
      return;
    }

    tbody.innerHTML = sessions.map(s => `
      <tr data-session-id="${this._esc(s.id)}">
        <td><strong>${this._esc(s.project_name || s.id)}</strong></td>
        <td>${this._esc(s.git_branch || '-')}</td>
        <td>${this._renderTicket(s.ticket_id)}</td>
        <td>${this._esc(s.model || '-')}</td>
        <td><span class="card-status-label ${s.status}">${s.status}</span></td>
        <td>$${(s.cost_usd || 0).toFixed(4)}</td>
        <td>${this._formatDate(s.created_at)}</td>
      </tr>
    `).join('');

    tbody.querySelectorAll('tr').forEach(row => {
      row.addEventListener('click', () => {
        this.showTranscript(row.dataset.sessionId, row.querySelector('strong').textContent);
      });
    });
  },

  renderSearchResults(results, query) {
    const tbody = document.getElementById('history-tbody');
    if (!results.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:40px">No results for "${this._esc(query)}"</td></tr>`;
      return;
    }

    // Group by session
    const bySession = {};
    results.forEach(r => {
      if (!bySession[r.session_id]) bySession[r.session_id] = [];
      bySession[r.session_id].push(r);
    });

    tbody.innerHTML = Object.entries(bySession).map(([sid, entries]) => `
      <tr data-session-id="${this._esc(sid)}">
        <td><strong>${this._esc(sid)}</strong></td>
        <td colspan="3">${entries.length} match${entries.length !== 1 ? 'es' : ''}: ${this._esc(entries[0].highlighted || entries[0].content).substring(0, 100)}...</td>
        <td></td>
        <td>${this._formatDate(entries[0].timestamp)}</td>
      </tr>
    `).join('');

    tbody.querySelectorAll('tr').forEach(row => {
      row.addEventListener('click', () => {
        this.showTranscript(row.dataset.sessionId, row.dataset.sessionId);
      });
    });
  },

  renderPagination(count) {
    const pagination = document.getElementById('history-pagination');
    const hasPrev = this.currentOffset > 0;
    const hasNext = count >= this.pageSize;

    pagination.innerHTML = `
      ${hasPrev ? `<button class="btn btn-sm" id="hist-prev">&larr; Prev</button>` : ''}
      ${hasNext ? `<button class="btn btn-sm" id="hist-next">Next &rarr;</button>` : ''}
    `;

    if (hasPrev) {
      document.getElementById('hist-prev').addEventListener('click', () => {
        this.currentOffset = Math.max(0, this.currentOffset - this.pageSize);
        this.fetchSessions();
      });
    }
    if (hasNext) {
      document.getElementById('hist-next').addEventListener('click', () => {
        this.currentOffset += this.pageSize;
        this.fetchSessions();
      });
    }
  },

  async showTranscript(sessionId, title) {
    document.getElementById('history-content').style.display = 'none';
    document.getElementById('transcript-view').style.display = 'block';
    document.getElementById('transcript-title').textContent = title || sessionId;

    try {
      const resp = await fetch(`/api/sessions/${sessionId}/transcript`);
      const data = await resp.json();
      this.renderTranscript(data.transcripts);
    } catch (e) {
      console.error('Failed to load transcript:', e);
      document.getElementById('transcript-messages').innerHTML =
        '<p style="color:var(--text-muted)">Failed to load transcript</p>';
    }
  },

  renderTranscript(transcripts) {
    const container = document.getElementById('transcript-messages');
    if (!transcripts.length) {
      container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:40px">No transcript entries</p>';
      return;
    }

    container.innerHTML = transcripts.map(t => {
      const role = t.role || 'unknown';
      const isCollapsible = role === 'tool_use' || role === 'tool_result';
      const time = t.timestamp ? this._formatTime(t.timestamp) : '';

      return `
        <div class="transcript-message ${role}${isCollapsible ? '' : ''}" ${isCollapsible ? 'onclick="this.classList.toggle(\'expanded\')"' : ''}>
          <span class="transcript-role">${this._esc(role)}</span>
          <span class="transcript-time">${time}</span>
          <button class="transcript-copy" onclick="event.stopPropagation();navigator.clipboard.writeText(this.parentElement.querySelector('.transcript-text').textContent)">Copy</button>
          <div class="transcript-text">${this._esc(t.content || '')}</div>
        </div>
      `;
    }).join('');
  },

  _formatDate(dateStr) {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return dateStr; }
  },

  _formatTime(dateStr) {
    if (!dateStr) return '';
    try {
      return new Date(dateStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return ''; }
  },

  _renderTicket(ticketId) {
    if (!ticketId) return '-';
    const jiraUrl = (App.settings && App.settings.jira_server_url) || '';
    if (jiraUrl) {
      return `<a href="${this._esc(jiraUrl)}/browse/${this._esc(ticketId)}" target="_blank" onclick="event.stopPropagation()">${this._esc(ticketId)}</a>`;
    }
    return this._esc(ticketId);
  },

  _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};
