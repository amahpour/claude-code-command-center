/**
 * Terminal View — xterm.js terminal with WebSocket PTY streaming
 */

const Terminal = {
  term: null,
  fitAddon: null,
  ws: null,
  currentSessionId: null,

  open(sessionId, title) {
    this.currentSessionId = sessionId;
    const overlay = document.getElementById('terminal-overlay');
    const titleEl = document.getElementById('terminal-title');
    const container = document.getElementById('terminal-container');

    titleEl.textContent = title || sessionId;
    overlay.style.display = 'flex';

    // Clean up previous terminal
    if (this.term) {
      this.term.dispose();
      this.term = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    container.innerHTML = '';

    // Initialize xterm.js
    this.term = new window.Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
      theme: {
        background: '#0f0f1a',
        foreground: '#e0e0e0',
        cursor: '#7c3aed',
        selectionBackground: 'rgba(124, 58, 237, 0.3)',
        black: '#1a1a2e',
        red: '#ef4444',
        green: '#22c55e',
        yellow: '#eab308',
        blue: '#3b82f6',
        magenta: '#7c3aed',
        cyan: '#06b6d4',
        white: '#e0e0e0',
        brightBlack: '#6b7280',
        brightRed: '#f87171',
        brightGreen: '#4ade80',
        brightYellow: '#facc15',
        brightBlue: '#60a5fa',
        brightMagenta: '#a78bfa',
        brightCyan: '#22d3ee',
        brightWhite: '#f3f4f6',
      },
    });

    this.fitAddon = new window.FitAddon.FitAddon();
    this.term.loadAddon(this.fitAddon);

    try {
      const webLinksAddon = new window.WebLinksAddon.WebLinksAddon();
      this.term.loadAddon(webLinksAddon);
    } catch (e) {
      // WebLinksAddon may not be available
    }

    this.term.open(container);
    this.fitAddon.fit();

    // Connect WebSocket to PTY
    this.connectPTY(sessionId);

    // Handle resize
    this._resizeHandler = () => {
      if (this.fitAddon) this.fitAddon.fit();
    };
    window.addEventListener('resize', this._resizeHandler);

    // Send keystrokes to PTY
    this.term.onData((data) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(data);
      }
    });
  },

  connectPTY(sessionId) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/terminal/${sessionId}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.term.writeln('\x1b[2m--- Connected to session ---\x1b[0m');
    };

    this.ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'error') {
            this.term.writeln(`\x1b[31mError: ${msg.message}\x1b[0m`);
            this.term.writeln('\x1b[2m--- No active terminal for this session ---\x1b[0m');
            this.term.writeln('\x1b[2mThis session may have been started outside the Command Center.\x1b[0m');
            return;
          }
          if (msg.type === 'pong') return;
        } catch {
          // Not JSON, treat as terminal data
          this.term.write(event.data);
        }
      } else if (event.data instanceof Blob) {
        event.data.arrayBuffer().then(buf => {
          this.term.write(new Uint8Array(buf));
        });
      }
    };

    this.ws.onclose = () => {
      this.term.writeln('\x1b[2m--- Disconnected ---\x1b[0m');
    };

    this.ws.onerror = () => {
      this.term.writeln('\x1b[31mWebSocket connection error\x1b[0m');
    };
  },

  close() {
    const overlay = document.getElementById('terminal-overlay');
    overlay.style.display = 'none';

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    if (this.term) {
      this.term.dispose();
      this.term = null;
    }
    if (this._resizeHandler) {
      window.removeEventListener('resize', this._resizeHandler);
    }
    this.currentSessionId = null;
  },
};

// Wire up back button and popout
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('terminal-back').addEventListener('click', () => {
    Terminal.close();
  });

  document.getElementById('terminal-popout').addEventListener('click', () => {
    if (Terminal.currentSessionId) {
      // Open a simple standalone terminal page
      const url = `/terminal.html?session=${Terminal.currentSessionId}`;
      window.open(url, '_blank', 'width=1000,height=600');
    }
  });
});
