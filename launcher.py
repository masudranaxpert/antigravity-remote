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

STATE_PATH = os.path.join(BASE_DIR, "state.json")
PORT = 8077
tunnel_proc = None


def cleanup_and_exit(signum=None, frame=None):
    """Gracefully terminate background tunnel and process on exit."""
    global tunnel_proc
    print("\n\033[93m[!] Shutting down Antigravity Remote...\033[0m", flush=True)
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


def main():
    global tunnel_proc
    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGTERM, cleanup_and_exit)

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

    # 2. Boot Cloudflare Quick Tunnel
    print("\033[96m[*] Establishing Cloudflare Quick Tunnel...\033[0m", flush=True)
    cloudflared_bin = os.path.expanduser("~/.local/bin/cloudflared")
    if not os.path.exists(cloudflared_bin):
        cloudflared_bin = "cloudflared"

    cmd = [
        cloudflared_bin,
        "tunnel",
        "--no-autoupdate",
        "--url",
        f"http://127.0.0.1:{PORT}",
    ]

    try:
        tunnel_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        print(f"\033[91m[✗] Failed to launch cloudflared: {e}\033[0m", file=sys.stderr)
        cleanup_and_exit()

    # 3. Capture Tunnel URL from stream
    tunnel_url = None
    token = load_token()
    url_pattern = re.compile(r"https://[-a-z0-9.]+\.trycloudflare\.com")

    for line in tunnel_proc.stdout:
        m = url_pattern.search(line)
        if m:
            tunnel_url = m.group(0)
            break
        if "error" in line.lower() and "failed" in line.lower():
            print(f"\033[93m[cloudflared]\033[0m {line.strip()}", flush=True)

    if not tunnel_url:
        print("\033[91m[✗] Could not resolve Cloudflare Tunnel URL.\033[0m", file=sys.stderr)
        cleanup_and_exit()

    mobile_access_url = f"{tunnel_url}/?token={token}" if token else tunnel_url

    # Save active tunnel URL to state.json for programmatic access
    try:
        st_data = {}
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                st_data = json.load(f)
        st_data["tunnel_url"] = tunnel_url
        st_data["mobile_access_url"] = mobile_access_url
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st_data, f, indent=2)
    except Exception:
        pass

    # 4. Display Premium Terminal Dashboard Banner
    print("\033[2J\033[H", end="")  # Clear screen and move cursor to home
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print("\033[1;37m                 ANTIGRAVITY REMOTE - CONTROL CENTER\033[0m")
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print(f"  \033[1;32m[✓] Local Port  :\033[0m http://127.0.0.1:{PORT}")
    print(f"  \033[1;32m[✓] Cloud Tunnel:\033[0m {tunnel_url}")
    print(f"  \033[1;32m[✓] Secret Token:\033[0m {token}")
    print("\033[1;36m" + "-" * 74 + "\033[0m")
    print("  \033[1;33m👉 OPEN ON MOBILE (Direct Authorized Link):\033[0m")
    print(f"     \033[1;4;37m{mobile_access_url}\033[0m")
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print("  \033[90mKeep this terminal window open while you need remote access.\033[0m")
    print("  \033[90mPress \033[1;37mCtrl+C\033[0;90m anytime to safely terminate server and tunnel.\033[0m")
    print("\033[1;36m" + "=" * 74 + "\033[0m")
    print("\033[1;35m[LIVE REQUEST STREAM]\033[0m\n", flush=True)

    # 5. Keep main thread alive and monitor processes
    try:
        while True:
            if tunnel_proc.poll() is not None:
                print("\033[91m[!] Cloudflare Tunnel disconnected unexpectedly.\033[0m", flush=True)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_and_exit()


if __name__ == "__main__":
    main()
