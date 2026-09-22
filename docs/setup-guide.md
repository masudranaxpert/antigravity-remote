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

### 2. Install Cloudflared (Quick Tunnel)
Download and install the official Cloudflare tunnel binary:

```bash
# Ubuntu / Debian (.deb package)
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb

# Or standalone user binary (No sudo required)
mkdir -p ~/.local/bin
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/.local/bin/cloudflared
chmod +x ~/.local/bin/cloudflared
```

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

## ⚙️ Configuration & Custom Ports

By default, the server binds to port `8077`. To change the port or customize behavior:

1. Edit `launcher.py` and modify:
   ```python
   PORT = 8077  # Set to any available unprivileged port
   ```
2. The security token is stored in `state.json`. If you wish to change your mobile access password, edit `state.json`:
   ```json
   {
     "mobile_token": "your-custom-secret-token",
     "remote_url": ""
   }
   ```
