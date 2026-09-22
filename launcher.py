#!/usr/bin/env python3
"""Interactive Terminal Launcher for Antigravity Remote.

Starts the local HTTP server, boots Cloudflare Quick Tunnel, outputs the authorized
remote link with secret token, and streams live activity logs directly to the terminal.
Pure Python standard library only.
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from app.server import run_server
from app.switcher import get_sleep_inhibit_status, set_sleep_inhibit

STATE_PATH = os.path.join(BASE_DIR, "state.json")
PORT = 8077
tunnel_proc = None


def cleanup_and_exit(signum=None, frame=None):
    """Gracefully terminate background tunnel and process on exit."""
    global tunnel_proc
    print("\n\033[93m[!] Shutting down Antigravity Remote...\033[0m", flush=True)
    set_sleep_inhibit(False)
    if tunnel_proc and tunnel_proc.poll() is None:
        try:
            tunnel_proc.terminate()
            tunnel_proc.wait(timeout=2)
        except Exception:
            try:
                tunnel_proc.kill()
            except Exception:
                pass
    print("\033[92m[✓] Antigravity Remote stopped cleanly.\033[0m", flush=True)
    sys.exit(0)


def load_token():
    """Read authorization mobile token from state.json."""
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("mobile_token", "")
        except Exception:
            pass
    return ""


def parse_mode(args, st_data):
    """Determine operational tunnel mode from CLI flags and state.json."""
    if any(a in args for a in ("--quick", "-q", "--try")):
        return "quick"
    if any(a in args for a in ("--permanent", "-p")):
        return "permanent"
    if any(a in args for a in ("--dual", "-d")):
        return "dual"
    if any(a in args for a in ("--local", "-l")):
        return "local"
    return st_data.get("tunnel_mode", "auto").lower()


def start_quick_tunnel(port):
    """Boot ephemeral Cloudflare Quick Tunnel and capture trycloudflare.com URL."""
    cloudflared_bin = "/usr/local/bin/cloudflared" if os.path.exists("/usr/local/bin/cloudflared") else os.path.expanduser("~/.local/bin/cloudflared")
    if not os.path.exists(cloudflared_bin):
        cloudflared_bin = "cloudflared"

    cmd = [
        cloudflared_bin,
        "tunnel",
        "--no-autoupdate",
        "--url",
        f"http://127.0.0.1:{port}",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        print(f"\033[91m[✗] Failed to launch cloudflared: {e}\033[0m", file=sys.stderr)
        return None, None

    url_pattern = re.compile(r"https://[-a-z0-9.]+\.trycloudflare\.com")
    tunnel_url = None
    for line in proc.stdout:
        m = url_pattern.search(line)
        if m:
            tunnel_url = m.group(0)
            break
        if "error" in line.lower() and "failed" in line.lower():
            print(f"\033[93m[cloudflared]\033[0m {line.strip()}", flush=True)

    return proc, tunnel_url


def main():
    global tunnel_proc
    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print("""Antigravity Remote Launcher

Usage:
  python3 launcher.py [OPTIONS]

Options:
  --quick, -q, --try      Force Cloudflare Quick Tunnel (*.trycloudflare.com)
  --permanent, -p         Force Cloudflare Zero Trust Permanent Named Tunnel
  --dual, -d              Run both Permanent Domain and Quick Tunnel simultaneously
  --local, -l             Localhost only (no public cloud tunnel)
  --prevent-sleep         Keep host awake and block auto-suspend while running
  --allow-sleep           Allow normal OS auto-suspend / sleep while running
  --help, -h              Show this help message
