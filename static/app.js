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

  // Auto-capture token from URL query string and persist as cookie
  const queryToken = new URLSearchParams(window.location.search).get('token');
  if (queryToken) {
    document.cookie = `mrt=${encodeURIComponent(queryToken)};max-age=31536000;path=/;SameSite=Lax`;
    window.history.replaceState({}, document.title, window.location.pathname);
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
    const remoteInput = document.getElementById('remote-input');
    const gatewayHeading = document.getElementById('gateway-device-name');
    const heroWrap = document.getElementById('hero-deck-wrap');
    const grid = document.getElementById('accounts-grid');
    const countBadge = document.getElementById('accounts-count');

    // 1. Host Telemetry
    const now = new Date();
    syncTime.textContent = 'Synced ' + now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    if (data.antigravity_running) {
      pcDot.className = 'status-dot active';
      pcStatusText.textContent = 'PC Active';
      pcStatusText.style.color = 'var(--text-primary)';
    } else {
      pcDot.className = 'status-dot stopped';
      pcStatusText.textContent = 'PC Stopped';
      pcStatusText.style.color = 'var(--text-secondary)';
    }

    // 2. Gateway & Remote Launcher
    const deviceName = data.remote_device_name || 'Host Machine';
    gatewayHeading.textContent = `${deviceName} Remote Session`;

    if (data.remote_url) {
      remoteInput.value = data.remote_url;
      remoteActionWrap.innerHTML = `
        <div class="remote-btn-group">
          <a href="${data.remote_url}" target="_blank" rel="noopener noreferrer" class="remote-launch-btn">
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
                  HOST ACTIVE
                </span>
                <span class="badge-tier">PRO PLAN</span>
              </div>
              <div class="hero-process-status">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
                  <line x1="8" y1="21" x2="16" y2="21"></line>
                  <line x1="12" y1="17" x2="12" y2="21"></line>
                </svg>
                <span>${data.antigravity_running ? 'Antigravity IDE' : 'Stopped'}</span>
              </div>
            </div>
            <div class="hero-account-info">
              <div class="hero-email">${activeAccount.email}</div>
              ${activeAccount.name ? `<div class="hero-name">${activeAccount.name}</div>` : ''}
            </div>
          </div>
          ${renderDualQuotas(activeAccount)}
        </div>
      `;
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
              <div class="account-email">${account.email}</div>
              ${account.name ? `<div class="account-name">${account.name}</div>` : ''}
            </div>
            <span class="badge-tier">PRO</span>
          </div>
          <div style="margin-top: 14px;">
            ${renderDualQuotas(account)}
          </div>
        </div>
        <button type="button" class="switch-btn" data-id="${account.id}" data-email="${account.email}">
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

  // Data Loading Coordinator
  async function loadStateData(isManual = false) {
    const refreshBtn = document.getElementById('refresh-btn');
    if (isManual && refreshBtn) refreshBtn.classList.add('spinning');

    const data = await apiRequest('/api/state');
    if (refreshBtn) refreshBtn.classList.remove('spinning');

    if (data.need_login) {
      renderAuthScreen();
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

    const toggleDrawerBtn = document.getElementById('toggle-drawer-btn');
    const drawer = document.getElementById('remote-drawer');
    if (toggleDrawerBtn && drawer) {
      toggleDrawerBtn.addEventListener('click', () => {
        drawer.classList.toggle('open');
      });
    }

    const saveRemoteBtn = document.getElementById('save-remote-btn');
    if (saveRemoteBtn) {
      saveRemoteBtn.addEventListener('click', async () => {
        const input = document.getElementById('remote-input');
        const url = input.value.trim();
        const res = await apiRequest('/api/remote-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        });
        if (res.success) {
          showToast('Remote link saved', 'ok');
          drawer.classList.remove('open');
          loadStateData(false);
        } else {
          showToast('Failed to save remote link', 'err');
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
