# Antigravity Remote

<div align="center">
  <img src="icon.png" width="120" height="120" alt="Antigravity Remote Icon" />
  <p><strong>A zero-dependency remote management dashboard, real-time quota monitor, and seamless Google account switcher for Antigravity IDE.</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.10+" />
    <img src="https://img.shields.io/badge/Dependencies-Zero%20(Stdlib%20Only)-success" alt="Zero Dependencies" />
    <img src="https://img.shields.io/badge/Tunnel-Cloudflare%20Quick%20Tunnel-F38020?style=flat&logo=cloudflare&logoColor=white" alt="Cloudflare Tunnel" />
    <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Windows-blue" alt="Platform" />
    <img src="https://img.shields.io/badge/Design-Impeccable%20(0%20Anti--Patterns)-purple" alt="Design" />
  </p>
</div>

---

## 🌟 Highlights

- **🔄 One-Tap Credential Hot-Swap**: Instantly switch the active Google account on your host PC directly from your phone. Injects credentials into the Linux Secret Service (GNOME Keyring) and atomically restarts the Electron IDE without interrupting background tray apps.
- **🔍 Authoritative Ground-Truth Detection**: Decodes Google OAuth JWT claims (`id_token`) on the fly. Guaranteed 100% accurate identification of the live active account, even after manual desktop logins or logouts.
- **🌐 Instant Official Remote Desktop**: Automatically extracts host machine UUID and constructs pre-authorized Google WebChannel URLs. Tap on mobile to immediately connect to the PC session with the active account pre-selected.
- **📊 Streamlined Dual Quota Telemetry**: Filters out cluttered sub-models and tracks the two primary model pools: **Gemini** and **Claude** with precise percentages and live countdown reset timers.
- **🎨 Studio-Grade, Anti-Slop UI**: Designed in strict accordance with [Impeccable Design Guidelines](https://impeccable.style/slop/)—calm neutral carbon/obsidian surfaces, high-contrast typography, zero layout thrashing, and zero garish AI glows.
- **⚡ 100% Standard Library**: Pure Python. Zero third-party packages (`pip`), zero Node.js/npm dependencies, zero framework bloat.
- **🖥️ Dedicated Desktop & Taskbar Integration**: Includes desktop shortcuts, custom brand icon isolation on the taskbar/dock, and live streaming terminal request logs.

---

## 🚀 Quick Start

### 1. Requirements
- **Python 3.10+** (Standard installation)
- **Cloudflared CLI** (Optional but recommended for mobile access outside local Wi-Fi)

### 2. Installation

Clone the repository to your preferred location:

```bash
git clone https://github.com/<your-username>/antigravity-remote.git "Antigravity Remote"
cd "Antigravity Remote"
```

### 3. Launching

#### On Linux (Ubuntu / Debian / Arch / Fedora):
Double-click the desktop shortcut or run directly from terminal:
```bash
./run.sh
```

#### Headless / Windows / macOS / Command Line:
```bash
python3 launcher.py
```

### 4. What Happens on Launch:
1. The local server boots on `http://127.0.0.1:8077`.
2. Cloudflare establishes a secure, encrypted Quick Tunnel.
3. The terminal displays your unique authorized mobile link:
   ```text
   ==========================================================================
                    ANTIGRAVITY REMOTE - CONTROL CENTER
   ==========================================================================
     [✓] Local Port  : http://127.0.0.1:8077
     [✓] Cloud Tunnel: https://your-tunnel.trycloudflare.com
     [✓] Secret Token: abc123xyz...
   --------------------------------------------------------------------------
     👉 OPEN ON MOBILE (Direct Authorized Link):
        https://your-tunnel.trycloudflare.com/?token=abc123xyz...
   ==========================================================================
   ```
4. Simply tap or copy the link to your phone browser. You now have full control over your host PC's Antigravity IDE!

---

## 📁 Repository Structure

```text
Antigravity Remote/
├── app/
│   ├── __init__.py           # Package marker
│   ├── detector.py           # Multi-tier JWT identity & remote URL resolver
│   ├── switcher.py           # Secret Service keyring & process lifecycle orchestrator
│   └── server.py             # Pure Python HTTP server and REST API
├── templates/
│   └── index.html            # Semantic, responsive HTML5 layout (0 anti-patterns)
├── static/
│   ├── app.css               # Studio-grade design system (carbon & obsidian palette)
│   ├── app.js                # Vanilla ES6 client controller with real-time polling
│   └── icon.png              # Monochrome brand icon (used as web favicon)
├── docs/
│   ├── architecture.md       # Detailed technical design & security model
│   ├── setup-guide.md        # Linux & Windows step-by-step setup guides
│   └── troubleshooting.md    # FAQ and diagnostics guide
├── launcher.py               # Interactive CLI launcher with live logging
├── terminal_gui.py           # Dedicated GTK3/VTE window with unique taskbar icon
├── run.sh                    # Universal shell runner
├── icon.png                  # High-resolution brand icon
└── test_self_check.py        # Automated runnable self-check test suite
```

---

## 📚 Documentation

For in-depth technical documentation, refer to the guides in the `docs/` folder:

- [Architecture & Security Model](docs/architecture.md): Deep dive into token decoding, Secret Service integration, and zero-trust mobile auth.
- [Detailed Setup Guide](docs/setup-guide.md): Step-by-step configuration for Linux, Windows, and systemd automation.
- [Troubleshooting & FAQ](docs/troubleshooting.md): Solutions for common networking, keyring, and permission issues.

---

## 🛡️ Security & Privacy

- **Cryptographic Mobile Token**: All remote requests require an authorized security token generated cryptographically on the host PC.
- **Zero Remote Storage**: Credentials and tokens never leave your local machine or touch any external database.
- **Strict HTTPS Encryption**: Remote traffic is encrypted end-to-end via Cloudflare's TLS infrastructure.
- **Localhost Bound**: The HTTP listener is strictly bound to `127.0.0.1`.

---

## 📄 License

Distributed under the [MIT License](LICENSE).
