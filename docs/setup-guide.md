# Setup & Deployment Guide

This guide covers step-by-step setup instructions for both Linux and Windows environments.

---

## 🐧 Linux Setup (Ubuntu / Debian / Arch / Fedora)

### 1. Prerequisites
Ensure Python 3.10+ and standard tools are installed:

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y python3 python3-pip libsecret-tools

# Arch Linux
sudo pacman -S python libsecret

# Fedora
sudo dnf install python3 libsecret
```

Additionally, install and configure [Antigravity-Manager (lbjlaq/Antigravity-Manager)](https://github.com/lbjlaq/Antigravity-Manager) to add and manage your Google accounts.

### 2. Install Cloudflared (Tunnel Provider)
Install `cloudflared` system-wide so both your user and background system services have direct access:

```bash
# Official Cloudflare APT Repository (Ubuntu / Debian)
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg | sudo tee /usr/share/keyrings/cloudflare-public-v2.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# Or standalone system binary
sudo curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
```

> [!TIP]
> Want a permanent URL with your own domain that never changes? See the [Permanent Custom Domain Guide](custom-domain-tunnel.md).

### 3. Clone and Initialize
```bash
git clone https://github.com/masudranaxpert/antigravity-remote.git ~/apps/"Antigravity Remote"
cd ~/apps/"Antigravity Remote"
chmod +x run.sh launcher.py
```

### 4. Create Desktop Shortcut
To launch with a single click from your desktop and GNOME application launcher:

```bash
cp com.antigravity.remote.desktop ~/.local/share/applications/
cp com.antigravity.remote.desktop ~/Desktop/
chmod +x ~/Desktop/com.antigravity.remote.desktop
gio set ~/Desktop/com.antigravity.remote.desktop metadata::trusted true
```

---

## 🪟 Windows Setup (Windows 10 / 11)

Antigravity Remote runs natively on Windows using standard Python.

### 1. Prerequisites
1. Install **Python 3.10+** from [python.org](https://www.python.org/) (ensure **"Add Python to PATH"** is checked during installation).
2. Install [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) on Windows to register multi-account profiles.
3. Download **cloudflared.exe** from [Cloudflare Releases](https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe) and place it in your `PATH` (e.g., `C:\Windows\System32` or your user folder).

### 2. Clone Repository
Open PowerShell or Windows Terminal:

```powershell
git clone https://github.com/masudranaxpert/antigravity-remote.git "$HOME\Antigravity-Remote"
cd "$HOME\Antigravity-Remote"
```

### 3. Launching on Windows
Run the interactive CLI launcher:

```powershell
python launcher.py
```

*Note on Windows Credential Management:* On Windows, Google Antigravity stores credentials in the Windows Credential Manager (`wincred`). If you are running on Windows, account switching commands can be executed via the `app/switcher.py` Windows API fallback.

---

## ⚙️ Configuration & Customization

All application settings are persisted in `state.json` in the root directory.

### `state.json` Schema & Options

```json
{
  "mobile_token": "your-random-token",
  "custom_domain": "remote.yourdomain.com",
  "tunnel_mode": "permanent",
  "prevent_sleep": true
}
```

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `mobile_token` | `string` | *(Auto-generated)* | Cryptographically random secret token required to unlock the mobile dashboard. Can be customized. |
| `custom_domain` | `string` | `""` | Your registered Cloudflare Zero Trust public hostname (e.g. `remote.yourdomain.com`). Used for permanent link generation. |
| `tunnel_mode` | `string` | `"auto"` | Controls how the remote tunnel behaves. See mode table below. |
| `prevent_sleep` | `boolean` | `true` | When `true`, prevents host OS auto-suspend and idle sleep while the server runs using Linux native `systemd-inhibit`. Toggleable from the mobile web UI. |

### Tunnel Modes (`tunnel_mode`)

| Mode | Behavior | Use Case |
| :--- | :--- | :--- |
| `"auto"` | If Cloudflare Zero Trust system service is active, uses your permanent domain. If not, automatically falls back to Quick Tunnel (`trycloudflare.com`). | Default setup for zero-configuration deployments. |
| `"permanent"` | Strictly binds to your Cloudflare Named Tunnel (`cloudflared.service`). Never spins up temporary trycloudflare links. | Recommended when you have your own domain and want 100% fixed, non-expiring access. |
| `"quick"` | Forces an ephemeral Cloudflare Quick Tunnel (`*.trycloudflare.com`), even if a permanent system service is running. | Ideal for quick testing or sharing a temporary link without disclosing your personal domain. |
| `"dual"` | Runs **both** tunnels concurrently. Displays your permanent custom domain link and a fresh temporary Quick Tunnel link side by side. | Useful when you want fixed personal access while simultaneously sharing a guest link. |
| `"local"` | Starts the local HTTP server on `127.0.0.1:8077` with no cloud tunnel. | Local network / Wi-Fi only, or when connecting via WireGuard / Tailscale VPN. |

---

## 💻 CLI Flags & Shortcuts

You can override `state.json` settings on the fly from the command line:

```bash
# Force temporary Quick Tunnel
./run.sh --quick           # or -q, --try
python3 launcher.py --quick

# Force Permanent Named Tunnel (Custom Domain)
./run.sh --permanent       # or -p
python3 launcher.py --permanent

# Run both Permanent and Quick Tunnels simultaneously
./run.sh --dual            # or -d
python3 launcher.py --dual

# Localhost only (no cloud tunnel)
./run.sh --local           # or -l
python3 launcher.py --local

# Sleep Prevention (Keep-Awake) controls
python3 launcher.py --prevent-sleep  # Keep host awake (auto-suspend blocked)
python3 launcher.py --allow-sleep    # Allow normal OS idle sleep/suspend

# Display CLI help
python3 launcher.py --help
```

---

## 🖥️ Desktop Shortcut Management

To ensure only **one** clean desktop launcher icon is shown:

```bash
# Copy single trusted desktop entry
cp "com.antigravity.remote.desktop" ~/Desktop/"Antigravity Remote.desktop"
chmod +x ~/Desktop/"Antigravity Remote.desktop"
gio set ~/Desktop/"Antigravity Remote.desktop" metadata::trusted true
```

Double-clicking the desktop icon automatically starts the native dark terminal window, reads your `state.json` configuration, and displays the authorized connection URL.
