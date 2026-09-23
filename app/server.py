"""HTTP API server and static file provider for Antigravity Mobile Switcher.

Serves the mobile web dashboard and coordinates account switching using
direct filesystem truth and Secret Service credentials.
Features cryptographic device-bound session security (anti-cookie-theft),
24-hour session lifetime, optional RFC 6238 TOTP 2FA, access audit logging
in log/ip.json (30-day retention), and terminal killswitch.
Pure Python standard library only.
"""
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.audit import load_ip_log, record_client_access
from app.detector import (
    get_host_audio_status,
    get_official_remote_info,
    is_antigravity_running,
    is_daemon_alive,
    load_accounts,
)
from app.switcher import (
    apply_switch,
    get_sleep_inhibit_status,
    launch_antigravity_clean,
    set_sleep_inhibit,
    toggle_host_audio_mute,
)
from app.terminal import TerminalSession, close_all_terminal_sessions, compute_accept_key
from app.totp import (
    compute_totp,
    generate_totp_secret,
    get_totp_uri,
    verify_totp,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
STATIC_PATH = os.path.join(BASE_DIR, "static.json")
DEFAULT_PORT = 8077
SESSION_MAX_AGE = 86400  # 24-hour session lifetime in seconds

_POWER_SUPPLY = "/sys/class/power_supply"


def get_battery_status():
    """Read battery percentage, charging state, and AC plug status from sysfs."""
    try:
        entries = os.listdir(_POWER_SUPPLY)
    except OSError:
        return None
    bat = next((e for e in sorted(entries) if e.startswith("BAT")), None)
    if not bat:
        return None
    base = os.path.join(_POWER_SUPPLY, bat)
    try:
        pct = int(open(os.path.join(base, "capacity")).read().strip())
        status = open(os.path.join(base, "status")).read().strip()  # Charging/Discharging/Full/Unknown
        # AC adapter: status=Full while plugged in is common on Linux — check separately
        plugged = any(
            open(os.path.join(_POWER_SUPPLY, e, "online")).read().strip() == "1"
            for e in entries if e.startswith("AC")
            if os.path.exists(os.path.join(_POWER_SUPPLY, e, "online"))
        )
        return {"percent": pct, "status": status, "plugged": plugged}
    except Exception:
        return None


QUOTA_CACHE_TTL = 180  # 3-minute quota cache lifetime in seconds
_QUOTA_CACHE = {
    "accounts": None,
    "current_email": None,
    "current_id": None,
    "remote_info": None,
    "timestamp": 0,
}


def get_cached_accounts_and_remote(force_refresh=False):
    """Retrieve accounts quota and remote info from 3-minute in-memory cache, or refresh."""
    now = time.time()
    is_expired = (now - _QUOTA_CACHE["timestamp"]) >= QUOTA_CACHE_TTL
    is_empty = _QUOTA_CACHE["accounts"] is None
    if force_refresh or is_empty or is_expired:
        accounts, current_email, current_id = load_accounts()
        remote_info = get_official_remote_info(current_email)
        _QUOTA_CACHE["accounts"] = accounts
        _QUOTA_CACHE["current_email"] = current_email
        _QUOTA_CACHE["current_id"] = current_id
        _QUOTA_CACHE["remote_info"] = remote_info
        _QUOTA_CACHE["timestamp"] = now
        cached = False
    else:
        accounts = _QUOTA_CACHE["accounts"]
        current_email = _QUOTA_CACHE["current_email"]
        current_id = _QUOTA_CACHE["current_id"]
        remote_info = _QUOTA_CACHE["remote_info"]
        cached = True

    return accounts, current_email, current_id, remote_info, int(_QUOTA_CACHE["timestamp"]), cached


def invalidate_quota_cache():
    """Invalidate in-memory quota cache to force fresh read on next request."""
    _QUOTA_CACHE["timestamp"] = 0
    _QUOTA_CACHE["accounts"] = None


def load_state():
    """Load persistent mobile token and settings from state.json with static.json fallback."""
    data = {}
    if os.path.exists(STATE_PATH):
        try:
            os.chmod(STATE_PATH, 0o600)
        except OSError:
            pass
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:
            data = {}

    # Check optional static.json configuration overrides
    if os.path.exists(STATIC_PATH):
        try:
            with open(STATIC_PATH, "r", encoding="utf-8") as f:
                static_cfg = json.load(f)
                if isinstance(static_cfg, dict):
                    data.update(static_cfg)
        except Exception:
            pass

    updated = False
    if not data.get("mobile_token"):
        data["mobile_token"] = secrets.token_urlsafe(24)
        updated = True
    if "prevent_sleep" not in data:
        data["prevent_sleep"] = True
        updated = True
    if "terminal_enabled" not in data:
        data["terminal_enabled"] = True
        updated = True
    if "totp_enabled" not in data:
        data["totp_enabled"] = False
        updated = True
    if not data.get("totp_secret"):
        data["totp_secret"] = generate_totp_secret()
        updated = True

    if updated:
        save_state(data)
    return data


def save_state(state):
    """Atomically persist state configuration with strict owner permissions."""
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, STATE_PATH)
    try:
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass


