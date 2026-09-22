"""HTTP API server and static file provider for Antigravity Mobile Switcher.

Serves the mobile web dashboard and coordinates account switching using
direct filesystem truth and Secret Service credentials.
Features cryptographic device-bound session security (anti-cookie-theft),
access audit logging in log/ip.json (30-day retention), and terminal killswitch.
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
from app.terminal import TerminalSession, compute_accept_key

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
STATIC_PATH = os.path.join(BASE_DIR, "static.json")
DEFAULT_PORT = 8077


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
    """Compute normalized SHA-256 fingerprint hash for device binding."""
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
    """Verify session token signature, expiration (30 days), and device binding."""
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

    # Expire after 30 days
    if time.time() - epoch > 30 * 86400:
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

    server_version = "AGRemote/3.1"

    def is_authenticated(self):
        """Verify request authenticity via constant-time token comparison and device binding."""
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
        """Read and transmit a local file with proper MIME headers and security flags."""
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
                    f"mrt={set_cookie_token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax",
                )
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
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
                auth_file = os.path.join(TEMPLATES_DIR, "auth.html")
                return self.send_file(auth_file, "text/html; charset=utf-8")

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
                record_client_access(self.headers, self.client_address, path, "WS", 403, True, "terminal_disabled")
                return self.send_json({"error": "terminal_disabled", "message": "Terminal access is disabled."}, 403)

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

            session = TerminalSession(self.connection, rows=rows, cols=cols)
            self.close_connection = True
            session.start()
            return

        if path == "/api/state":
            authed, st, _, reason = self.is_authenticated()
            if not authed:
                record_client_access(self.headers, self.client_address, path, "GET", 401, False, reason)
                return self.send_json({"need_login": True}, 401)

            record_client_access(self.headers, self.client_address, path, "GET", 200, True, "ok")
            accounts, current_email, current_id = load_accounts()
            remote_info = get_official_remote_info(current_email)
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
            })

        if path == "/api/audit/logs":
            authed, st, _, _ = self.is_authenticated()
            if not authed:
                return self.send_json({"error": "unauthorized"}, 401)
            return self.send_json(load_ip_log())

        self.send_json({"error": "not_found"}, 404)

    def do_POST(self):
        """Route POST mutations for account switching and remote configuration."""
        path = urlparse(self.path).path
        authed, st, _, reason = self.is_authenticated()
        if not authed:
            record_client_access(self.headers, self.client_address, path, "POST", 401, False, reason)
            return self.send_json({"error": "unauthorized"}, 401)

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 1024 * 1024:
                return self.send_json({"error": "payload_too_large"}, 413)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            body = {}

        if path == "/api/switch":
            account_id = body.get("account_id", "").strip()
            target_ide = body.get("target_ide", "classic").strip()
            if not account_id:
                return self.send_json({"success": False, "error": "missing account_id"}, 400)

            success, msg = apply_switch(account_id, target_ide)
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
