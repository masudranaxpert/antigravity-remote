# Antigravity Remote

<div align="center">
  <img src="icon.png" width="120" height="120" alt="Antigravity Remote Icon" />
  <p><strong>Lightweight remote dashboard, real-time quota monitor, and Google account switcher for Antigravity IDE.</strong></p>
</div>

---

## Features

- **One-Tap Account Switcher**: Swap active Google accounts directly from your phone. Injects credentials into system keyring and restarts Antigravity cleanly.
- **Authoritative Identity Detection**: Decodes Google OAuth JWT claims (`id_token`) on the fly to detect the active account with 100% accuracy.
- **Host Sleep Prevention (Keep-Awake)**: Inhibits system auto-suspend and idle sleep via Linux native `systemd-inhibit` while the remote gateway runs. Toggle anytime from the mobile dashboard or `state.json`.
- **Full-Featured Mobile Web Terminal**: Native Linux PTY shell (`/bin/bash`) accessible directly from your phone browser via RFC 6455 WebSockets. Includes touch-friendly accessory keys (`Esc`, `Tab`, `Ctrl`, arrows, pipe), quick command chips, and dynamic viewport resizing.
- **Host PC Audio Telemetry & Remote Control**: Live master volume percentage detection and one-tap remote mute/unmute directly from your phone.
- **Official Remote Gateway**: Generates one-tap Google Account Chooser remote desktop links with pre-authorized active credentials.
- **Dual Quota Telemetry**: Clean, unnested tracking for Gemini and Claude with live countdown reset timers.
- **Multi-Mode Tunnel Engine**: Instant support for permanent Cloudflare Zero Trust custom domains, temporary Quick Tunnels (`trycloudflare.com`), Dual Mode, and Localhost-only.
- **Anti-Cookie-Theft & Device Binding**: Cryptographically signed ephemeral session tokens bound to the client's device fingerprint (OS, browser, platform). Even if an attacker copies the cookie header, replay attempts on different devices or browsers are immediately rejected with HTTP 401.
- **Access Audit Logging (`log/ip.json`)**: Persistent audit logging recording client IP, Cloudflare country/datacenter, device type, OS, and browser with automatic 30-day retention and self-cleaning.
- **Terminal Access Killswitch**: Instantly toggle shell terminal access on/off via `state.json`, `static.json`, CLI (`--disable-terminal`), or web API.
- **Studio-Grade UI**: Refined dark palette (`#0d0f12`), symmetrical mobile action buttons, zero layout thrashing, dedicated server-side auth gate (zero FOUC), and responsive layouts.
- **Zero Dependencies**: 100% Python standard library. No pip packages, no node_modules.
- **Desktop & Terminal Integration**: Dedicated desktop shortcut with independent taskbar icon and live request streaming.

---

## Prerequisites & Dependencies

1. **Google Antigravity IDE** (Installed on host PC)
2. **Antigravity Manager (`antigravity-tools` / AMGR)**: [lbjlaq/Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) (Required for initial multi-account login and token storage in `~/.antigravity_tools/accounts/`).
3. **Linux Keyring Tools** (Linux only): `libsecret-tools` (`secret-tool`) for GNOME Keyring credential injection.
4. **Cloudflared CLI** (Optional): For secure remote access outside your local Wi-Fi network.

---

## Quick Start

### 1. Clone
```bash
git clone https://github.com/masudranaxpert/antigravity-remote.git "Antigravity Remote"
cd "Antigravity Remote"
```

### 2. Launch
- **Linux Desktop**: Double-click `com.antigravity.remote.desktop` or run `./run.sh`
- **Headless / Windows / Terminal**: `python3 launcher.py`

### 3. Access
Open the generated authorized link on your phone browser:
```text
https://<your-tunnel>.trycloudflare.com/?token=<your-token>
```

---

## Documentation

- [Architecture & Security](docs/architecture.md): Internal mechanics, JWT decoding, and credential injection.
- [Setup & Deployment](docs/setup-guide.md): Step-by-step setup for Linux and Windows.
- [Permanent Custom Domain](docs/custom-domain-tunnel.md): Configure fixed permanent URLs via Cloudflare Zero Trust (never expires).
- [Troubleshooting](docs/troubleshooting.md): Common questions and solutions.

---

## Security
 
- **Device-Bound Ephemeral Sessions**: Uses HMAC-signed tokens tied to the authorized client's browser and device fingerprint. Session tokens cannot be replayed from other devices.
- **HttpOnly & SameSite Protection**: Cookies are transmitted with `HttpOnly; SameSite=Lax` headers, immune to client-side XSS exfiltration.
- **Access Audit Trail**: All authentication attempts, incoming client IPs, and device details are logged in `log/ip.json` with 30-day retention.
- **Terminal Killswitch**: Web shell access is gated by both authentication and an administrative toggle in `state.json`/`static.json`.
- **Zero-Trust Network Perimeter**: Traffic is encrypted end-to-end via Cloudflare TLS, with the local daemon listening strictly on loopback `127.0.0.1`.

---

## License

Distributed under the [MIT License](LICENSE).
