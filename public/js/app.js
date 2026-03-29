/**
 * Claude Code Command Center — Main App Logic
 *
 * Manages navigation, WebSocket connection, and global state.
 */

const App = {
  ws: null,
  sessions: {},
  currentView: 'dashboard',

  init() {
    this.setupNavigation();
    this.setupModal();
    this.connectWebSocket();
    this.loadInitialData();
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
    this.currentView = view;

    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.nav-tab[data-view="${view}"]`).classList.add('active');

    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(`view-${view}`).classList.add('active');

    // Trigger view-specific loading
    if (view === 'history' && typeof History !== 'undefined') {
      History.load();
    } else if (view === 'analytics' && typeof Analytics !== 'undefined') {
      Analytics.load();
    }
  },

  // ---- Modal ----
  setupModal() {
    const modal = document.getElementById('new-session-modal');
    const btn = document.getElementById('new-session-btn');
    const cancel = document.getElementById('modal-cancel');
    const launch = document.getElementById('modal-launch');

    btn.addEventListener('click', () => { modal.style.display = 'flex'; });
    cancel.addEventListener('click', () => {
      modal.style.display = 'none';
      document.getElementById('folder-picker').style.display = 'none';
    });
    modal.addEventListener('click', (e) => {
      if (e.target === modal) {
        modal.style.display = 'none';
        document.getElementById('folder-picker').style.display = 'none';
      }
    });

    launch.addEventListener('click', async () => {
      const dir = document.getElementById('project-dir').value.trim();
      const prompt = document.getElementById('session-prompt').value.trim();
      if (!dir) return;

      launch.disabled = true;
      launch.textContent = 'Launching...';
      try {
        const resp = await fetch('/api/sessions/new', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_dir: dir, prompt: prompt || null }),
        });
        if (resp.ok) {
          modal.style.display = 'none';
          document.getElementById('project-dir').value = '';
          document.getElementById('session-prompt').value = '';
          document.getElementById('folder-picker').style.display = 'none';
        } else {
          const err = await resp.json();
          alert('Failed: ' + (err.detail || 'Unknown error'));
        }
      } catch (e) {
        console.error('Failed to launch session:', e);
        alert('Failed to launch session');
      }
      launch.disabled = false;
      launch.textContent = 'Launch';
    });

    // Folder picker
    this.setupFolderPicker();
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
      this.sessions[session.id] = session;
      Dashboard.updateCard(session);
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
