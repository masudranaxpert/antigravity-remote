"""Authoritative state and process detector for Antigravity.

Inspects local filesystem, Google OAuth tokens, and system keyrings
to reliably determine the active account and official remote session.
Pure Python standard library only.
"""
import base64
import glob
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from urllib.parse import quote

HOME = os.path.expanduser("~")
ACCOUNTS_DIR = os.path.join(HOME, ".antigravity_tools", "accounts")
INDEX_PATH = os.path.join(HOME, ".antigravity_tools", "accounts.json")
CONFIG_PATH = os.path.join(HOME, ".gemini", "config", "config.json")
PBTXT_PATH = os.path.join(HOME, ".gemini", "antigravity", "antigravity_state.pbtxt")
OAUTH_CREDS_PATH = os.path.join(HOME, ".gemini", "oauth_creds.json")
GOOGLE_ACCOUNTS_PATH = os.path.join(HOME, ".gemini", "google_accounts.json")
APP_STORAGE_PATH = os.path.join(HOME, ".config", "Antigravity", "app_storage.json")
LOG_PATH = os.path.join(HOME, ".config", "Antigravity", "logs", "language_server.log")


def decode_jwt_claims(token):
    """Decode JWT payload claims without third-party dependencies."""
    if not token or "." not in token:
        return {}
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload = parts[1]
            rem = len(payload) % 4
            if rem > 0:
                payload += "=" * (4 - rem)
            raw = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
            return json.loads(raw)
    except Exception:
        pass
    return {}


