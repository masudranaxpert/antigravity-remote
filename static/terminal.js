/**
 * Antigravity Remote - Interactive Mobile Terminal Controller
 * Manages xterm.js instance, RFC 6455 Binary WebSocket bridge, persistent session
 * reattachment, keep-alive heartbeat, and mobile touch accessories.
 */
(function () {
  'use strict';

  let term = null;
  let fitAddon = null;
  let ws = null;
  let isCtrlActive = false;
  let isAltActive = false;
  let currentFontSize = 13;
  let reconnectTimer = null;
  let toastTimer = null;
  let heartbeatTimer = null;
  let pongTimeoutTimer = null;
  let debouncedFitTimer = null;
  let reconnectAttempts = 0;
  let isManuallyClosed = false;
  let isReconnecting = false;
  let lastTouchTime = 0;
  let lastSentCols = -1;
  let lastSentRows = -1;
  let currentSessionId = '';
  const SESSION_KEY = 'remote_term_session_id';
  const MAX_RECONNECT_ATTEMPTS = 15;

  try {
    currentSessionId = sessionStorage.getItem(SESSION_KEY) || '';
    if (!currentSessionId) {
      currentSessionId = 'sess_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
      sessionStorage.setItem(SESSION_KEY, currentSessionId);
    }
  } catch (_) {}

  // Lightweight haptic vibration feedback for modifier key toggles
  function triggerHaptic(duration = 12) {
    if (typeof navigator !== 'undefined' && navigator.vibrate) {
      try {
        navigator.vibrate(duration);
      } catch (_) {}
    }
  }

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

  // Sends raw keystrokes / terminal data as RFC 6455 Binary Frame (opcode 2)
  function sendInput(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      if (typeof data === 'string') {
        ws.send(new TextEncoder().encode(data));
      } else {
        ws.send(data);
      }
    }
  }

  // Sends terminal control commands (resize, ping) as RFC 6455 Text Frame (opcode 1)
  function sendResize(cols, rows) {
    if (cols === lastSentCols && rows === lastSentRows) return;
    lastSentCols = cols;
    lastSentRows = rows;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }));
    }
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
        clearTimeout(pongTimeoutTimer);
        pongTimeoutTimer = setTimeout(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            showToast('Connection stalled, reconnecting...', 'info');
            ws.close();
          }
        }, 8000);
      }
    }, 15000);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (pongTimeoutTimer) {
      clearTimeout(pongTimeoutTimer);
      pongTimeoutTimer = null;
    }
  }

  function handlePong() {
    if (pongTimeoutTimer) {
      clearTimeout(pongTimeoutTimer);
      pongTimeoutTimer = null;
    }
  }

  let lastFitWidth = 0;
  let lastFitHeight = 0;

  function debouncedFit(delay = 180) {
    clearTimeout(debouncedFitTimer);
    debouncedFitTimer = setTimeout(() => {
      if (fitAddon && term) {
        const container = document.getElementById('terminal-container');
        if (container) {
          const w = container.clientWidth;
          const h = container.clientHeight;
          if (lastFitWidth > 0 && Math.abs(w - lastFitWidth) < 6 && Math.abs(h - lastFitHeight) < 10) {
            return;
          }
          lastFitWidth = w;
          lastFitHeight = h;
        }
        fitAddon.fit();
        sendResize(term.cols, term.rows);
      }
    }, delay);
  }

  // Sanitizes mobile smart punctuation (curly quotes, em-dashes, non-breaking spaces)
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

  function getArrowKey(directionChar) {
    const isAppMode = Boolean(term && term.modes && term.modes.applicationCursorKeysMode);
    let prefix = isAppMode ? '\x1bO' : '\x1b[';
    if (isCtrlActive) {
      setCtrlActive(false);
      return `\x1b[1;5${directionChar}`;
    }
    if (isAltActive) {
      setAltActive(false);
      return `\x1b[1;3${directionChar}`;
    }
    return prefix + directionChar;
  }

  function initTerminal() {
    const container = document.getElementById('terminal-container');
    if (!container) return;

    term = new Terminal({
      theme: STUDIO_THEME,
      fontSize: currentFontSize,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, Consolas, monospace",
      cursorBlink: true,
      cursorStyle: 'bar',
      cursorWidth: 2,
      scrollback: 5000,
      tabStopWidth: 4,
      allowTransparency: false,
      smoothScrollDuration: 0,
    });

    if (window.FitAddon && window.FitAddon.FitAddon) {
      fitAddon = new window.FitAddon.FitAddon();
      term.loadAddon(fitAddon);
    }

    // Mobile soft keyboards (Gboard/Android/iOS) treat standard textareas as IME composition targets,
    // buffering typed words until the spacebar is pressed. Swapping xterm's internal helper element
    // to input[type=password] disables predictive composition and streams every keystroke immediately.
    const isMobileDevice = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
                           ('ontouchstart' in window) ||
                           (window.matchMedia && window.matchMedia('(pointer: coarse)').matches);

    const origCreateElement = document.createElement;
    if (isMobileDevice) {
      document.createElement = function (tag, ...args) {
        if (typeof tag === 'string' && tag.toLowerCase() === 'textarea') {
          const el = origCreateElement.call(document, 'input', ...args);
          el.type = 'password';
          el.setAttribute('autocomplete', 'new-password');
          el.setAttribute('data-1p-ignore', 'true');
          el.setAttribute('data-lpignore', 'true');
          el.setAttribute('data-bwignore', 'true');
          return el;
        }
        return origCreateElement.call(document, tag, ...args);
      };
    }

    term.open(container);

    if (isMobileDevice) {
      document.createElement = origCreateElement;
    }

    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => {
        if (fitAddon) fitAddon.fit();
      });
    } else if (fitAddon) {
      fitAddon.fit();
    }

    // Configure mobile helper element against unwanted autocorrect
    if (term.textarea) {
      term.textarea.setAttribute('autocapitalize', 'none');
      term.textarea.setAttribute('autocorrect', 'off');
      term.textarea.setAttribute('spellcheck', 'false');
      term.textarea.setAttribute('enterkeyhint', 'go');

      // Intercept empty-buffer backspace & linebreaks on mobile soft keyboards
      term.textarea.addEventListener('beforeinput', (e) => {
        if (e.inputType === 'deleteContentBackward') {
          // When the helper has no text to delete, soft keyboard won't fire keydown.
          // Dispatch \x7f directly to shell so mobile backspace always deletes.
          if (!term.textarea.value) {
            sendInput('\x7f');
            e.preventDefault();
          }
        } else if (e.inputType === 'insertLineBreak') {
          sendInput('\r');
          e.preventDefault();
          term.textarea.value = '';
        }
      });
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

    // Process keystrokes typed by user natively via xterm.js
    term.onData((data) => {
      // Filter out synthetic mouse tracking events triggered by mobile screen taps
      if (data.startsWith('\x1b[<') || data.startsWith('\x1b[M')) {
        if (Date.now() - lastTouchTime < 650) {
          return;
        }
      }

      // Sanitize input universally across all typing paths
      data = sanitizeTerminalInput(data);

      if (isCtrlActive && data.length === 1) {
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

      if (isAltActive && data.length === 1) {
        setAltActive(false);
        sendInput('\x1b' + data);
        return;
      }

      sendInput(data);
    });

    // Unified Mobile Touch Gestures on Terminal Container
    let touchStartTime = 0;
    let touchStartX = 0;
    let touchStartY = 0;
    let touchScrollLastY = 0;
    let longPressTimer = null;

    container.addEventListener('touchstart', (e) => {
      lastTouchTime = Date.now();
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      touchStartX = t.clientX;
      touchStartY = t.clientY;
      touchScrollLastY = touchStartY;
      touchStartTime = Date.now();

      clearTimeout(longPressTimer);
      longPressTimer = setTimeout(() => {
        if (!term || !term.hasSelection()) {
          triggerHaptic(20);
          openSelectMode();
        }
      }, 550);
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
      lastTouchTime = Date.now();
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      if (Math.hypot(t.clientX - touchStartX, t.clientY - touchStartY) > 10) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }

      const dy = touchScrollLastY - t.clientY;
      const rowStep = 18;

      if (Math.abs(dy) >= rowStep) {
        const rows = Math.trunc(dy / rowStep);
        touchScrollLastY += rows * rowStep;

        if (term && term.buffer && term.buffer.active) {
          if (term.buffer.active.type === 'alternate') {
            // Fullscreen TUI mode (opencode, vim, less, htop)
            const hasMouse = Boolean(term.modes && term.modes.mouseTracking && term.modes.mouseTracking !== 'none');
            if (hasMouse) {
              const wheelCode = rows > 0 ? 65 : 64; // 64 = Up, 65 = Down
              for (let i = 0; i < Math.abs(rows); i++) {
                sendInput(`\x1b[<${wheelCode};1;1M`);
              }
            } else {
              // Standard TUI without mouse reporting: scroll via arrow keys
              const arrow = rows > 0 ? getArrowKey('B') : getArrowKey('A');
              for (let i = 0; i < Math.abs(rows); i++) {
                sendInput(arrow);
              }
            }
          } else {
            // Normal buffer: scroll terminal viewport lines
            term.scrollLines(rows);
          }
        }
        if (e.cancelable) {
          e.preventDefault();
        }
      }
    }, { passive: false });

    container.addEventListener('touchend', (e) => {
      lastTouchTime = Date.now();
      clearTimeout(longPressTimer);
      longPressTimer = null;
      if (Date.now() - touchStartTime < 250 && e.changedTouches.length === 1) {
        const end = e.changedTouches[0];
        if (Math.hypot(end.clientX - touchStartX, end.clientY - touchStartY) <= 8) {
          if (term && term.textarea) {
            term.textarea.focus({ preventScroll: true });
          }
        }
      }
    }, { passive: true });

    container.addEventListener('touchcancel', () => {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    });

    // Floating Scroll-to-Bottom button setup
    const scrollBottomBtn = document.getElementById('scroll-bottom-btn');
    if (scrollBottomBtn) {
      scrollBottomBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (term) {
          term.scrollToBottom();
          scrollBottomBtn.hidden = true;
        }
      });

      term.onScroll(() => {
        const buf = term.buffer.active;
        const isAtBottom = buf.viewportY >= buf.baseY;
        scrollBottomBtn.hidden = isAtBottom;
      });
    }
  }

  function connectWebSocket() {
    clearTimeout(reconnectTimer);
    isReconnecting = false;
    setStatus(false, reconnectAttempts > 0 ? 'reconnecting...' : 'connecting...');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const cols = term ? term.cols : 80;
    const rows = term ? term.rows : 24;
    const urlParams = new URLSearchParams(window.location.search);
    const tokenParam = urlParams.get('token');

    let wsUrl = `${protocol}//${location.host}/api/terminal/ws?cols=${cols}&rows=${rows}`;
    if (currentSessionId) {
      wsUrl += `&session_id=${encodeURIComponent(currentSessionId)}`;
    }
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
      ws.binaryType = 'arraybuffer'; // RFC 6455 Binary Frame handling
    } catch (err) {
      setStatus(false, 'error');
      showToast('WebSocket error: ' + err.message, 'err');
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      reconnectAttempts = 0;
      isManuallyClosed = false;
      setStatus(true, 'bash');
      lastSentCols = -1;
      lastSentRows = -1;
      if (term) {
        sendResize(term.cols, term.rows);
      }
      startHeartbeat();
    };

    function handleControlMessage(data) {
      try {
        const msg = typeof data === 'string' ? JSON.parse(data) : data;
        if (!msg || typeof msg !== 'object') return false;

        if (msg.type === 'pong') {
          handlePong();
          return true;
        }

        if (msg.type === 'session') {
          currentSessionId = msg.id;
          try {
            sessionStorage.setItem(SESSION_KEY, msg.id);
          } catch (_) {}
          if (msg.reconnected && term) {
            term.reset();
            // Nudge resize to trigger SIGWINCH in TUI apps
            setTimeout(() => {
              sendResize(term.cols - 1, term.rows);
              setTimeout(() => sendResize(term.cols, term.rows), 60);
            }, 80);
          }
          return true;
        }

        return Boolean(msg.type);
      } catch (_) {
        return false;
      }
    }

    ws.onmessage = (event) => {
      // 1. ArrayBuffer payload (PTY binary output or tunnel-converted control frame)
      if (event.data instanceof ArrayBuffer) {
        const bytes = new Uint8Array(event.data);
        // Intercept control JSON frame if WebKit or proxy delivered it as binary
        if (bytes.length > 0 && bytes.length < 1024 && bytes[0] === 0x7B /* '{' */) {
          try {
            const text = new TextDecoder('utf-8').decode(bytes);
            if (handleControlMessage(text)) {
              return;
            }
          } catch (_) {}
        }
        // Stream raw PTY binary output into xterm's streaming UTF-8 decoder
        if (term) {
          term.write(bytes);
        }
        return;
      }

      // 2. Text payload (Control JSON commands)
      if (typeof event.data === 'string') {
        handleControlMessage(event.data);
        return;
      }
    };

    ws.onclose = (event) => {
      stopHeartbeat();
      if (event && event.code === 4000) {
        // Detached because attached on another device
        try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
        setStatus(false, 'detached');
        showToast('Session opened on another device/tab', 'info');
        return;
      }
      if (event && event.code === 4001) {
        // Shell process terminated cleanly
        try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
        setStatus(false, 'finished');
        showToast('Shell process exited', 'info');
        return;
      }
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

  function scheduleReconnect() {
    if (isManuallyClosed || isReconnecting) return;
    isReconnecting = true;
    reconnectAttempts++;

    if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
      setStatus(false, 'disconnected');
      showToast('Connection lost. Please tap Reconnect.', 'err');
      isReconnecting = false;
      return;
    }

    const delay = Math.min(6000, 400 * Math.pow(1.4, reconnectAttempts));
    setStatus(false, `reconnecting...`);

    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      connectWebSocket();
    }, delay);
  }

  function setCtrlActive(active) {
    isCtrlActive = active;
    const btn = document.getElementById('ctrl-toggle-btn');
    if (btn) {
      btn.classList.toggle('active', isCtrlActive);
    }
    triggerHaptic(12);
  }

  function setAltActive(active) {
    isAltActive = active;
    const btn = document.getElementById('alt-toggle-btn');
    if (btn) {
      btn.classList.toggle('active', isAltActive);
    }
    triggerHaptic(12);
  }

  function changeFontSize(delta) {
    currentFontSize = Math.min(20, Math.max(10, currentFontSize + delta));
    if (term) {
      term.options.fontSize = currentFontSize;
      if (fitAddon) fitAddon.fit();
      sendResize(term.cols, term.rows);
    }
  }

  function attachTapHandler(container, selector, onTrigger) {
    let startX = 0;
    let startY = 0;
    let isDrag = false;
    let activeEl = null;
    let holdTimer = null;
    let repeatInterval = null;
    let hasRepeated = false;

    function clearHold() {
      clearTimeout(holdTimer);
      clearInterval(repeatInterval);
      holdTimer = null;
      repeatInterval = null;
      if (activeEl) {
        activeEl.classList.remove('holding');
      }
    }

    function isRepeatable(el) {
      if (!el) return false;
      const key = el.getAttribute('data-key');
      return key === 'backspace' || key === 'arrow-left' || key === 'arrow-right' || key === 'arrow-up' || key === 'arrow-down';
    }

    container.addEventListener('pointerdown', (e) => {
      const el = e.target.closest(selector);
      if (el) {
        e.preventDefault();
      }
      clearHold();
      isDrag = false;
      hasRepeated = false;
      startX = e.clientX;
      startY = e.clientY;
      activeEl = el;

      if (isRepeatable(el)) {
        holdTimer = setTimeout(() => {
          if (!isDrag && activeEl === el) {
            hasRepeated = true;
            el.classList.add('holding');
            triggerHaptic(12);
            onTrigger(el);
            repeatInterval = setInterval(() => {
              if (activeEl === el) {
                triggerHaptic(6);
                onTrigger(el);
              } else {
                clearHold();
              }
            }, 55);
          }
        }, 280);
      }
    });

    container.addEventListener('pointermove', (e) => {
      if (!isDrag && Math.hypot(e.clientX - startX, e.clientY - startY) > 8) {
        isDrag = true;
        clearHold();
      }
    });

    container.addEventListener('pointerup', (e) => {
      const el = e.target.closest(selector);
      const wasHolding = hasRepeated;
      clearHold();
      if (isDrag || !activeEl) return;
      if (!el || el !== activeEl) return;
      e.preventDefault();
      if (!wasHolding) {
        onTrigger(el);
      }
      activeEl = null;
    });

    container.addEventListener('pointercancel', () => {
      clearHold();
      isDrag = false;
      activeEl = null;
    });

    container.addEventListener('pointerleave', () => {
      clearHold();
    });

    container.addEventListener('click', (e) => {
      const el = e.target.closest(selector);
      if (el) e.preventDefault();
    });
  }

  async function copyTextToClipboard(text, successMsg = 'Copied to clipboard') {
    if (!text) return false;
    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch (_) {}
    }
    if (!copied) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        copied = true;
      } catch (_) {}
    }
    if (copied) {
      triggerHaptic(12);
      showToast(successMsg, 'ok');
    } else {
      showToast('Copy failed or permission denied', 'err');
    }
    return copied;
  }

  async function handlePaste() {
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const text = await navigator.clipboard.readText();
        if (text) {
          if (term) term.paste(text);
          else sendInput(text);
          showToast('Pasted from clipboard', 'ok');
        } else {
          showToast('Clipboard is empty', 'info');
        }
      } else {
        const manual = prompt('Paste text to send to terminal:');
        if (manual) {
          if (term) term.paste(manual);
          else sendInput(manual);
        }
      }
    } catch (_) {
      const manual = prompt('Paste text to send to terminal:');
      if (manual) {
        if (term) term.paste(manual);
        else sendInput(manual);
      }
    }
    if (term) term.focus();
  }

  function openSelectMode() {
    if (!term) return;
    const overlay = document.getElementById('select-overlay');
    const pre = document.getElementById('select-overlay-pre');
    if (!overlay || !pre) return;

    const buf = term.buffer.active;
    const lines = [];
    for (let i = 0; i < buf.length; i++) {
      const line = buf.getLine(i);
      if (!line) continue;
      const text = line.translateToString(true);
      if (line.isWrapped && lines.length) {
        lines[lines.length - 1] += text;
      } else {
        lines.push(text);
      }
    }
    while (lines.length && !lines[lines.length - 1].trim()) {
      lines.pop();
    }

    pre.textContent = lines.join('\n');
    overlay.hidden = false;
    const body = document.getElementById('select-overlay-body');
    if (body) {
      body.scrollTop = body.scrollHeight;
    }
  }

  function closeSelectMode() {
    const overlay = document.getElementById('select-overlay');
    if (overlay) overlay.hidden = true;
    if (term && term.textarea) {
      term.textarea.focus({ preventScroll: true });
    }
  }

  async function copySelectModeContent(allOnly = false) {
    const pre = document.getElementById('select-overlay-pre');
    if (!pre) return;
    const domSelection = window.getSelection() ? window.getSelection().toString() : '';
    const textToCopy = (!allOnly && domSelection) ? domSelection : (pre.textContent || '');
    if (!textToCopy) {
      showToast('No text available to copy', 'info');
      return;
    }
    await copyTextToClipboard(textToCopy, `Copied ${textToCopy.length} characters`);
  }

  async function handleCopy() {
    if (term && term.hasSelection()) {
      const selection = term.getSelection();
      if (selection) {
        await copyTextToClipboard(selection, `Copied ${selection.length} chars`);
        if (term) term.focus();
        return;
      }
    }
    openSelectMode();
  }

  function toggleSoftKeyboard() {
    if (!term || !term.textarea) return;
    const isFocused = (document.activeElement === term.textarea);
    if (isFocused) {
      term.textarea.blur();
    } else {
      term.textarea.focus({ preventScroll: true });
    }
  }

  function toggleComposer(forceState) {
    const drawer = document.getElementById('composer-drawer');
    const input = document.getElementById('composer-input');
    if (!drawer) return;
    const shouldOpen = (forceState !== undefined) ? forceState : drawer.hidden;
    drawer.hidden = !shouldOpen;
    if (shouldOpen && input) {
      setTimeout(() => input.focus(), 80);
    } else if (!shouldOpen && term && term.textarea) {
      term.textarea.focus({ preventScroll: true });
    }
  }

  function sendComposerCommand() {
    const input = document.getElementById('composer-input');
    if (!input) return;
    const rawVal = input.value;
    if (!rawVal) return;
    if (term) {
      term.paste(rawVal);
      sendInput('\r');
    } else {
      sendInput(rawVal + '\r');
    }
    input.value = '';
    toggleComposer(false);
  }

  function initAccessoryBar() {
    const bar = document.getElementById('accessory-bar');
    if (!bar) return;

    attachTapHandler(bar, 'button', (btn) => {
      const key = btn.getAttribute('data-key');
      const raw = btn.getAttribute('data-raw');
      const action = btn.getAttribute('data-action');
      const id = btn.id;

      if (id === 'ctrl-toggle-btn') {
        setCtrlActive(!isCtrlActive);
        return;
      }
      if (id === 'alt-toggle-btn') {
        setAltActive(!isAltActive);
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
      if (id === 'composer-acc-btn') {
        toggleComposer();
        return;
      }

      if (raw) {
        sendInput(raw);
        if (term && term.textarea) term.textarea.focus({ preventScroll: true });
        return;
      }

      if (key) {
        switch (key) {
          case 'enter':
            sendInput('\r');
            break;
          case 'escape':
            sendInput('\x1b');
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
          case 'arrow-up':
            sendInput(getArrowKey('A'));
            break;
          case 'arrow-down':
            sendInput(getArrowKey('B'));
            break;
          case 'arrow-right':
            sendInput(getArrowKey('C'));
            break;
          case 'arrow-left':
            sendInput(getArrowKey('D'));
            break;
        }
        if (term && term.textarea) term.textarea.focus({ preventScroll: true });
      }
    });
  }

  function initHeaderControls() {
    const kbdBtn = document.getElementById('kbd-toggle-btn');
    if (kbdBtn) {
      kbdBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        toggleSoftKeyboard();
      });
    }

    const menuBtn = document.getElementById('header-menu-btn');
    const dropdown = document.getElementById('header-dropdown');
    if (menuBtn && dropdown) {
      menuBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        dropdown.hidden = !dropdown.hidden;
      });

      document.addEventListener('pointerdown', (e) => {
        if (!dropdown.hidden && !dropdown.contains(e.target) && e.target !== menuBtn) {
          dropdown.hidden = true;
        }
      });
    }

    const composerToggleBtn = document.getElementById('composer-toggle-btn');
    if (composerToggleBtn) {
      composerToggleBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        if (dropdown) dropdown.hidden = true;
        toggleComposer();
      });
    }

    const fontDecBtn = document.getElementById('font-decrease-btn');
    if (fontDecBtn) {
      fontDecBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        changeFontSize(-1);
      });
    }

    const fontIncBtn = document.getElementById('font-increase-btn');
    if (fontIncBtn) {
      fontIncBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        changeFontSize(1);
      });
    }

    const reconnectBtn = document.getElementById('reconnect-btn');
    if (reconnectBtn) {
      reconnectBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        if (dropdown) dropdown.hidden = true;
        reconnectAttempts = 0;
        isManuallyClosed = false;
        clearTimeout(reconnectTimer);
        stopHeartbeat();
        showToast('Reconnecting terminal...', 'info');
        connectWebSocket();
      });
    }

    const composerCloseBtn = document.getElementById('composer-close-btn');
    if (composerCloseBtn) {
      composerCloseBtn.addEventListener('click', () => {
        toggleComposer(false);
      });
    }

    const composerSendBtn = document.getElementById('composer-send-btn');
    if (composerSendBtn) {
      composerSendBtn.addEventListener('click', () => {
        sendComposerCommand();
      });
    }

    const composerInput = document.getElementById('composer-input');
    if (composerInput) {
      composerInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendComposerCommand();
        }
      });
    }

    const selectCopyBtn = document.getElementById('select-copy-btn');
    if (selectCopyBtn) {
      selectCopyBtn.addEventListener('click', () => {
        copySelectModeContent(false);
      });
    }

    const selectCopyAllBtn = document.getElementById('select-copy-all-btn');
    if (selectCopyAllBtn) {
      selectCopyAllBtn.addEventListener('click', () => {
        copySelectModeContent(true);
      });
    }

    const selectCloseBtn = document.getElementById('select-close-btn');
    if (selectCloseBtn) {
      selectCloseBtn.addEventListener('click', () => {
        closeSelectMode();
      });
    }
  }

  // Rapid background wake-up handler
  function handleWakeup() {
    if (document.visibilityState === 'visible') {
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reconnectAttempts = 0;
        clearTimeout(reconnectTimer);
        stopHeartbeat();
        connectWebSocket();
        return;
      }
      try {
        ws.send(JSON.stringify({ type: 'ping' }));
        clearTimeout(pongTimeoutTimer);
        pongTimeoutTimer = setTimeout(() => {
          if (ws) {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onclose = null;
            ws.onerror = null;
            try { ws.close(); } catch (_) {}
            ws = null;
          }
          connectWebSocket();
        }, 2500);
      } catch (_) {
        connectWebSocket();
      }
    }
  }

  document.addEventListener('visibilitychange', handleWakeup);
  window.addEventListener('pageshow', handleWakeup);
  window.addEventListener('online', handleWakeup);

  // Viewport tracking for soft keyboard state and safe-area adjustments
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => {
      const isKeyboardOpen = window.visualViewport.height < window.innerHeight - 80;
      if (isKeyboardOpen) {
        document.body.classList.add('keyboard-open');
      } else {
        document.body.classList.remove('keyboard-open');
      }
      debouncedFit();
    });
  } else {
    window.addEventListener('resize', () => debouncedFit());
  }

  window.addEventListener('DOMContentLoaded', () => {
    initTerminal();
    initAccessoryBar();
    initHeaderControls();
    connectWebSocket();
  });
})();
