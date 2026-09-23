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

  // Sanitizes mobile smart punctuation (curly quotes, em-dashes, non-breaking spaces) to raw ASCII
  function sanitizeTerminalInput(data) {
    if (!data) return data;
    return data
      .replace(/[\u2018\u2019]/g, "'") // Smart single quotes ‘ ’ -> '
      .replace(/[\u201c\u201d]/g, '"') // Smart double quotes “ ” -> "
      .replace(/\u2014/g, '--')        // Em-dash — -> --
      .replace(/\u2013/g, '-')         // En-dash – -> -
      .replace(/\u2026/g, '...')       // Ellipsis … -> ...
      .replace(/\u00a0/g, ' ');        // Non-breaking space \u00a0 -> space
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

    // Hardening the hidden mobile textarea against Gboard predictive composition and autocorrect
    if (term.textarea) {
      term.textarea.setAttribute('autocapitalize', 'none');
      term.textarea.setAttribute('autocorrect', 'off');
      term.textarea.setAttribute('autocomplete', 'off');
      term.textarea.setAttribute('spellcheck', 'false');
      // inputmode="search" neutralizes word-prediction dictionaries on mobile keyboards (Gboard/Samsung/iOS)
      term.textarea.setAttribute('inputmode', 'search');
      term.textarea.setAttribute('enterkeyhint', 'go');

      // Intercept mobile Backspace deleteContentBackward event to guarantee deletion in shell
      term.textarea.addEventListener('beforeinput', (e) => {
        if (e.inputType === 'deleteContentBackward') {
          sendInput('\x7f');
          e.preventDefault();
        }
      });
    }

    // Disable SGR and DEC mouse reporting modes to eliminate garbage clicks (e.g. 35;9;3M)
    term.write('\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l');

    if (fitAddon) {
      setTimeout(() => {
        fitAddon.fit();
      }, 50);
    }

    // Intercept physical Escape key so browser does not swallow it
    term.attachCustomKeyEventHandler((e) => {
      if (e.key === 'Escape') {
        if (e.type === 'keydown') {
          sendInput('\x1b');
        }
        return false;
      }
      return true;
    });

    // Process keystrokes typed by user
    term.onData((data) => {
      // Filter out SGR/X10 mouse tracking sequences sent on mobile screen taps (e.g. \x1b[<35;9;3M)
      if (data.startsWith('\x1b[<') || data.startsWith('\x1b[M')) {
        return;
      }

      data = sanitizeTerminalInput(data);

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
    const urlParams = new URLSearchParams(window.location.search);
    const tokenParam = urlParams.get('token');
    let wsUrl = `${protocol}//${location.host}/api/terminal/ws?cols=${cols}&rows=${rows}`;
    if (tokenParam) {
      wsUrl += `&token=${encodeURIComponent(tokenParam)}`;
    }

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

  function attachTapHandler(container, selector, onTrigger) {
    let startX = 0;
    let startY = 0;
    let isDrag = false;
    let activeEl = null;

    container.addEventListener('pointerdown', (e) => {
      // Always reset drag state on any new touch, even outside a button
      isDrag = false;
      startX = e.clientX;
      startY = e.clientY;
      activeEl = e.target.closest(selector);
    });

    container.addEventListener('pointermove', (e) => {
      if (!isDrag && Math.hypot(e.clientX - startX, e.clientY - startY) > 8) {
        isDrag = true;
      }
    });

    container.addEventListener('pointerup', (e) => {
      if (isDrag || !activeEl) return;
      const el = e.target.closest(selector);
      // Require pointerup on same element as pointerdown to avoid swipe-through
      if (!el || el !== activeEl) return;
      e.preventDefault();
      onTrigger(el);
      activeEl = null;
    });

    container.addEventListener('pointercancel', () => {
      isDrag = false;
      activeEl = null;
    });

    // Suppress synthetic click to prevent duplicate trigger
    container.addEventListener('click', (e) => {
      const el = e.target.closest(selector);
      if (el) e.preventDefault();
    });
  }

  async function handlePaste() {
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const text = await navigator.clipboard.readText();
        if (text) {
          sendInput(sanitizeTerminalInput(text));
          showToast('Pasted from clipboard', 'ok');
        } else {
          showToast('Clipboard is empty', 'info');
        }
      } else {
        const manual = prompt('Paste text to send to terminal:');
        if (manual) sendInput(sanitizeTerminalInput(manual));
      }
    } catch (_) {
      const manual = prompt('Paste text to send to terminal:');
      if (manual) sendInput(sanitizeTerminalInput(manual));
    }
    if (term) term.focus();
  }

  async function handleCopy() {
    let text = term ? term.getSelection() : '';
    if (!text && term) {
      try {
        const buf = term.buffer.active;
        const line = buf.getLine(buf.baseY + buf.cursorY);
        if (line) text = line.translateToString(true).trim();
      } catch (_) {}
    }
    if (text) {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          showToast(`Copied ${text.length} chars to clipboard`, 'ok');
        } else {
          showToast('Clipboard write unavailable', 'err');
        }
      } catch (_) {
        showToast('Clipboard permission denied', 'err');
      }
    } else {
      showToast('No text available to copy', 'info');
    }
    if (term) term.focus();
  }

  function initAccessoryBar() {
    const bar = document.getElementById('accessory-bar');
    if (!bar) return;

    attachTapHandler(bar, 'button', (btn) => {
      const key = btn.getAttribute('data-key');
      const raw = btn.getAttribute('data-raw');
      const action = btn.getAttribute('data-action');

      if (btn.id === 'ctrl-toggle-btn') {
        setCtrlActive(!isCtrlActive);
        return;
      }

      if (action === 'paste') {
        handlePaste();
        return;
      }

      if (action === 'copy') {
        handleCopy();
        return;
      }

      if (raw) {
        sendInput(raw);
        if (term) term.focus();
        return;
      }

      switch (key) {
        case 'escape':
          // Single ESC; do NOT call term.focus() here — xterm focus events
          // arrive via PTY and can interrupt the TUI's 20ms escape timeout.
          sendInput('\x1b');
          return;
        case 'enter':
          sendInput('\r');
          break;
        case 'backspace':
          sendInput('\x7f');
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

    attachTapHandler(bar, '.quick-chip', (chip) => {
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

    // Debounced window and visualViewport resize listener (prevents SIGWINCH spam during keyboard animation)
    let resizeDebounceTimer = null;
    const handleResize = () => {
      clearTimeout(resizeDebounceTimer);
      resizeDebounceTimer = setTimeout(() => {
        if (fitAddon && term) {
          fitAddon.fit();
          sendResize(term.cols, term.rows);
          term.scrollToBottom();
        }
      }, 180);
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
