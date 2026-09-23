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
  let isComposing = false;
  let currentFontSize = 13;
  let reconnectTimer = null;
  let toastTimer = null;
  let heartbeatTimer = null;
  let pongTimeoutTimer = null;
  let reconnectAttempts = 0;
  let isManuallyClosed = false;
  let isReconnecting = false;
  let lastTouchTime = 0;
  let lastSentCols = -1;
  let lastSentRows = -1;
  let currentSessionId = '';
  const MAX_RECONNECT_ATTEMPTS = 15;

  try {
    currentSessionId = sessionStorage.getItem('remote_term_session_id') || '';
  } catch (_) {}

  // Lightweight haptic vibration feedback for mobile touches
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
        // Ping control packet
        ws.send(JSON.stringify({ type: 'ping' }));
        clearTimeout(pongTimeoutTimer);
        // Half-open connection detection: if pong does not arrive within 8s, trigger reconnect
        pongTimeoutTimer = setTimeout(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            showToast('Heartbeat timeout, reconnecting...', 'info');
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

  function scheduleReconnect() {
    if (isManuallyClosed || isReconnecting) return;
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      setStatus(false, 'disconnected (max retries)');
      showToast('Terminal connection dropped. Tap refresh button to reconnect.', 'err');
      return;
    }
    isReconnecting = true;
    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(1.3, reconnectAttempts), 5000);
    setStatus(false, `reconnecting (${reconnectAttempts})...`);
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      isReconnecting = false;
      connectWebSocket();
    }, delay);
  }

  // Sanitizes mobile smart punctuation (curly quotes, em-dashes, non-breaking spaces) on soft keyboard
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

    // Initial measurement
    if (fitAddon) {
      fitAddon.fit();
    }

    // Hardening mobile helper textarea against unwanted autocorrect and tracking composition
    if (term.textarea) {
      term.textarea.setAttribute('autocapitalize', 'none');
      term.textarea.setAttribute('autocorrect', 'off');
      term.textarea.setAttribute('autocomplete', 'off');
      term.textarea.setAttribute('spellcheck', 'false');
      term.textarea.removeAttribute('inputmode');
      term.textarea.setAttribute('enterkeyhint', 'go');

      term.textarea.addEventListener('compositionstart', () => {
        isComposing = true;
      });

      term.textarea.addEventListener('compositionend', () => {
        isComposing = false;
        setTimeout(() => {
          if (term.textarea && !isComposing) term.textarea.value = '';
        }, 0);
      });

      // Intercept beforeinput to handle Backspace, Enter, and Gboard word replacements cleanly
      term.textarea.addEventListener('beforeinput', (e) => {
        if (e.inputType === 'deleteContentBackward') {
          sendInput('\x7f');
          e.preventDefault();
          term.textarea.value = '';
        } else if (e.inputType === 'insertLineBreak') {
          sendInput('\r');
          e.preventDefault();
          term.textarea.value = '';
        } else if (e.inputType === 'insertReplacementText') {
          // Autocorrect or Gboard suggestion replacement
          const newText = (e.dataTransfer ? e.dataTransfer.getData('text/plain') : e.data) || '';
          if (newText) {
            const prevLen = term.textarea.value.length;
            const backspaces = '\x7f'.repeat(Math.max(1, prevLen));
            sendInput(backspaces + newText);
            e.preventDefault();
            term.textarea.value = '';
          }
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

    // Process keystrokes typed by user
    term.onData((data) => {
      // Filter out synthetic mouse tracking events triggered by mobile screen taps
      if (data.startsWith('\x1b[<') || data.startsWith('\x1b[M')) {
        if (Date.now() - lastTouchTime < 650) {
          return;
        }
        // Desktop mouse event: allow through to TUI app!
      }

      // Sanitize single interactive character typing
      if (data.length === 1) {
        data = sanitizeTerminalInput(data);
      }

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
        if (term.textarea && !isComposing) term.textarea.value = '';
        return;
      }

      if (isAltActive && data.length >= 1) {
        setAltActive(false);
        sendInput('\x1b' + data);
        if (term.textarea && !isComposing) term.textarea.value = '';
        return;
      }

      sendInput(data);
      if (term.textarea && !isComposing) {
        term.textarea.value = '';
      }
    });

    // Mobile tap-to-focus and long-press (550ms) to open native Select Mode overlay
    let touchStartTime = 0;
    let touchStartX = 0;
    let touchStartY = 0;
    let longPressTimer = null;

    container.addEventListener('touchstart', (e) => {
      lastTouchTime = Date.now();
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      touchStartX = t.clientX;
      touchStartY = t.clientY;
      touchStartTime = Date.now();

      clearTimeout(longPressTimer);
      longPressTimer = setTimeout(() => {
        longPressTimer = null;
        triggerHaptic(25);
        openSelectMode();
      }, 550);
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
      if (!longPressTimer) return;
      const t = e.touches[0];
      if (t && Math.hypot(t.clientX - touchStartX, t.clientY - touchStartY) > 10) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
    }, { passive: true });

    container.addEventListener('touchend', (e) => {
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
    });

    container.addEventListener('touchcancel', () => {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    });

    // Alt-screen touch scrolling: In htop, vim, nano, translate vertical swipes into arrow keys
    let altScrollStartY = 0;
    let altScrollLastY = 0;
    container.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        altScrollStartY = e.touches[0].clientY;
        altScrollLastY = altScrollStartY;
      }
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
      if (!term || e.touches.length !== 1) return;
      if (term.buffer && term.buffer.active && term.buffer.active.type === 'alternate') {
        const y = e.touches[0].clientY;
        const dy = altScrollLastY - y;
        if (Math.abs(dy) >= 18) {
          if (dy > 0) {
            sendInput(getArrowKey('B')); // Down
          } else {
            sendInput(getArrowKey('A')); // Up
          }
          altScrollLastY = y;
        }
      }
    }, { passive: true });

    // Floating Scroll-to-Bottom button setup
    const scrollBottomBtn = document.getElementById('scroll-bottom-btn');
    if (scrollBottomBtn) {
      scrollBottomBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        triggerHaptic(12);
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
    setStatus(false, reconnectAttempts > 0 ? `reconnecting...` : 'connecting...');

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
      setStatus(false, 'connection error');
      showToast('WebSocket error: ' + err.message, 'err');
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      const wasReconnecting = reconnectAttempts > 0;
      reconnectAttempts = 0;
      isManuallyClosed = false;
      isReconnecting = false;
      startHeartbeat();
      setStatus(true, 'bash • remote');
      showToast(wasReconnecting ? 'Terminal reconnected' : 'Connected to host terminal', 'ok');
      if (fitAddon && term) {
        fitAddon.fit();
        sendResize(term.cols, term.rows);
      }
      term.focus();
    };

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary PTY terminal output: feeds xterm streaming UTF-8 decoder directly
        if (term) {
          term.write(new Uint8Array(event.data));
        }
        return;
      }

      if (typeof event.data === 'string') {
        // Text control frames (JSON)
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'pong') {
            handlePong();
            return;
          }
          if (msg.type === 'session') {
            currentSessionId = msg.id;
            try {
              sessionStorage.setItem('remote_term_session_id', msg.id);
            } catch (_) {}
            return;
          }
        } catch (_) {}
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

  function setAltActive(active) {
    isAltActive = active;
    const btn = document.getElementById('alt-toggle-btn');
    if (btn) {
      if (isAltActive) {
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
      // Prevent losing focus on term.textarea when tapping accessory buttons
      const el = e.target.closest(selector);
      if (el) {
        e.preventDefault();
      }
      isDrag = false;
      startX = e.clientX;
      startY = e.clientY;
      activeEl = el;
    });

    container.addEventListener('pointermove', (e) => {
      if (!isDrag && Math.hypot(e.clientX - startX, e.clientY - startY) > 8) {
        isDrag = true;
      }
    });

    container.addEventListener('pointerup', (e) => {
      if (isDrag || !activeEl) return;
      const el = e.target.closest(selector);
      if (!el || el !== activeEl) return;
      e.preventDefault();
      triggerHaptic(10);
      onTrigger(el);
      activeEl = null;
    });

    container.addEventListener('pointercancel', () => {
      isDrag = false;
      activeEl = null;
    });

    container.addEventListener('click', (e) => {
      const el = e.target.closest(selector);
      if (el) e.preventDefault();
    });
  }

  async function handlePaste() {
    triggerHaptic(12);
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const text = await navigator.clipboard.readText();
        if (text) {
          if (term) {
            term.paste(text); // Respects bracketed paste mode!
          } else {
            sendInput(text);
          }
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

  // Opens native mobile text selection overlay snapshotting active terminal buffer
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

    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(textToCopy);
        copied = true;
      } catch (_) {}
    }
    if (!copied) {
      try {
        const ta = document.createElement('textarea');
        ta.value = textToCopy;
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
      triggerHaptic(15);
      showToast(`Copied ${textToCopy.length} characters`, 'ok');
    } else {
      showToast('Copy failed or permission denied', 'err');
    }
  }

  async function handleCopy() {
    triggerHaptic(12);
    // If xterm has an active mouse/desktop selection, copy it directly
    if (term && term.hasSelection()) {
      const selection = term.getSelection();
      if (selection) {
        let copied = false;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          try {
            await navigator.clipboard.writeText(selection);
            copied = true;
          } catch (_) {}
        }
        if (!copied) {
          try {
            const ta = document.createElement('textarea');
            ta.value = selection;
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
          showToast(`Copied ${selection.length} chars to clipboard`, 'ok');
          if (term) term.focus();
          return;
        }
      }
    }

    // On mobile touch devices without selection, open native Select Mode overlay
    openSelectMode();
  }

  function toggleSoftKeyboard() {
    if (!term || !term.textarea) return;
    triggerHaptic(12);
    const isFocused = (document.activeElement === term.textarea);
    if (isFocused) {
      term.textarea.blur();
      showToast('Keyboard dismissed', 'info');
    } else {
      term.textarea.focus({ preventScroll: true });
      showToast('Keyboard focused', 'ok');
    }
  }

  function toggleComposer(forceState) {
    const drawer = document.getElementById('composer-drawer');
    const input = document.getElementById('composer-input');
    if (!drawer) return;
    const shouldOpen = (forceState !== undefined) ? forceState : drawer.hidden;
    drawer.hidden = !shouldOpen;
    triggerHaptic(10);
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
    triggerHaptic(15);
    if (term) {
      term.paste(rawVal + '\r');
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

      if (btn.id === 'ctrl-toggle-btn') {
        setCtrlActive(!isCtrlActive);
        return;
      }

      if (btn.id === 'alt-toggle-btn') {
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

      if (raw) {
        sendInput(raw);
        if (term) term.focus();
        return;
      }

      switch (key) {
        case 'escape':
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
          sendInput(getArrowKey('A'));
          break;
        case 'arrow-down':
          sendInput(getArrowKey('B'));
          break;
        case 'arrow-left':
          sendInput(getArrowKey('D'));
          break;
        case 'arrow-right':
          sendInput(getArrowKey('C'));
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
        triggerHaptic(12);
        // Prefix with Ctrl+U (\x15) to clear any half-typed text on the prompt first
        sendInput('\x15' + cmd);
        if (term) term.focus();
      }
    });
  }

  function initTools() {
    const reconnectBtn = document.getElementById('reconnect-btn');
    if (reconnectBtn) {
      reconnectBtn.addEventListener('click', () => {
        triggerHaptic(12);
        reconnectAttempts = 0;
        isManuallyClosed = false;
        clearTimeout(reconnectTimer);
        stopHeartbeat();
        showToast('Reconnecting terminal...', 'ok');
        connectWebSocket();
      });
    }

    const kbdToggleBtn = document.getElementById('kbd-toggle-btn');
    if (kbdToggleBtn) {
      kbdToggleBtn.addEventListener('click', () => {
        toggleSoftKeyboard();
      });
    }

    const composerToggleBtn = document.getElementById('composer-toggle-btn');
    if (composerToggleBtn) {
      composerToggleBtn.addEventListener('click', () => {
        toggleComposer();
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

    // Native Select Overlay Actions
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
        triggerHaptic(10);
        closeSelectMode();
      });
    }

    const fontDec = document.getElementById('font-decrease-btn');
    if (fontDec) {
      fontDec.addEventListener('click', () => {
        triggerHaptic(10);
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
        triggerHaptic(10);
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

    // Debounced window and visualViewport resize listener with iOS visualViewport height anchoring
    let resizeDebounceTimer = null;
    const scaffold = document.querySelector('.terminal-scaffold');

    const handleResize = () => {
      if (window.visualViewport && scaffold) {
        scaffold.style.height = `${window.visualViewport.height}px`;
      }
      clearTimeout(resizeDebounceTimer);
      resizeDebounceTimer = setTimeout(() => {
        if (fitAddon && term) {
          fitAddon.fit();
          sendResize(term.cols, term.rows);
        }
      }, 150);
    };

    window.addEventListener('resize', handleResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleResize);
      handleResize();
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