def get_live_antigravity_identity():
    """Detect currently active Google identity from layered system truth.

    ponytail: queries Google OAuth JWT first, then accounts index, GNOME
    keyring, and app onboarding state for 100% ground-truth resolution.
    """
    # 1. Primary: JWT claims from ~/.gemini/oauth_creds.json
    if os.path.exists(OAUTH_CREDS_PATH):
        try:
            with open(OAUTH_CREDS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            claims = decode_jwt_claims(d.get("id_token"))
            email = claims.get("email")
            name = claims.get("name")
            if email:
                return email.strip(), (name or "").strip(), "oauth_jwt"
        except Exception:
            pass

    # 2. Secondary: ~/.gemini/google_accounts.json
    if os.path.exists(GOOGLE_ACCOUNTS_PATH):
        try:
            with open(GOOGLE_ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            email = d.get("active")
            if email and isinstance(email, str) and email.strip():
                return email.strip(), "", "google_accounts"
        except Exception:
            pass

    # 3. Tertiary: Secret Service (GNOME Keyring) matching
    try:
        res = subprocess.run(
            ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout:
            kr_data = json.loads(res.stdout)
            kr_rt = kr_data.get("token", {}).get("refresh_token")
            if kr_rt:
                for p in glob.glob(os.path.join(ACCOUNTS_DIR, "*.json")):
                    with open(p, "r", encoding="utf-8") as af:
                        acc = json.load(af)
                    if acc.get("token", {}).get("refresh_token") == kr_rt:
                        return acc.get("email", "").strip(), acc.get("name", "").strip(), "keyring"
    except Exception:
        pass

    # 4. Quaternary: App onboarding storage fallback
    if os.path.exists(APP_STORAGE_PATH):
        try:
            with open(APP_STORAGE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            email = d.get("jetski.onboarding.lastLoginUsername")
            if email and isinstance(email, str) and email.strip():
                return email.strip(), "", "app_storage"
        except Exception:
            pass

    return None, None, "unauthenticated"


def get_official_remote_info(active_email=None):
    """Resolve official Google Antigravity Remote Control details from local state."""
    uuid = None
    if os.path.exists(PBTXT_PATH):
        try:
            with open(PBTXT_PATH, "r", encoding="utf-8") as f:
                m = re.search(r'installation_uuid:\s*"([^"]+)"', f.read())
                if m:
                    uuid = m.group(1).strip()
        except Exception:
            pass

    if not uuid and os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                m = re.search(r"remote-control-([a-f0-9-]+)-v2", f.read())
                if m:
                    uuid = m.group(1).strip()
        except Exception:
            pass

    device_name = "Host Machine"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                device_name = cfg.get("userSettings", {}).get("remoteControlHostname", device_name)
        except Exception:
            pass

    if not uuid:
        return {"url": "", "device_name": device_name, "uuid": None}

    target_url = f"https://antigravity.google.com/r/{uuid}-v2"
    if active_email:
        final_url = f"https://accounts.google.com/AccountChooser?Email={quote(active_email)}&continue={quote(target_url, safe='')}"
    else:
        final_url = target_url

    return {
        "url": final_url,
        "device_name": device_name,
        "uuid": uuid,
    }


def summarize_quotas(models):
    """Aggregate raw model metrics into distinct Gemini and Claude quota pools."""
    gemini = None
    claude = None

    for m in (models or []):
        name = (m.get("name") or "").lower()
        pct = m.get("percentage", 0)
        reset = m.get("reset_time")

        if "claude" in name:
            if claude is None or "sonnet" in name:
                claude = {"name": "Claude", "percentage": pct, "reset_time": reset}
        elif "gemini" in name:
            if gemini is None or "pro" in name:
                gemini = {"name": "Gemini", "percentage": pct, "reset_time": reset}

    if not gemini and models:
        for m in models:
            if "claude" not in (m.get("name") or "").lower():
                gemini = {"name": "Gemini", "percentage": m.get("percentage", 0), "reset_time": m.get("reset_time")}
                break

    gem_res = gemini or {"name": "Gemini", "percentage": 0, "reset_time": None}
    cld_res = claude or {"name": "Claude", "percentage": 0, "reset_time": None}
    return {"gemini": gem_res, "claude": cld_res}


def load_accounts():
    """Load PRO accounts and match active host account from authoritative identity."""
    active_email, active_name, _ = get_live_antigravity_identity()

    current_account_id = None
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                current_account_id = json.load(f).get("current_account_id")
        except Exception:
            pass

    account_files = glob.glob(os.path.join(ACCOUNTS_DIR, "*.json"))
    accounts = []
    matched_current_id = None

    for filepath in account_files:
        try:
            with open(filepath, "r", encoding="utf-8") as af:
                acc = json.load(af)

            if acc.get("disabled"):
                continue

            quota = acc.get("quota") or {}
            tier = (quota.get("subscription_tier") or "").upper()
            if tier != "PRO":
                continue

            is_current = False
            if active_email and acc.get("email") == active_email:
                is_current = True
            elif not active_email and current_account_id and acc.get("id") == current_account_id:
                is_current = True

            if is_current:
                matched_current_id = acc.get("id")

            summary = summarize_quotas(quota.get("models", []))
            accounts.append({
                "id": acc["id"],
                "email": acc["email"],
                "name": acc.get("name") or (active_name if is_current and active_name else ""),
                "is_current": is_current,
                "tier": tier,
                "quotas": summary,
                "models": [summary["gemini"], summary["claude"]],
            })
        except Exception:
            continue

    # Keep accounts.json index synchronized with host truth
    if matched_current_id and current_account_id != matched_current_id:
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                idx = json.load(f)
            idx["current_account_id"] = matched_current_id
            tmp_idx = INDEX_PATH + ".tmp"
            with open(tmp_idx, "w", encoding="utf-8") as f:
                json.dump(idx, f, indent=2)
            os.replace(tmp_idx, INDEX_PATH)
        except Exception:
            pass

    accounts.sort(key=lambda a: (not a["is_current"], a["email"]))
    return accounts, active_email, matched_current_id


def is_antigravity_running():
    """Detect if the Antigravity desktop IDE Electron process is currently active."""
    for pid_path in glob.glob("/proc/[0-9]*"):
        try:
            exe = os.path.realpath(os.path.join(pid_path, "exe"))
            if ("opt/antigravity/antigravity" in exe or "antigravity-ide" in exe) and "antigravity-tools" not in exe:
                cmdline_path = os.path.join(pid_path, "cmdline")
                with open(cmdline_path, "rb") as f:
                    cmdline = f.read().decode("utf-8", errors="ignore")
                if "--type=" not in cmdline and "language_server" not in cmdline:
                    return True
        except Exception:
            continue
    return False


def is_daemon_alive():
    """Check if Antigravity Manager daemon on port 8045 is responding."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8045/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False
