/**
 * Claude Code Command Center — Main App Logic
 *
 * Manages navigation, WebSocket connection, and global state.
 */

const App = {
  ws: null,
  sessions: {},
  settings: {},
  currentView: 'dashboard',

  init() {
    this.setupNavigation();
    this.setupModal();
    this.setupSettings();
    this.connectWebSocket();
    this.loadInitialData();
    this.loadSettings();
    this.setupHistory();
    Dashboard.initExpandedView();
  },

  // ---- Browser History (back/forward) ----
  setupHistory() {
    // Handle back/forward buttons
    window.addEventListener('popstate', (e) => {
      const state = e.state || { view: 'dashboard' };
      if (state.view === 'session' && state.sessionId) {
        // Reopen session transcript without pushing another state
        this._openSessionDirect(state.sessionId, state.sessionTitle);
      } else if (state.view === 'history' && state.transcript && state.sessionId) {
        // Reopen history transcript without pushing another state
        if (typeof History !== 'undefined') {
          History.showTranscriptDirect(state.sessionId, state.sessionTitle);
        }
      } else {
        // Close any open transcript/overlay and switch view
        if (typeof SessionViewer !== 'undefined') SessionViewer.close();
        if (typeof History !== 'undefined') History.closeTranscript();
        this._switchViewDirect(state.view || 'dashboard');
      }
    });

    // Set initial state
    window.history.replaceState({ view: 'dashboard' }, '', '#dashboard');
  },

  _openSessionDirect(sessionId, title) {
    if (typeof SessionViewer !== 'undefined') {
      SessionViewer.open(sessionId, title);
    }
  },

  _switchViewDirect(view) {
    this.currentView = view;
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    const tab = document.querySelector(`.nav-tab[data-view="${view}"]`);
    if (tab) tab.classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const el = document.getElementById(`view-${view}`);
    if (el) el.classList.add('active');
    if (view === 'history' && typeof History !== 'undefined') History.load();
    else if (view === 'analytics' && typeof Analytics !== 'undefined') Analytics.load();
  },

  // ---- Navigation ----
  setupNavigation() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const view = tab.dataset.view;
        this.switchView(view);
      });
    });
  },

  switchView(view) {
    if (typeof SessionViewer !== 'undefined') SessionViewer.close();
    this._switchViewDirect(view);
    window.history.pushState({ view }, '', `#${view}`);
  },

  // ---- Modal (disabled — see GitHub issue #4) ----
  setupModal() {},

  // ---- Settings ----
  async loadSettings() {
    try {
      const resp = await fetch('/api/settings');
      const data = await resp.json();
      this.settings = data.settings || {};
    } catch (e) {
      console.error('Failed to load settings:', e);
    }
  },

  setupSettings() {
    const modal = document.getElementById('settings-modal');
    const btn = document.getElementById('settings-btn');
    const cancel = document.getElementById('settings-cancel');
    const close = document.getElementById('settings-close');
    const save = document.getElementById('settings-save');
    const indicator = document.getElementById('settings-indicator');

    const openModal = () => {
      const keys = this.settings.jira_project_keys || [];
      document.getElementById('jira-keys').value = keys.join(', ');
      document.getElementById('jira-url').value = this.settings.jira_server_url || '';
      document.getElementById('summary-interval').value = this.settings.summary_interval || 5;
      document.getElementById('expanded-tile-items').value = this.settings.expanded_tile_items || 5;
      indicator.textContent = '';
      indicator.className = 'save-indicator';
      modal.style.display = 'flex';
    };

    const closeModal = () => { modal.style.display = 'none'; };

    btn.addEventListener('click', openModal);
    cancel.addEventListener('click', closeModal);
    close.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    // Tab switching
    modal.querySelectorAll('.settings-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        modal.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
        modal.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
      });
    });

    save.addEventListener('click', async () => {
      const keysRaw = document.getElementById('jira-keys').value;
      const keys = keysRaw.split(',').map(k => k.trim().toUpperCase()).filter(Boolean);
      const url = document.getElementById('jira-url').value.trim();
      const summaryInterval = parseInt(document.getElementById('summary-interval').value, 10) || 5;
      const expandedTileItems = parseInt(document.getElementById('expanded-tile-items').value, 10) || 5;
      try {
        const resp = await fetch('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            jira_project_keys: keys,
            jira_server_url: url || null,
            summary_interval: summaryInterval,
            expanded_tile_items: expandedTileItems,
          }),
        });
        const data = await resp.json();
        this.settings = data.settings || {};
        closeModal();
      } catch (e) {
        console.error('Failed to save settings:', e);
        indicator.textContent = 'Failed to save';
        indicator.className = 'save-indicator error';
      }
    });
  },

  setupFolderPicker() {
    const browseBtn = document.getElementById('browse-btn');
    const picker = document.getElementById('folder-picker');
    const pickerList = document.getElementById('folder-picker-list');
    const pickerPath = document.getElementById('folder-picker-path');
    const upBtn = document.getElementById('folder-up');
    const selectBtn = document.getElementById('folder-select');
    let currentPath = '~';

    const loadDir = async (path) => {
      try {
        const resp = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        currentPath = data.path;
        pickerPath.textContent = currentPath;
        document.getElementById('project-dir').value = currentPath;

        if (data.entries.length === 0) {
          pickerList.innerHTML = '<div class="folder-empty">No subdirectories</div>';
          return;
        }

        pickerList.innerHTML = data.entries.map(e =>
          `<div class="folder-entry" data-path="${e.path.replace(/"/g, '&quot;')}">
            <span class="folder-entry-icon">&#128193;</span>
            <span class="folder-entry-name">${e.name}</span>
          </div>`
        ).join('');

        pickerList.querySelectorAll('.folder-entry').forEach(el => {
          el.addEventListener('click', () => loadDir(el.dataset.path));
        });
      } catch (e) {
        console.error('Browse failed:', e);
      }
    };

    browseBtn.addEventListener('click', () => {
      picker.style.display = picker.style.display === 'none' ? 'block' : 'none';
      if (picker.style.display === 'block') {
        const current = document.getElementById('project-dir').value.trim();
        loadDir(current || '~');
      }
    });

    upBtn.addEventListener('click', () => {
      const parent = currentPath.replace(/\/[^/]+\/?$/, '') || '/';
      loadDir(parent);
    });

    selectBtn.addEventListener('click', () => {
      document.getElementById('project-dir').value = currentPath;
      picker.style.display = 'none';
    });
  },

  // ---- WebSocket ----
  connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/dashboard`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('Dashboard WebSocket connected');
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.handleMessage(data);
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    this.ws.onclose = () => {
      console.log('Dashboard WebSocket disconnected, reconnecting in 3s...');
      setTimeout(() => this.connectWebSocket(), 3000);
    };

    this.ws.onerror = (e) => {
      console.error('WebSocket error:', e);
    };

    // Keep alive
    this._pingInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send('ping');
      }
    }, 30000);
  },

  handleMessage(data) {
    if (data.type === 'initial_state') {
      this.sessions = {};
      (data.sessions || []).forEach(s => {
        this.sessions[s.id] = s;
      });
      Dashboard.render(this.sessions);
      this.updateStats();
    } else if (data.type === 'session_update') {
      const session = data.session;
      if (session.parent_session_id) {
        // Subagent update — route to parent card, never create a tile
        const parent = this.sessions[session.parent_session_id];
        if (parent) {
          const subs = parent.subagents || [];
          const idx = subs.findIndex(s => s.id === session.id);
          if (idx >= 0) subs[idx] = session;
          else subs.push(session);
          parent.subagents = subs;
          Dashboard.updateCard(parent);
        }
      } else {
        // Parent session update — preserve existing subagents if not provided
        if (!session.subagents && this.sessions[session.id]) {
          session.subagents = this.sessions[session.id].subagents || [];
        }
        this.sessions[session.id] = session;
        Dashboard.updateCard(session);
      }
      this.updateStats();
    }
  },

  // ---- Initial Data Load ----
  async loadInitialData() {
    try {
      const resp = await fetch('/api/sessions');
      const data = await resp.json();
      (data.sessions || []).forEach(s => {
        this.sessions[s.id] = s;
      });
      Dashboard.render(this.sessions);

      const analytics = await fetch('/api/analytics/summary');
      const summary = await analytics.json();
      document.getElementById('today-cost').textContent = `$${(summary.today_cost || 0).toFixed(2)}`;
      this.updateStats();
    } catch (e) {
      console.error('Failed to load initial data:', e);
    }
  },

  updateStats() {
    const active = Object.values(this.sessions).filter(
      s => s.status !== 'completed'
    ).length;
    document.getElementById('active-count').textContent = active;

    const todayCost = Object.values(this.sessions).reduce(
      (sum, s) => sum + (s.cost_usd || 0), 0
    );
    document.getElementById('today-cost').textContent = `$${todayCost.toFixed(2)}`;
  },
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => App.init());