""")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGTERM, cleanup_and_exit)

    # Apply sleep inhibit CLI flags if specified
    if "--allow-sleep" in args:
        set_sleep_inhibit(False)
    elif "--prevent-sleep" in args:
        set_sleep_inhibit(True)

    if "--disable-terminal" in args or "--no-terminal" in args:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                cur_st = json.load(f)
            cur_st["terminal_enabled"] = False
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(cur_st, f, indent=2)
        except Exception:
            pass
    elif "--enable-terminal" in args:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                cur_st = json.load(f)
            cur_st["terminal_enabled"] = True
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(cur_st, f, indent=2)
        except Exception:
            pass

    # 1. Start Server in Background Thread
    print("\033[96m[*] Starting Antigravity Remote Server on port 8077...\033[0m", flush=True)
    server_thread = threading.Thread(target=run_server, args=(PORT,), daemon=True)
    server_thread.start()

    # Wait for HTTP server to respond
    server_ready = False
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as resp:
                if resp.status == 200:
                    server_ready = True
                    break
        except Exception:
            time.sleep(0.2)

    if not server_ready:
        print("\033[91m[✗] Failed to start local server on port 8077.\033[0m", file=sys.stderr)
        sys.exit(1)

    # 2. Check Systemd Cloudflare Service & Determine Mode
    token = load_token()
    is_service_active = False
    try:
        chk = subprocess.run(["systemctl", "is-active", "cloudflared"], capture_output=True, text=True, timeout=2)
        is_service_active = (chk.stdout.strip() == "active")
    except Exception:
        pass

    st_data = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                st_data = json.load(f)
        except Exception:
            pass

    custom_domain = st_data.get("custom_domain", "").strip()
    mode = parse_mode(args, st_data)

    quick_url = None
    perm_url = f"https://{custom_domain}" if custom_domain else None

    if mode == "local":
        print("\033[93m[*] Local Mode: Public cloud tunnel disabled.\033[0m", flush=True)
    elif mode == "quick":
        print("\033[96m[*] Establishing Cloudflare Quick Tunnel (--quick mode)...\033[0m", flush=True)
        tunnel_proc, quick_url = start_quick_tunnel(PORT)
    elif mode == "dual":
        print("\033[96m[*] Dual Mode: Activating Quick Tunnel alongside Permanent Service...\033[0m", flush=True)
        tunnel_proc, quick_url = start_quick_tunnel(PORT)
    elif mode == "permanent":
        if not is_service_active:
            print("\033[93m[!] Permanent service not active. Starting system service...\033[0m", flush=True)
            subprocess.run(["systemctl", "start", "cloudflared"], check=False)
            is_service_active = True
    else:  # "auto"
        if is_service_active:
            pass
        else:
            print("\033[96m[*] Establishing Cloudflare Quick Tunnel (Auto fallback)...\033[0m", flush=True)
            tunnel_proc, quick_url = start_quick_tunnel(PORT)

    # 3. Display Premium Terminal Dashboard Banner
    print("\033[2J\033[H", end="")  # Clear screen and move cursor to home
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print("\033[1;37m                 ANTIGRAVITY REMOTE - CONTROL CENTER\033[0m")
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print(f"  \033[1;32m[✓] Local Port     :\033[0m http://127.0.0.1:{PORT}")
    sleep_disp = "\033[1;32mActive (Host auto-suspend blocked)\033[0m" if get_sleep_inhibit_status() else "\033[90mDisabled (Normal OS sleep)\033[0m"
    print(f"  \033[1;32m[✓] Sleep Inhibit  :\033[0m {sleep_disp}")
    term_on = bool(st_data.get("terminal_enabled", True))
    term_disp = "\033[1;32mEnabled (Interactive Shell Online)\033[0m" if term_on else "\033[91mDisabled (Access Blocked)\033[0m"
    print(f"  \033[1;32m[✓] Web Terminal   :\033[0m {term_disp}")

    if mode == "dual" or (perm_url and quick_url):
        print(f"  \033[1;32m[✓] Permanent URL  :\033[0m {perm_url or 'Active (System Service)'}")
        print(f"  \033[1;32m[✓] Quick Tunnel   :\033[0m {quick_url}")
        print(f"  \033[1;32m[✓] Secret Token   :\033[0m {token}")
        print("\033[1;36m" + "-" * 74 + "\033[0m")
        if perm_url:
            print("  \033[1;33m👉 PERMANENT LINK (Your Domain):\033[0m")
            print(f"     \033[1;4;37m{perm_url}/?token={token}\033[0m\n")
        print("  \033[1;33m👉 QUICK SHARE LINK (Temporary):\033[0m")
        print(f"     \033[1;4;37m{quick_url}/?token={token}\033[0m")
    elif mode == "quick" or (quick_url and not perm_url):
        print(f"  \033[1;32m[✓] Cloud Tunnel   :\033[0m {quick_url} \033[90m(Quick Tunnel Mode)\033[0m")
        print(f"  \033[1;32m[✓] Secret Token   :\033[0m {token}")
        print("\033[1;36m" + "-" * 74 + "\033[0m")
        print("  \033[1;33m👉 OPEN ON MOBILE (Direct Authorized Link):\033[0m")
        print(f"     \033[1;4;37m{quick_url}/?token={token}\033[0m")
    elif mode == "local":
        print("  \033[1;33m[!] Mode           :\033[0m Localhost Only (No Public Tunnel)")
        print(f"  \033[1;32m[✓] Secret Token   :\033[0m {token}")
        print("\033[1;36m" + "-" * 74 + "\033[0m")
        print("  \033[1;33m👉 OPEN LOCALLY:\033[0m")
        print(f"     \033[1;4;37mhttp://127.0.0.1:{PORT}/?token={token}\033[0m")
    else:
        display_perm = perm_url or "Active (Cloudflare Zero Trust System Service)"
        print(f"  \033[1;32m[✓] Cloud Tunnel   :\033[0m {display_perm}")
        print(f"  \033[1;32m[✓] Secret Token   :\033[0m {token}")
        print("\033[1;36m" + "-" * 74 + "\033[0m")
        if perm_url:
            print("  \033[1;33m👉 OPEN ON MOBILE (Permanent Custom Domain Link):\033[0m")
            print(f"     \033[1;4;37m{perm_url}/?token={token}\033[0m")
        else:
            print("  \033[1;33m👉 CLOUDFLARE ZERO TRUST ACTIVE:\033[0m")
            print("     Access via your configured Cloudflare hostname with secret token:")
            print(f"     \033[1;37m?token={token}\033[0m")

    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print("  \033[90mFlags: \033[1;37m--quick\033[0;90m (force quick tunnel), \033[1;37m--dual\033[0;90m (both), \033[1;37m--local\033[0;90m (no tunnel)\033[0m")
    print("  \033[90mPress \033[1;37mCtrl+C\033[0;90m anytime to cleanly exit.\033[0m")
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print("\033[1;35m[LIVE REQUEST STREAM]\033[0m\n", flush=True)

    # 4. Keep main thread alive and monitor processes
    try:
        while True:
            if tunnel_proc and tunnel_proc.poll() is not None:
                print("\033[91m[!] Quick Tunnel process disconnected.\033[0m", flush=True)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_and_exit()


if __name__ == "__main__":
    main()