def compute_client_fingerprint(headers) -> str:
    """Compute normalized SHA-256 fingerprint hash for device binding.

    Uses User-Agent which is reliably present and identical across standard
    HTTP GET/POST requests and RFC 6455 WebSocket upgrade handshakes.
    """
    ua = headers.get("User-Agent", "").strip()
    return hashlib.sha256(ua.encode("utf-8")).hexdigest()[:16]


def compute_legacy_fingerprint(headers) -> str:
    """Compute legacy fingerprint for seamless migration of active sessions."""
    ua = headers.get("User-Agent", "").strip()
    lang = headers.get("Accept-Language", "").strip().split(",")[0]
    platform = headers.get("Sec-Ch-Ua-Platform", "").strip()
    raw = f"{ua}|{lang}|{platform}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def generate_session_token(master_token: str, fingerprint: str) -> str:
    """Generate cryptographically signed, device-bound ephemeral session token."""
    now_epoch = int(time.time())
    payload = f"v1:{now_epoch}:{fingerprint}"
    sig = hmac.new(master_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"v1.{now_epoch}.{fingerprint}.{sig}"


def verify_session_token(token_str: str, master_token: str, current_fingerprint: str) -> tuple:
    """Verify session token signature, 24-hour expiration, and device binding."""
    if not token_str:
        return False, "missing_token"

    # Backward compatibility: accept raw master token if matching directly
    if hmac.compare_digest(token_str, master_token):
        return True, "legacy_master_match"

    parts = token_str.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return False, "malformed_session_token"

    _, epoch_str, bound_fp, sig = parts
    try:
        epoch = int(epoch_str)
    except ValueError:
        return False, "invalid_timestamp"

    # Enforce strict 24-hour session lifetime
    if time.time() - epoch > SESSION_MAX_AGE:
        return False, "session_expired"

    # Verify HMAC signature against master secret
    payload = f"v1:{epoch_str}:{bound_fp}"
    expected_sig = hmac.new(master_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False, "invalid_signature"

    # Anti-theft: Ensure the cookie is used from the exact presenting device/browser
    if not hmac.compare_digest(bound_fp, current_fingerprint):
        return False, "device_fingerprint_mismatch"

    return True, "ok"


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request router for the mobile dashboard and direct API."""

    server_version = "AGRemote/3.2"

    def is_authenticated(self):
        """Verify request authenticity via constant-time token comparison, device binding, and 2FA."""
        st = load_state()
        master_token = st.get("mobile_token", "")
        if not master_token:
            master_token = secrets.token_urlsafe(24)
            st["mobile_token"] = master_token
            save_state(st)

        current_fp = compute_client_fingerprint(self.headers)

        # 1. Master token in query parameter (?token=...)
        query = parse_qs(urlparse(self.path).query)
        token_param = query.get("token", [""])[0]
        if token_param and hmac.compare_digest(token_param, master_token):
            # Check if 2FA (TOTP) is enforced
            if st.get("totp_enabled", False):
                otp_param = query.get("otp", [""])[0]
                if otp_param and verify_totp(st.get("totp_secret", ""), otp_param):
                    session_cookie = generate_session_token(master_token, current_fp)
                    return True, st, session_cookie, "query_token_and_totp"
                else:
                    return False, st, None, "totp_required"
            else:
                session_cookie = generate_session_token(master_token, current_fp)
                return True, st, session_cookie, "query_master_token"

        # 2. Device-bound session cookie
        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
                if "mrt" in cookie:
                    cookie_val = cookie["mrt"].value
                    valid, reason = verify_session_token(cookie_val, master_token, current_fp)
                    if not valid and reason == "device_fingerprint_mismatch":
                        # Attempt transparent migration from legacy multi-header fingerprint
                        legacy_fp = compute_legacy_fingerprint(self.headers)
                        if legacy_fp != current_fp:
                            valid_leg, reason_leg = verify_session_token(cookie_val, master_token, legacy_fp)
                            if valid_leg:
                                upgraded_token = generate_session_token(master_token, current_fp)
                                return True, st, upgraded_token, "cookie_migrated"
                    if valid:
                        return True, st, None, f"cookie_{reason}"
                    else:
                        record_client_access(
                            self.headers,
                            self.client_address,
                            self.path,
                            self.command,
                            401,
                            False,
                            auth_reason=f"cookie_rejected_{reason}",
                        )
                        return False, st, None, f"cookie_rejected_{reason}"
            except Exception:
                pass

        return False, st, None, "unauthenticated"

    def send_json(self, obj, code=200):
        """Serialize and transmit JSON response."""
        data = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def send_file(self, file_path, content_type, set_cookie_token=None):
        """Read and transmit a local file with proper MIME headers and 24h security flags."""
        if not os.path.exists(file_path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Resource not found")
            return

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            if set_cookie_token:
                self.send_header(
                    "Set-Cookie",
                    f"mrt={set_cookie_token}; Path=/; Max-Age={SESSION_MAX_AGE}; HttpOnly; SameSite=Lax",
                )
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def send_auth_page(self, st):
        """Serve auth.html with injected TOTP state and strict no-cache headers."""
        auth_file = os.path.join(TEMPLATES_DIR, "auth.html")
        if not os.path.exists(auth_file):
            return self.send_json({"error": "Auth page missing"}, 500)
        try:
            with open(auth_file, "r", encoding="utf-8") as f:
                content = f.read()
            totp_val = "true" if st.get("totp_enabled", False) else "false"
            content = content.replace("/*__SERVER_TOTP__*/false", totp_val)
            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        """Route GET requests for static assets, templates, and state endpoints."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            return self.send_json({
                "ok": True,
                "daemon_alive": is_daemon_alive(),
                "antigravity_running": is_antigravity_running(),
            })

        if path.startswith("/static/"):
            if path.endswith(".map"):
                self.send_response(204)
                self.end_headers()
                return

            rel_path = path[len("/static/"):].lstrip("/")
            safe_path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
            if not (safe_path == STATIC_DIR or safe_path.startswith(STATIC_DIR + os.sep)) or not os.path.isfile(safe_path):
                self.send_response(404)
                self.end_headers()
                return

            ctype, _ = mimetypes.guess_type(safe_path)
            if safe_path.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif safe_path.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            return self.send_file(safe_path, ctype or "application/octet-stream")

        if path == "/api/auth/status":
            st = load_state()
            return self.send_json({
                "totp_enabled": bool(st.get("totp_enabled", False)),
            })

        if path == "/logout":
            record_client_access(self.headers, self.client_address, path, "GET", 302, True, "user_logout")
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                "mrt=deleted; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax",
            )
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            return

        if path == "/":
            authed, st, new_cookie, reason = self.is_authenticated()
            record_client_access(
                self.headers,
                self.client_address,
                path,
                "GET",
                200 if authed else 401,
                authed,
                auth_reason=reason,
            )
            if not authed:
                return self.send_auth_page(st)

            html_file = os.path.join(TEMPLATES_DIR, "index.html")
            return self.send_file(html_file, "text/html; charset=utf-8", set_cookie_token=new_cookie)

        if path == "/terminal":
            authed, st, new_cookie, reason = self.is_authenticated()
            if not authed:
                record_client_access(self.headers, self.client_address, path, "GET", 401, False, reason)
                auth_file = os.path.join(TEMPLATES_DIR, "auth.html")
                return self.send_file(auth_file, "text/html; charset=utf-8")

            # Enforce administrator terminal killswitch
            if not st.get("terminal_enabled", True):
                record_client_access(self.headers, self.client_address, path, "GET", 403, True, "terminal_disabled")
                self.send_response(403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<!DOCTYPE html><html><body style='font-family:sans-serif;background:#0d0f12;color:#f87171;display:flex;align-items:center;justify-content:center;height:100vh;'><div style='text-align:center;'><h2>403 Forbidden</h2><p>Terminal access is disabled by administrator.</p><p><a href='/' style='color:#38bdf8;'>Back to Dashboard</a></p></div></body></html>")
                return

            record_client_access(self.headers, self.client_address, path, "GET", 200, True, reason)
            html_file = os.path.join(TEMPLATES_DIR, "terminal.html")
            return self.send_file(html_file, "text/html; charset=utf-8", set_cookie_token=new_cookie)

        if path == "/api/terminal/ws":
            authed, st, _, reason = self.is_authenticated()
            if not authed:
                record_client_access(self.headers, self.client_address, path, "WS", 401, False, reason)
                return self.send_json({"error": "unauthorized"}, 401)

            if not st.get("terminal_enabled", True):
                close_all_terminal_sessions()
                record_client_access(self.headers, self.client_address, path, "WS", 403, True, "terminal_disabled")
                return self.send_json({"error": "terminal_disabled", "message": "Terminal access is disabled."}, 403)

            # Issue 28: Origin validation to prevent Cross-Site WebSocket Hijacking
            origin = self.headers.get("Origin", "").strip()
            if origin:
                origin_host = urlparse(origin).netloc.split(":")[0].lower()
                host_header = self.headers.get("Host", "").split(":")[0].lower()
                allowed_hosts = {host_header, "localhost", "127.0.0.1"}
                if origin_host not in allowed_hosts and not origin_host.endswith(".masud-rana.me"):
                    record_client_access(self.headers, self.client_address, path, "WS", 403, False, "origin_rejected")
                    return self.send_json({"error": "forbidden_origin"}, 403)

            record_client_access(self.headers, self.client_address, path, "WS", 101, True, "websocket_upgrade")
            ws_key = self.headers.get("Sec-WebSocket-Key", "").strip()
            if not ws_key:
                return self.send_json({"error": "bad_websocket_request"}, 400)

            accept_key = compute_accept_key(ws_key)
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key}\r\n"
                "\r\n"
            )
            try:
                self.wfile.write(response.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

            query = parse_qs(urlparse(self.path).query)
            try:
                rows = int(query.get("rows", [24])[0])
                cols = int(query.get("cols", [80])[0])
            except Exception:
                rows, cols = 24, 80

            session_id = query.get("session_id", [None])[0]
            session = TerminalSession(self.connection, rows=rows, cols=cols, session_id=session_id)
            self.close_connection = True
            session.start()
            return

        if path == "/api/state":
            authed, st, _, reason = self.is_authenticated()
            if not authed:
                record_client_access(self.headers, self.client_address, path, "GET", 401, False, reason)
                return self.send_json({"need_login": True}, 401)

            record_client_access(self.headers, self.client_address, path, "GET", 200, True, "ok")
            query = parse_qs(parsed.query)
            force_refresh = (
                query.get("refresh_quota", ["0"])[0] in ("1", "true")
                or query.get("force_refresh", ["0"])[0] in ("1", "true")
            )
            accounts, current_email, current_id, remote_info, quota_ts, was_cached = get_cached_accounts_and_remote(
                force_refresh=force_refresh
            )
            remote_url = remote_info.get("url", "")

            # Reconcile sleep inhibitor with desired state configuration
            desired_sleep = bool(st.get("prevent_sleep", True))
            if desired_sleep and not get_sleep_inhibit_status():
                set_sleep_inhibit(True)
            elif not desired_sleep and get_sleep_inhibit_status():
                set_sleep_inhibit(False)

            return self.send_json({
                "accounts": accounts,
                "current": current_email,
                "current_id": current_id,
                "remote_url": remote_url,
                "remote_device_name": remote_info.get("device_name", "Host PC"),
                "source": "direct_filesystem",
                "daemon_alive": is_daemon_alive(),
                "antigravity_running": is_antigravity_running(),
                "audio": get_host_audio_status(),
                "prevent_sleep": get_sleep_inhibit_status(),
                "terminal_enabled": bool(st.get("terminal_enabled", True)),
                "totp_enabled": bool(st.get("totp_enabled", False)),
                "battery": get_battery_status(),
                "quota_cached": was_cached,
                "quota_updated_at": quota_ts,
            })

        if path == "/api/audit/logs":
            authed, st, _, _ = self.is_authenticated()
            if not authed:
                return self.send_json({"error": "unauthorized"}, 401)
            return self.send_json(load_ip_log())

        self.send_json({"error": "not_found"}, 404)

    def do_POST(self):
        """Route POST mutations for account switching, authentication, and remote configuration."""
        path = urlparse(self.path).path

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 1024 * 1024:
                return self.send_json({"error": "payload_too_large"}, 413)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            body = {}

        # Public 2FA / Login Verification Endpoint
        if path == "/api/auth/verify":
            st = load_state()
            token = body.get("token", "").strip()
            otp = body.get("otp", "").strip()
            master_token = st.get("mobile_token", "")

            if not token or not hmac.compare_digest(token, master_token):
                record_client_access(self.headers, self.client_address, path, "POST", 401, False, "invalid_master_token")
                return self.send_json({"success": False, "error": "Invalid authorization token."}, 401)

            if st.get("totp_enabled", False):
                secret = st.get("totp_secret", "")
                if not otp:
                    record_client_access(self.headers, self.client_address, path, "POST", 401, False, "missing_totp_code")
                    return self.send_json({
                        "success": False,
                        "error": "2-Factor Authentication active. Please enter the 6-digit Authenticator code.",
                        "totp_required": True,
                    }, 401)

                if not verify_totp(secret, otp, window=2):
                    record_client_access(self.headers, self.client_address, path, "POST", 401, False, "invalid_totp_code")
                    return self.send_json({
                        "success": False,
                        "error": "Invalid 6-digit Authenticator code. Check your Google Authenticator app.",
                        "totp_required": True,
                    }, 401)

            current_fp = compute_client_fingerprint(self.headers)
            session_cookie = generate_session_token(master_token, current_fp)
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, "login_success")

            data = json.dumps({"success": True, "token": session_cookie}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Set-Cookie",
                f"mrt={session_cookie}; Path=/; Max-Age={SESSION_MAX_AGE}; HttpOnly; SameSite=Lax",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/auth/logout":
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, "user_logout_api")
            data = json.dumps({"success": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Set-Cookie",
                "mrt=deleted; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax",
            )
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
            return

        authed, st, _, reason = self.is_authenticated()
        if not authed:
            record_client_access(self.headers, self.client_address, path, "POST", 401, False, reason)
            return self.send_json({"error": "unauthorized"}, 401)

        if path == "/api/totp/setup":
            secret = st.get("totp_secret", "")
            if not secret:
                secret = generate_totp_secret()
                st["totp_secret"] = secret
                save_state(st)
            uri = get_totp_uri(secret, issuer="Antigravity Remote", account_name="masud")
            return self.send_json({
                "enabled": bool(st.get("totp_enabled", False)),
                "secret": secret,
                "uri": uri,
            })

        if path == "/api/totp/toggle":
            enable = bool(body.get("enable", False))
            if enable:
                otp = body.get("otp", "").strip()
                secret = st.get("totp_secret", "")
                if not verify_totp(secret, otp):
                    return self.send_json({"success": False, "error": "Invalid code. Please check your Authenticator app."}, 400)
            st["totp_enabled"] = enable
            save_state(st)
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, f"totp_toggled_{enable}")
            return self.send_json({
                "success": True,
                "totp_enabled": enable,
            })

        if path == "/api/switch":
            account_id = body.get("account_id", "").strip()
            target_ide = body.get("target_ide", "classic").strip()
            if not account_id:
                return self.send_json({"success": False, "error": "missing account_id"}, 400)

            success, msg = apply_switch(account_id, target_ide)
            if success:
                invalidate_quota_cache()
            record_client_access(self.headers, self.client_address, path, "POST", 200 if success else 500, True, f"switch_account_{account_id}")
            return self.send_json({
                "success": success,
                "message": msg if success else None,
                "error": msg if not success else None,
            }, 200)

        if path == "/api/launch":
            target_ide = body.get("target_ide", "classic").strip()
            launch_antigravity_clean(target_ide)
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, f"launch_{target_ide}")
            return self.send_json({
                "success": True,
                "antigravity_running": is_antigravity_running(),
            })

        if path == "/api/audio/mute":
            toggle_host_audio_mute()
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, "toggle_audio_mute")
            return self.send_json({
                "success": True,
                "audio": get_host_audio_status(),
            })

        if path == "/api/sleep/toggle":
            st_cur = load_state()
            new_val = not st_cur.get("prevent_sleep", True)
            st_cur["prevent_sleep"] = new_val
            save_state(st_cur)
            set_sleep_inhibit(new_val)
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, f"toggle_sleep_{new_val}")
            return self.send_json({
                "success": True,
                "prevent_sleep": new_val,
                "active": get_sleep_inhibit_status(),
            })

        if path == "/api/terminal/toggle":
            st_cur = load_state()
            new_val = not st_cur.get("terminal_enabled", True)
            st_cur["terminal_enabled"] = new_val
            save_state(st_cur)
            if not new_val:
                close_all_terminal_sessions()
            record_client_access(self.headers, self.client_address, path, "POST", 200, True, f"toggle_terminal_{new_val}")
            return self.send_json({
                "success": True,
                "terminal_enabled": new_val,
            })

        self.send_json({"error": "not_found"}, 404)

    def log_message(self, format, *args):
        """Format and log incoming HTTP requests to terminal stdout."""
        now = datetime.datetime.now().strftime("%I:%M:%S %p")
        req = args[0] if len(args) > 0 else ""
        status = args[1] if len(args) > 1 else ""
        if str(status).startswith("2"):
            stat_color = "\033[92m"
        elif str(status).startswith("4"):
            stat_color = "\033[93m"
        elif str(status).startswith("5"):
            stat_color = "\033[91m"
        else:
            stat_color = "\033[90m"
        reset = "\033[0m"
        print(f"\033[90m[{now}]\033[0m {req} -> {stat_color}{status}{reset}", flush=True)


def run_server(port=DEFAULT_PORT, init_sleep_inhibit=True):
    """Start threaded HTTP server listening on localhost."""
    st = load_state()
    if init_sleep_inhibit:
        if st.get("prevent_sleep", True):
            set_sleep_inhibit(True)

    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Antigravity Mobile Switcher running on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    run_server()
