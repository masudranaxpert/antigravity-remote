/**
 * Antigravity Remote - Interactive Mobile Terminal Controller
 * Manages xterm.js instance, RFC 6455 WebSocket bridge, keep-alive heartbeat,
 * auto-reconnect, and mobile touch accessories.
 */
(function () {
  'use strict';

  let term = null;
  let fitAddon = null;
  let ws = null;
  let isCtrlActive = false;
  let currentFontSize = 13;
  let reconnectTimer = null;
  let toastTimer = null;
  let heartbeatTimer = null;
  let reconnectAttempts = 0;
  let isManuallyClosed = false;
  const MAX_RECONNECT_ATTEMPTS = 15;

  // Visual Studio Dark Palette for xterm.js
  const STUDIO_THEME = {
    background: '#0d0f12',
    foreground: '#f3f4f6',
    cursor: '#38bdf8',
    cursorAccent: '#0d0f12',
    selectionBackground: 'rgba(56, 189, 248, 0.35)',
    black: '#1f242d',
    red: '#f43f5e',
    green: '#10b981',
    yellow: '#f59e0b',
    blue: '#38bdf8',
    magenta: '#c084fc',
    cyan: '#22d3ee',
    white: '#e5e7eb',
    brightBlack: '#4b5563',
    brightRed: '#fb7185',
    brightGreen: '#34d399',
    brightYellow: '#fbbf24',
    brightBlue: '#60a5fa',
    brightMagenta: '#e879f9',
    brightCyan: '#67e8f9',
    brightWhite: '#ffffff',
  };

  function showToast(msg, type = 'ok') {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toast-msg');
    const toastIcon = document.getElementById('toast-icon');
    if (!toast || !toastMsg) return;

    clearTimeout(toastTimer);
    toastMsg.textContent = msg;
    toast.className = type;
    toast.classList.add('show');

    if (type === 'ok') {
      toastIcon.innerHTML = `
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
      `;
    } else if (type === 'err') {
      toastIcon.innerHTML = `
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="15" y1="9" x2="9" y2="15"></line>
          <line x1="9" y1="9" x2="15" y2="15"></line>
        </svg>
      `;
    } else {
      toastIcon.innerHTML = `
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="16" x2="12" y2="12"></line>
          <line x1="12" y1="8" x2="12.01" y2="8"></line>
        </svg>
      `;
    }

    toastTimer = setTimeout(() => {
      toast.classList.remove('show');
    }, 3800);
  }

  function setStatus(online, text) {
    const dot = document.getElementById('term-status-dot');
    const label = document.getElementById('session-label');
    if (dot) {
      dot.className = online ? 'status-dot active' : 'status-dot disconnected';
    }
    if (label && text) {
      label.textContent = text;
    }
  }

  function sendInput(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(data);
    }
  }

  function sendResize(cols, rows) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }));
    }
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 15000); // 15s keep-alive interval prevents Cloudflare tunnel / mobile idle timeouts
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function scheduleReconnect() {
    if (isManuallyClosed) return;
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      setStatus(false, 'disconnected (max retries)');
      showToast('Terminal connection dropped. Tap refresh button to reconnect.', 'err');
      return;
    }
    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(1.4, reconnectAttempts), 6000);
    setStatus(false, `reconnecting (${reconnectAttempts})...`);
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      connectWebSocket();
    }, delay);
  }

  function initTerminal() {
    const container = document.getElementById('terminal-container');
    if (!container) return;

    term = new Terminal({
      theme: STUDIO_THEME,
      fontSize: currentFontSize,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
      cursorBlink: true,
      cursorStyle: 'bar',
      cursorWidth: 2,
      scrollback: 5000,
      tabStopWidth: 4,
      allowTransparency: true,
      smoothScrollDuration: 100,
    });

    if (window.FitAddon && window.FitAddon.FitAddon) {
      fitAddon = new window.FitAddon.FitAddon();
      term.loadAddon(fitAddon);
    }

    term.open(container);

    // Disable SGR and DEC mouse reporting modes to eliminate garbage clicks (e.g. 35;9;3M)
    term.write('\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l');

    if (fitAddon) {
      setTimeout(() => {
        fitAddon.fit();
      }, 50);
    }

    // Process keystrokes typed by user
    term.onData((data) => {
      // Filter out SGR/X10 mouse tracking sequences sent on mobile screen taps (e.g. \x1b[<35;9;3M)
      if (data.startsWith('\x1b[<') || data.startsWith('\x1b[M')) {
        return;
      }

      if (isCtrlActive && data.length === 1) {
        // Apply sticky Ctrl modifier to single character
        const code = data.charCodeAt(0);
        let ctrlChar = data;
        if (code >= 64 && code <= 95) {
          ctrlChar = String.fromCharCode(code - 64);
        } else if (code >= 97 && code <= 122) {
          ctrlChar = String.fromCharCode(code - 96);
        }
        setCtrlActive(false);
        sendInput(ctrlChar);
        return;
      }
      sendInput(data);
    });
  }

  function connectWebSocket() {
    clearTimeout(reconnectTimer);
    setStatus(false, reconnectAttempts > 0 ? `reconnecting...` : 'connecting...');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const cols = term ? term.cols : 80;
    const rows = term ? term.rows : 24;
    const wsUrl = `${protocol}//${location.host}/api/terminal/ws?cols=${cols}&rows=${rows}`;

    try {
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        try { ws.close(); } catch (_) {}
      }
      ws = new WebSocket(wsUrl);
    } catch (err) {
      setStatus(false, 'connection error');
      showToast('WebSocket error: ' + err.message, 'err');
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      reconnectAttempts = 0;
      isManuallyClosed = false;
      startHeartbeat();
      setStatus(true, 'bash • remote');
      showToast(reconnectAttempts > 0 ? 'Terminal reconnected' : 'Connected to host terminal', 'ok');
      if (fitAddon && term) {
        fitAddon.fit();
        sendResize(term.cols, term.rows);
      }
      term.focus();
    };

    ws.onmessage = (event) => {
      if (term) {
        // Discard ping/pong control JSON from output
        if (typeof event.data === 'string' && event.data.includes('"type":"pong"')) {
          return;
        }
        term.write(event.data);
      }
    };

    ws.onclose = () => {
      stopHeartbeat();
      if (!isManuallyClosed) {
        scheduleReconnect();
      } else {
        setStatus(false, 'disconnected');
      }
    };

    ws.onerror = () => {
      stopHeartbeat();
      scheduleReconnect();
    };
  }

  function setCtrlActive(active) {
    isCtrlActive = active;
    const btn = document.getElementById('ctrl-toggle-btn');
    if (btn) {
      if (isCtrlActive) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    }
  }

  function initAccessoryBar() {
    const bar = document.getElementById('accessory-bar');
    if (!bar) return;

    bar.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (!btn) return;

      const key = btn.getAttribute('data-key');
      const raw = btn.getAttribute('data-raw');

      if (btn.id === 'ctrl-toggle-btn') {
        setCtrlActive(!isCtrlActive);
        return;
      }

      if (raw) {
        sendInput(raw);
        if (term) term.focus();
        return;
      }

      switch (key) {
        case 'escape':
          sendInput('\x1b');
          break;
        case 'tab':
          sendInput('\t');
          break;
        case 'ctrl-c':
          sendInput('\x03');
          break;
        case 'ctrl-d':
          sendInput('\x04');
          break;
        case 'ctrl-l':
          sendInput('\x0c');
          break;
        case 'arrow-up':
          sendInput('\x1b[A');
          break;
        case 'arrow-down':
          sendInput('\x1b[B');
          break;
        case 'arrow-left':
          sendInput('\x1b[D');
          break;
        case 'arrow-right':
          sendInput('\x1b[C');
          break;
        default:
          break;
      }

      if (term) term.focus();
    });
  }

  function initQuickChips() {
    const bar = document.getElementById('quick-commands-bar');
    if (!bar) return;

    bar.addEventListener('click', (e) => {
      const chip = e.target.closest('.quick-chip');
      if (!chip) return;

      const cmd = chip.getAttribute('data-cmd');
      if (cmd) {
        sendInput(cmd);
        if (term) term.focus();
      }
    });
  }

  function initTools() {
    const reconnectBtn = document.getElementById('reconnect-btn');
    if (reconnectBtn) {
      reconnectBtn.addEventListener('click', () => {
        reconnectAttempts = 0;
        isManuallyClosed = false;
        clearTimeout(reconnectTimer);
        stopHeartbeat();
        showToast('Reconnecting terminal...', 'ok');
        connectWebSocket();
      });
    }

    const fontDec = document.getElementById('font-decrease-btn');
    if (fontDec) {
      fontDec.addEventListener('click', () => {
        if (currentFontSize > 10 && term) {
          currentFontSize -= 1;
          term.options.fontSize = currentFontSize;
          if (fitAddon) {
            fitAddon.fit();
            sendResize(term.cols, term.rows);
          }
        }
      });
    }

    const fontInc = document.getElementById('font-increase-btn');
    if (fontInc) {
      fontInc.addEventListener('click', () => {
        if (currentFontSize < 22 && term) {
          currentFontSize += 1;
          term.options.fontSize = currentFontSize;
          if (fitAddon) {
            fitAddon.fit();
            sendResize(term.cols, term.rows);
          }
        }
      });
    }

    // Window and visualViewport resize listener
    const handleResize = () => {
      if (fitAddon && term) {
        fitAddon.fit();
        sendResize(term.cols, term.rows);
      }
    };

    window.addEventListener('resize', handleResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleResize);
    }
  }

  // Boot sequence
  document.addEventListener('DOMContentLoaded', () => {
    initTerminal();
    initAccessoryBar();
    initQuickChips();
    initTools();
    connectWebSocket();
  });
})();
