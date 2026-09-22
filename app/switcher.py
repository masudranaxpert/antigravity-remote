"""Authoritative account credential switcher and process orchestrator.

Applies direct Secret Service and file-based credential updates without
depending on daemon HTTP listeners. Pure Python standard library only.
"""
import datetime
import glob
import json
import os
import signal
import subprocess
import time

HOME = os.path.expanduser("~")
ACCOUNTS_DIR = os.path.join(HOME, ".antigravity_tools", "accounts")
INDEX_PATH = os.path.join(HOME, ".antigravity_tools", "accounts.json")
GEMINI_DIR = os.path.join(HOME, ".gemini")


def close_running_antigravity():
    """Cleanly close running Antigravity Electron instances and release stale locks."""
    pids = []
    for pid_path in glob.glob("/proc/[0-9]*"):
        try:
            exe = os.path.realpath(os.path.join(pid_path, "exe"))
            if ("opt/antigravity" in exe or "antigravity-ide" in exe) and "antigravity-tools" not in exe:
                pids.append(int(os.path.basename(pid_path)))
        except Exception:
            continue

    # Graceful shutdown with SIGTERM
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + 2.0
    while time.time() < deadline:
        still_running = [p for p in pids if os.path.exists(f"/proc/{p}")]
        if not still_running:
            break
        time.sleep(0.2)

    # Force kill any remaining processes
    for p in pids:
        try:
            if os.path.exists(f"/proc/{p}"):
                os.kill(p, signal.SIGKILL)
        except OSError:
            pass

    # Clear stale Singleton locks in Antigravity user data
    for lock_file in glob.glob(os.path.expanduser("~/.config/Antigravity/Singleton*")):
        try:
            os.unlink(lock_file)
        except OSError:
            pass


def launch_antigravity_clean(target_ide="classic"):
    """Launch Antigravity desktop IDE in user's graphical session."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")

    exe = "/snap/bin/antigravity-ide-snap.antigravity-ide" if target_ide == "ide" else "/snap/bin/antigravity"
    try:
        subprocess.Popen(
            [exe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    except Exception:
        pass


def apply_switch(account_id, target_ide="classic"):
    """Atomically switch system credentials to target account and restart Antigravity.

    ponytail: direct credential injection guarantees instant switch without
    triggering UI popups or interrupting AMGR system tray operation.
    """
    account_file = os.path.join(ACCOUNTS_DIR, f"{account_id}.json")
    if not os.path.exists(account_file):
        return False, f"Account record not found: {account_id}"

    try:
        with open(account_file, "r", encoding="utf-8") as f:
            acc = json.load(f)
    except Exception as e:
        return False, f"Failed to load account data: {e}"

    token = acc.get("token") or {}
    access_token = token.get("access_token", "")
    refresh_token = token.get("refresh_token", "")
    expiry_ts = token.get("expiry_timestamp", 0)
    email = acc.get("email", "")

    if not email or not refresh_token:
        return False, "Selected account credentials are incomplete."

    try:
        expiry_dt = datetime.datetime.fromtimestamp(expiry_ts, datetime.timezone.utc)
    except Exception:
        expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    expiry_str = expiry_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # 1. Update Linux Secret Service (GNOME Keyring)
    keyring_payload = json.dumps({
        "token": {
            "access_token": access_token,
            "token_type": "Bearer",
            "refresh_token": refresh_token,
            "expiry": expiry_str,
        },
        "auth_method": "consumer",
    }).encode("utf-8")

    # Store to 'login' collection
    try:
        subprocess.run(
            [
                "secret-tool", "store", "--collection=login",
                "--label=Password for 'antigravity' on 'gemini'",
                "service", "gemini", "username", "antigravity",
            ],
            input=keyring_payload,
            check=False,
            timeout=6,
        )
    except Exception:
        pass

    # Store to default collection fallback
    try:
        subprocess.run(
            [
                "secret-tool", "store", "--label=gemini",
                "service", "gemini", "username", "antigravity",
            ],
            input=keyring_payload,
            check=False,
            timeout=6,
        )
    except Exception:
        pass

    # 2. Write file-based credentials in ~/.gemini/
    os.makedirs(GEMINI_DIR, exist_ok=True)
    expiry_ms = expiry_ts if expiry_ts > 10_000_000_000 else expiry_ts * 1000

    oauth_creds = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expiry_date": expiry_ms,
        "id_token": token.get("id_token"),
        "scope": "https://www.googleapis.com/auth/userinfo.email openid https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.profile",
    }
    creds_path = os.path.join(GEMINI_DIR, "oauth_creds.json")
    try:
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(oauth_creds, f, indent=2)
        os.chmod(creds_path, 0o600)
    except Exception:
        pass

    google_accounts = {"active": email, "old": []}
    accounts_path = os.path.join(GEMINI_DIR, "google_accounts.json")
    try:
        with open(accounts_path, "w", encoding="utf-8") as f:
            json.dump(google_accounts, f, indent=2)
        os.chmod(accounts_path, 0o600)
    except Exception:
        pass

    # 3. Update ~/.antigravity_tools/accounts.json index
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            idx = json.load(f)
        idx["current_account_id"] = account_id
        tmp_idx = INDEX_PATH + ".tmp"
        with open(tmp_idx, "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=2)
        os.replace(tmp_idx, INDEX_PATH)
    except Exception:
        pass

    acc["last_used"] = int(time.time())
    try:
        with open(account_file, "w", encoding="utf-8") as f:
            json.dump(acc, f, indent=2)
    except Exception:
        pass

    # 4. Cleanly restart Antigravity Electron process
    close_running_antigravity()
    time.sleep(0.5)
    launch_antigravity_clean(target_ide)

    return True, f"Successfully switched to {email}. Antigravity restarted on PC."
