"""HTTP API server and static file provider for Antigravity Mobile Switcher.

Serves the mobile web dashboard and coordinates account switching using
direct filesystem truth and Secret Service credentials.
Pure Python standard library only.
"""
import hmac
import json
import mimetypes
import os
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.detector import (
    get_host_audio_status,
    get_official_remote_info,
    is_antigravity_running,
    is_daemon_alive,
    load_accounts,
)
from app.switcher import apply_switch, launch_antigravity_clean, toggle_host_audio_mute

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
DEFAULT_PORT = 8077


import secrets


def load_state():
    """Load persistent mobile token and saved settings, auto-generating token if missing."""
    if not os.path.exists(STATE_PATH):
        init_state = {
            "mobile_token": secrets.token_urlsafe(24),
            "remote_url": ""
        }
        save_state(init_state)
        return init_state
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not data.get("mobile_token"):
                data["mobile_token"] = secrets.token_urlsafe(24)
                save_state(data)
            return data
    except Exception:
        return {"mobile_token": secrets.token_urlsafe(24), "remote_url": ""}


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


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request router for the mobile dashboard and direct API."""

    server_version = "AGRemote/3.0"

    def is_authenticated(self):
        """Verify request authenticity via constant-time token comparison."""
        st = load_state()
        token = st.get("mobile_token", "")
        if not token:
            token = secrets.token_urlsafe(24)
            st["mobile_token"] = token
            save_state(st)

        query = parse_qs(urlparse(self.path).query)
        token_param = query.get("token", [""])[0]
        if token_param and hmac.compare_digest(token_param, token):
            return True, st, True

        cookie_header = self.headers.get("Cookie", "")
        if cookie_header:
            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
                if "mrt" in cookie and hmac.compare_digest(cookie["mrt"].value, token):
                    return True, st, False
            except Exception:
                pass

        return False, st, False

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
        """Read and transmit a local file with proper MIME headers."""
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
                    f"mrt={set_cookie_token}; Path=/; Max-Age=31536000; SameSite=Lax",
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
            authed, st, from_query = self.is_authenticated()
            if not authed:
                auth_file = os.path.join(TEMPLATES_DIR, "auth.html")
                return self.send_file(auth_file, "text/html; charset=utf-8")

            cookie_to_set = st.get("mobile_token") if from_query else None
            html_file = os.path.join(TEMPLATES_DIR, "index.html")
            return self.send_file(html_file, "text/html; charset=utf-8", set_cookie_token=cookie_to_set)

        if path == "/api/state":
            authed, st, _ = self.is_authenticated()
            if not authed:
                return self.send_json({"need_login": True}, 401)

            accounts, current_email, current_id = load_accounts()
            remote_info = get_official_remote_info(current_email)
            remote_url = st.get("remote_url") or remote_info.get("url", "")

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
            })

        self.send_json({"error": "not_found"}, 404)

    def do_POST(self):
        """Route POST mutations for account switching and remote configuration."""
        path = urlparse(self.path).path
        authed, st, _ = self.is_authenticated()
        if not authed:
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
            return self.send_json({
                "success": success,
                "message": msg if success else None,
                "error": msg if not success else None,
            }, 200)

        if path == "/api/launch":
            target_ide = body.get("target_ide", "classic").strip()
            launch_antigravity_clean(target_ide)
            return self.send_json({
                "success": True,
                "antigravity_running": is_antigravity_running(),
            })

        if path == "/api/remote-url":
            st["remote_url"] = body.get("url", "").strip()
            save_state(st)
            return self.send_json({"success": True})

        if path == "/api/audio/mute":
            toggle_host_audio_mute()
            return self.send_json({
                "success": True,
                "audio": get_host_audio_status(),
            })

        self.send_json({"error": "not_found"}, 404)

    def log_message(self, format, *args):
        """Format and log incoming HTTP requests to terminal stdout."""
        import datetime
        now = datetime.datetime.now().strftime("%H:%M:%S")
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


def run_server(port=DEFAULT_PORT):
    """Start threaded HTTP server listening on localhost."""
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Antigravity Mobile Switcher running on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    run_server()
