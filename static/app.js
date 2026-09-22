/**
 * Antigravity Mobile Switcher - Frontend Controller
 * Direct filesystem state consumer and credential orchestrator.
 */

(function () {
  'use strict';

  let pendingAccountId = null;
  let pendingAccountEmail = null;
  let toastTimer = null;
  let autoRefreshTimer = null;

  // Clean URL query string without exposing master token in browser address bar
  if (window.location.search.includes('token=')) {
    window.history.replaceState({}, document.title, window.location.pathname);
  }

  // HTML entity sanitization against XSS
  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Safe URL protocol validation
  function sanitizeUrl(url) {
    if (!url) return '#';
    const trimmed = String(url).trim();
    if (/^https?:\/\//i.test(trimmed)) {
      return escapeHtml(trimmed);
    }
    return '#';
  }

  // 12-Hour Time Formatter (hh:mm:ss AM/PM)
  function format12HourTime(date) {
    let hours = date.getHours();
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    hours = hours ? hours : 12;
    const strHours = String(hours).padStart(2, '0');
    return `${strHours}:${minutes}:${seconds} ${ampm}`;
  }

  // Top Floating Toast Notification HUD
  function showToast(msg, type = 'ok') {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toast-msg');
    const toastIcon = document.getElementById('toast-icon');

    if (!toast || !toastMsg || !toastIcon) return;

    if (toastTimer) {
      clearTimeout(toastTimer);
    }

    toast.className = `show toast-${type}`;
    toastMsg.textContent = msg;

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
    }, 4200);
  }

  // Resilient API Fetch Helper
  async function apiRequest(path, options = {}) {
    try {
      const res = await fetch(path, options);
      const text = await res.text();
      try {
        return JSON.parse(text);
      } catch {
        if (!res.ok) {
          return { error: `Host error (${res.status}). Service might be restarting.` };
        }
        return { error: 'Invalid response format from host.' };
      }
    } catch (err) {
      return { error: err.message || 'Host connection failure' };
    }
  }

  // Quota Countdown Formatter
  function formatCountdown(resetTimeStr, pct) {
    if (pct === 0) {
      if (!resetTimeStr) return 'Quota exhausted';
      const diff = new Date(resetTimeStr).getTime() - Date.now();
      if (diff <= 0) return 'Reset ready';
      const hours = Math.floor(diff / (1000 * 60 * 60));
      const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
      return hours > 0 ? `Exhausted • Resets in ${hours}h ${mins}m` : `Exhausted • Resets in ${mins}m`;
    }
    if (!resetTimeStr) return 'Active quota';
    const target = new Date(resetTimeStr).getTime();
    const diff = target - Date.now();
    if (diff <= 0) return 'Reset ready';
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    return hours > 0 ? `Resets in ${hours}h ${mins}m` : `Resets in ${mins}m`;
  }

  // Single Quota Metric Row Renderer (Unnested, clean telemetry)
  function renderQuotaRow(type, quotaData) {
    const isGemini = type === 'gemini';
    const label = isGemini ? 'Gemini' : 'Claude';
    const pct = (quotaData && typeof quotaData.percentage === 'number') ? quotaData.percentage : 0;
    const resetTime = quotaData ? quotaData.reset_time : null;
    const countdown = formatCountdown(resetTime, pct);
    const isExhausted = pct === 0;
    const isLow = pct < 20;

    let barClass = isGemini ? 'gemini' : 'claude';
    if (isLow) barClass = 'low';

    return `
      <div class="quota-item">
        <div class="quota-meta">
          <div class="quota-meta-left">
            <span class="quota-pill ${isGemini ? 'gemini' : 'claude'}">${label}</span>
            <span class="quota-reset ${isExhausted ? 'exhausted' : ''}">${countdown}</span>
          </div>
          <span class="quota-pct">${pct}%</span>
        </div>
        <div class="meter-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="${label} remaining quota">
          <div class="meter-bar ${barClass}" style="transform: scaleX(${Math.min(1, Math.max(0, pct / 100))});"></div>
        </div>
      </div>
    `;
  }

  // Render Dual Quotas Container
  function renderDualQuotas(account) {
    const gemini = (account.quotas && account.quotas.gemini) ||
                   (account.models && account.models.find(m => (m.name || '').toLowerCase().includes('gemini')));
    const claude = (account.quotas && account.quotas.claude) ||
                   (account.models && account.models.find(m => (m.name || '').toLowerCase().includes('claude')));

    return `
      <div class="quota-list">
        ${renderQuotaRow('gemini', gemini)}
        ${renderQuotaRow('claude', claude)}
      </div>
    `;
  }

  // Clipboard copy
  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        showToast('Official Remote link copied to clipboard!', 'ok');
      }).catch(() => {
        fallbackCopy(text);
      });
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      showToast('Official Remote link copied!', 'ok');
    } catch {
      showToast('Copy failed. Please copy link manually.', 'err');
    }
    document.body.removeChild(ta);
  }

  // Auth screen rendering for unauthenticated sessions
  function renderAuthScreen() {
    const app = document.getElementById('app');
    if (!app) return;
    app.innerHTML = `
      <div class="auth-box">
        <h2 class="auth-title">Authentication Required</h2>
        <p class="auth-desc">Enter your mobile security token or open the authenticated tunnel link to unlock the control center.</p>
        <input id="auth-token-input" class="auth-input" type="password" placeholder="Enter mobile token" autocomplete="current-password">
        <button id="auth-submit-btn" class="remote-launch-btn" style="width:100%;">Unlock Dashboard</button>
      </div>
    `;

    document.getElementById('auth-submit-btn').addEventListener('click', () => {
      const input = document.getElementById('auth-token-input');
      const val = input.value.trim();
      if (!val) return;
      document.cookie = `mrt=${encodeURIComponent(val)};max-age=31536000;path=/;SameSite=Lax`;
      window.location.reload();
    });
  }

  // Render Dashboard View with Direct Host Data
  function renderDashboard(data) {
    const syncTime = document.getElementById('sync-time');
    const pcDot = document.getElementById('pc-dot');
    const pcStatusText = document.getElementById('pc-status-text');
    const remoteActionWrap = document.getElementById('remote-action-wrap');
    const gatewayHeading = document.getElementById('gateway-device-name');
    const heroWrap = document.getElementById('hero-deck-wrap');
    const grid = document.getElementById('accounts-grid');
    const countBadge = document.getElementById('accounts-count');

    // 1. Host Telemetry (Guaranteed 12-Hour AM/PM format)
    const now = new Date();
    syncTime.textContent = 'Synced ' + format12HourTime(now);

    // Host PC Connection (Server is responding on host machine)
    if (pcDot && pcStatusText) {
      pcDot.className = 'status-dot active';
      pcStatusText.textContent = 'PC Online';
      pcStatusText.style.color = 'var(--text-primary)';
    }

    // Antigravity Desktop IDE Process Telemetry
    const appDot = document.getElementById('app-dot');
    const appStatusText = document.getElementById('app-status-text');
    const appPill = document.getElementById('app-status-pill');
    if (appDot && appStatusText && appPill) {
      if (data.antigravity_running) {
        appDot.className = 'status-dot active';
        appStatusText.textContent = 'IDE Active';
        appPill.className = 'app-pill active';
        appPill.title = 'Antigravity desktop IDE is open and active on PC';
        appPill.onclick = null;
      } else {
        appDot.className = 'status-dot stopped';
        appStatusText.textContent = 'IDE Closed';
        appPill.className = 'app-pill stopped';
        appPill.title = 'Antigravity IDE is closed. Tap to launch on host PC.';
        appPill.onclick = () => triggerLaunchIDE();
      }
    }

    // Sleep Prevention (Keep-Awake) Telemetry
    const sleepPill = document.getElementById('sleep-prevent-pill');
    const sleepStatusText = document.getElementById('sleep-status-text');
    if (sleepPill && sleepStatusText) {
      const isAwake = Boolean(data.prevent_sleep);
      if (isAwake) {
        sleepPill.className = 'sleep-pill active';
        sleepStatusText.textContent = 'Awake';
        sleepPill.title = 'Host Sleep Prevention Active (Host will not auto-suspend). Click to allow sleep.';
      } else {
        sleepPill.className = 'sleep-pill idle';
        sleepStatusText.textContent = 'Auto-Sleep';
        sleepPill.title = 'Host Sleep Prevention Off (Host will auto-suspend when idle). Click to keep awake.';
      }
    }

    // Audio Telemetry
    const audioPill = document.getElementById('host-audio-pill');
    const audioStatusText = document.getElementById('audio-status-text');
    const audioIcon = document.getElementById('audio-icon-elem');

    if (audioPill && audioStatusText && audioIcon) {
      const audio = data.audio;
      if (!audio || !audio.supported) {
        audioPill.classList.add('unsupported');
      } else {
        audioPill.classList.remove('unsupported');
        if (audio.muted) {
          audioPill.classList.add('muted');
          audioStatusText.textContent = 'Muted';
          audioPill.title = `Host Audio is Muted (${audio.volume}%). Click to unmute.`;
          audioIcon.innerHTML = `
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
            <line x1="23" y1="9" x2="17" y2="15"></line>
            <line x1="17" y1="9" x2="23" y2="15"></line>
          `;
        } else {
          audioPill.classList.remove('muted');
          audioStatusText.textContent = `${audio.volume}%`;
          audioPill.title = `Host Audio: ${audio.volume}%. Click to mute.`;
          audioIcon.innerHTML = `
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
            ${audio.volume > 50 ? '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>' : ''}
          `;
        }
      }
    }

    // Terminal Access Killswitch Visibility
    const terminalNavBtn = document.getElementById('terminal-nav-btn');
    if (terminalNavBtn) {
      if (data.terminal_enabled === false) {
        terminalNavBtn.style.display = 'none';
      } else {
        terminalNavBtn.style.display = '';
      }
    }

    // 2. Gateway & Remote Launcher
    const deviceName = data.remote_device_name || 'Host Machine';
    gatewayHeading.textContent = `${deviceName} Remote Session`;

    if (data.remote_url) {
      remoteActionWrap.innerHTML = `
        <div class="remote-btn-group">
          <a href="${sanitizeUrl(data.remote_url)}" target="_blank" rel="noopener noreferrer" class="remote-launch-btn">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
              <polyline points="15 3 21 3 21 9"></polyline>
              <line x1="10" y1="14" x2="21" y2="3"></line>
            </svg>
            <span>Open Official Remote Session ↗</span>
          </a>
          <button type="button" class="icon-btn-secondary" id="copy-remote-btn" title="Copy Remote Link" aria-label="Copy Remote Link">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
          </button>
        </div>
        <div class="remote-meta-row">
          <span class="remote-meta-dot"></span>
          <span>Google WebChannel Ready • Direct Authentication Bound</span>
        </div>
      `;

      document.getElementById('copy-remote-btn').addEventListener('click', () => {
        copyToClipboard(data.remote_url);
      });
    } else {
      remoteActionWrap.innerHTML = `
        <div style="font-size: 13px; color: var(--text-secondary);">
          No active remote tunnel registered on host machine.
        </div>
      `;
    }

    // 3. Accounts Segmentation
    const accounts = data.accounts || [];
    const activeAccount = accounts.find(a => a.is_current) || null;
    const availableAccounts = accounts.filter(a => !a.is_current);

    countBadge.textContent = String(availableAccounts.length);

    // 4. Hero Active Account Deck (Side-by-side with remote on desktop)
    if (activeAccount) {
      heroWrap.innerHTML = `
        <div class="hero-active-deck">
          <div>
            <div class="hero-top-row">
              <div class="hero-badge-group">
                <span class="badge-live">
                  <span class="badge-live-dot"></span>
                  ACTIVE ACCOUNT
                </span>
                <span class="badge-tier">PRO PLAN</span>
              </div>
              <div class="hero-process-status">
                ${data.antigravity_running ? `
                  <span class="process-badge running" title="Antigravity Electron process is active on PC">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                      <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
                      <line x1="8" y1="21" x2="16" y2="21"></line>
                      <line x1="12" y1="17" x2="12" y2="21"></line>
                    </svg>
                    <span>IDE Active</span>
                  </span>
                ` : `
                  <button type="button" class="launch-ide-btn" id="hero-launch-btn" title="Launch Antigravity Desktop IDE on Host PC">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                      <polygon points="5 3 19 12 5 21 5 3"></polygon>
                    </svg>
                    <span>Launch IDE</span>
                  </button>
                `}
              </div>
            </div>
            <div class="hero-account-info">
              <div class="hero-email">${escapeHtml(activeAccount.email)}</div>
              ${activeAccount.name ? `<div class="hero-name">${escapeHtml(activeAccount.name)}</div>` : ''}
            </div>
          </div>
          ${renderDualQuotas(activeAccount)}
        </div>
      `;

      const heroLaunchBtn = document.getElementById('hero-launch-btn');
      if (heroLaunchBtn) {
        heroLaunchBtn.addEventListener('click', () => triggerLaunchIDE());
      }
    } else {
      heroWrap.innerHTML = '';
    }

    // 5. Available Accounts Grid
    if (availableAccounts.length === 0) {
      grid.innerHTML = `
        <div class="account-card" style="text-align: center; color: var(--text-secondary); grid-column: 1 / -1;">
          No other PRO accounts currently registered in system storage.
        </div>
      `;
      return;
    }

    grid.innerHTML = '';
    availableAccounts.forEach(account => {
      const card = document.createElement('div');
      card.className = 'account-card';
      card.innerHTML = `
        <div>
          <div class="account-card-header">
            <div>
              <div class="account-email">${escapeHtml(account.email)}</div>
              ${account.name ? `<div class="account-name">${escapeHtml(account.name)}</div>` : ''}
            </div>
            <span class="badge-tier">PRO</span>
          </div>
          <div style="margin-top: 14px;">
            ${renderDualQuotas(account)}
          </div>
        </div>
        <button type="button" class="switch-btn" data-id="${escapeHtml(account.id)}" data-email="${escapeHtml(account.email)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 2.1l4 4-4 4"></path>
            <path d="M3 12.2v-2a4 4 0 0 1 4-4h14"></path>
            <path d="M7 21.9l-4-4 4-4"></path>
            <path d="M21 11.8v2a4 4 0 0 1-4 4H3"></path>
          </svg>
          <span>Switch Host PC to This Account</span>
        </button>
      `;

      card.querySelector('.switch-btn').addEventListener('click', () => {
        openSwitchModal(account.id, account.email);
      });

      grid.appendChild(card);
    });
  }

  // Switch Modal Handlers
  function openSwitchModal(id, email) {
    pendingAccountId = id;
    pendingAccountEmail = email;
    const modal = document.getElementById('switch-modal');
    const targetEl = document.getElementById('modal-target-email');
    if (!modal || !targetEl) return;

    targetEl.textContent = email;
    modal.classList.add('open');
  }

  function closeSwitchModal() {
    const modal = document.getElementById('switch-modal');
    if (modal) modal.classList.remove('open');
    pendingAccountId = null;
    pendingAccountEmail = null;
  }

  // Execute Account Switch Mutation
  async function confirmSwitch() {
    if (!pendingAccountId) return;
    const accountId = pendingAccountId;
    const targetEmail = pendingAccountEmail;
    closeSwitchModal();

    showToast(`Initiating switch to ${targetEmail}...`, 'info');

    const buttons = document.querySelectorAll('.switch-btn');
    buttons.forEach(btn => {
      btn.disabled = true;
      btn.querySelector('span').textContent = 'Switching Host...';
    });

    const res = await apiRequest('/api/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: accountId })
    });

    if (!res.success) {
      buttons.forEach(btn => {
        btn.disabled = false;
        btn.querySelector('span').textContent = 'Switch Host PC to This Account';
      });
      showToast('Switch error: ' + (res.error || 'Operation failed'), 'err');
      return;
    }

    showToast('Credentials applied. Relaunching Antigravity on host...', 'info');

    let attempts = 0;
    const pollInterval = setInterval(async () => {
      attempts++;
      const state = await apiRequest('/api/state');
      if (state && state.current === targetEmail) {
        clearInterval(pollInterval);
        buttons.forEach(btn => {
          btn.disabled = false;
          btn.querySelector('span').textContent = 'Switch Host PC to This Account';
        });
        renderDashboard(state);
        showToast(`Switched active account to ${targetEmail}`, 'ok');
      } else if (attempts >= 12) {
        clearInterval(pollInterval);
        buttons.forEach(btn => {
          btn.disabled = false;
          btn.querySelector('span').textContent = 'Switch Host PC to This Account';
        });
        loadStateData(false);
        showToast('Switch dispatched. Host state refreshed.', 'ok');
      }
    }, 1500);
  }

  // Trigger remote Antigravity IDE launch on host PC
  async function triggerLaunchIDE() {
    showToast('Launching Antigravity on host PC...', 'info');
    const res = await apiRequest('/api/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_ide: 'classic' })
    });
    if (res && res.success) {
      showToast('Antigravity IDE started on host machine!', 'ok');
      setTimeout(() => loadStateData(false), 1500);
    } else {
      showToast('Failed to launch Antigravity on host', 'err');
    }
  }

  // Data Loading Coordinator
  async function loadStateData(isManual = false) {
    const refreshBtn = document.getElementById('refresh-btn');
    if (isManual && refreshBtn) refreshBtn.classList.add('spinning');

    const data = await apiRequest('/api/state');
    if (refreshBtn) refreshBtn.classList.remove('spinning');

    if (!data) {
      const pcDot = document.getElementById('pc-dot');
      const pcStatusText = document.getElementById('pc-status-text');
      if (pcDot && pcStatusText) {
        pcDot.className = 'status-dot stopped';
        pcStatusText.textContent = 'PC Offline';
      }
      return;
    }

    if (data.need_login) {
      window.location.reload();
      return;
    }

    if (data.error) {
      showToast('Host: ' + data.error, 'err');
      return;
    }

    renderDashboard(data);
    if (isManual) {
      showToast('Host state synchronized', 'info');
    }
  }

  // Setup DOM Event Listeners
  function initEvents() {
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => loadStateData(true));
    }

    const sleepPill = document.getElementById('sleep-prevent-pill');
    if (sleepPill) {
      sleepPill.addEventListener('click', async () => {
        const res = await apiRequest('/api/sleep/toggle', { method: 'POST' });
        if (res && res.success) {
          const isAwake = Boolean(res.prevent_sleep);
          showToast(
            isAwake
              ? 'Host keep-awake enabled (Auto-suspend blocked)'
              : 'Host keep-awake disabled (Normal OS sleep allowed)',
            'info'
          );
          const sleepStatusText = document.getElementById('sleep-status-text');
          if (isAwake) {
            sleepPill.className = 'sleep-pill active';
            if (sleepStatusText) sleepStatusText.textContent = 'Awake';
            sleepPill.title = 'Host Sleep Prevention Active (Host will not auto-suspend). Click to allow sleep.';
          } else {
            sleepPill.className = 'sleep-pill idle';
            if (sleepStatusText) sleepStatusText.textContent = 'Auto-Sleep';
            sleepPill.title = 'Host Sleep Prevention Off (Host will auto-suspend when idle). Click to keep awake.';
          }
        } else {
          showToast('Failed to toggle host sleep prevention', 'err');
        }
      });
    }

    const audioPill = document.getElementById('host-audio-pill');
    if (audioPill) {
      audioPill.addEventListener('click', async () => {
        const res = await apiRequest('/api/audio/mute', { method: 'POST' });
        if (res && res.success && res.audio) {
          const a = res.audio;
          showToast(a.muted ? 'Host master audio muted' : `Host master audio unmuted (${a.volume}%)`, 'info');
          const audioStatusText = document.getElementById('audio-status-text');
          const audioIcon = document.getElementById('audio-icon-elem');
          if (a.muted) {
            audioPill.classList.add('muted');
            if (audioStatusText) audioStatusText.textContent = 'Muted';
            audioPill.title = `Host Audio is Muted (${a.volume}%). Click to unmute.`;
            if (audioIcon) {
              audioIcon.innerHTML = `
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                <line x1="23" y1="9" x2="17" y2="15"></line>
                <line x1="17" y1="9" x2="23" y2="15"></line>
              `;
            }
          } else {
            audioPill.classList.remove('muted');
            if (audioStatusText) audioStatusText.textContent = `${a.volume}%`;
            audioPill.title = `Host Audio: ${a.volume}%. Click to mute.`;
            if (audioIcon) {
              audioIcon.innerHTML = `
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
                ${a.volume > 50 ? '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>' : ''}
              `;
            }
          }
        } else {
          showToast('Failed to toggle host audio mute', 'err');
        }
      });
    }

    // Modal dialog controls
    const cancelBtn = document.getElementById('modal-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', closeSwitchModal);
    }

    const confirmBtn = document.getElementById('modal-confirm-btn');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', confirmSwitch);
    }

    const modal = document.getElementById('switch-modal');
    if (modal) {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) closeSwitchModal();
      });
    }
  }

  // Boot sequence
  document.addEventListener('DOMContentLoaded', () => {
    initEvents();
    loadStateData(false);
    autoRefreshTimer = setInterval(() => loadStateData(false), 45000);
  });
})();
