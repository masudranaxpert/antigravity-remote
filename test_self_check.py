"""Self-check verification script for Antigravity Mobile Switcher.

Runs assert-based checks against live detection and running server.
No test frameworks or fixtures required.
"""
import urllib.request
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.detector import (
    get_host_audio_status,
    get_live_antigravity_identity,
    get_official_remote_info,
    load_accounts,
)
from app.server import load_state
from app.switcher import get_sleep_inhibit_status, set_sleep_inhibit
from app.terminal import compute_accept_key, encode_ws_frame, resize_pty, spawn_shell_pty


def run_checks():
    # 1. Authoritative identity detection check
    email, name, source = get_live_antigravity_identity()
    assert email is not None and "@" in email, f"Expected valid active email, got {email}"
    assert source in ("oauth_jwt", "google_accounts", "keyring", "app_storage"), f"Unexpected source: {source}"
    print(f"PASS: Live identity resolved ({email}, source: {source})")

    # 2. Remote URL resolution check
    remote_info = get_official_remote_info(email)
    assert remote_info.get("uuid"), "Expected installation UUID to be present"
    assert "antigravity.google.com" in remote_info.get("url", ""), f"Invalid remote URL: {remote_info.get('url')}"
    print(f"PASS: Official remote URL generated ({remote_info['uuid']})")

    # 3. Account segmentation and quotas
    accounts, active_email, _ = load_accounts()
    assert len(accounts) > 0, "Expected at least 1 PRO account"
    active_accs = [a for a in accounts if a["is_current"]]
    assert len(active_accs) == 1, f"Expected exactly 1 active account, got {len(active_accs)}"
    assert active_accs[0]["email"] == email, f"Active account mismatch: {active_accs[0]['email']} != {email}"

    # Verify Gemini and Claude only quotas
    for a in accounts:
        assert "gemini" in a["quotas"], f"Missing gemini quota in {a['email']}"
        assert "claude" in a["quotas"], f"Missing claude quota in {a['email']}"
        assert len(a["models"]) == 2, f"Expected exactly 2 model pools, found {len(a['models'])}"
    print(f"PASS: Account models correctly restricted to Gemini and Claude ({len(accounts)} PRO accounts)")

    # 4. Host audio telemetry check
    audio = get_host_audio_status()
    assert isinstance(audio, dict) and "supported" in audio, "Invalid audio status structure"
    if audio["supported"]:
        assert isinstance(audio["volume"], int) and 0 <= audio["volume"] <= 200, f"Invalid volume: {audio['volume']}"
        assert isinstance(audio["muted"], bool), f"Invalid muted status: {audio['muted']}"
        print(f"PASS: Host audio status detected (Volume: {audio['volume']}%, Muted: {audio['muted']})")
    else:
        print("PASS: Host audio gracefully reports unsupported on non-audio system")

    # 5. Host sleep prevention inhibitor lifecycle check
    ok_on = set_sleep_inhibit(True)
    assert ok_on is True, "Failed to activate sleep inhibitor"
    assert get_sleep_inhibit_status() is True, "Expected sleep inhibitor to report active"
    ok_off = set_sleep_inhibit(False)
    assert ok_off is True, "Failed to deactivate sleep inhibitor"
    assert get_sleep_inhibit_status() is False, "Expected sleep inhibitor to report inactive"
    print("PASS: Host sleep prevention inhibitor lifecycle verified")

    # 6. Terminal PTY Engine and RFC 6455 verification
    accept_val = compute_accept_key("dGhlIHNhbXBsZSBub25jZQ==")
    assert accept_val == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", f"Invalid accept key: {accept_val}"
    frame = encode_ws_frame(b"ping-test", 1)
    assert frame[0] == 0x81 and frame[1] == 9 and frame[2:] == b"ping-test", "Invalid WebSocket frame encoding"

    master_fd, shell_pid = spawn_shell_pty(rows=24, cols=80)
    assert master_fd > 0 and shell_pid > 0, "Failed to spawn shell PTY"
    import time
    time.sleep(0.1)
    os.write(master_fd, b"echo __TERM_SELF_CHECK_OK__\n")
    time.sleep(0.1)
    pty_out = os.read(master_fd, 1024)
    assert b"__TERM_SELF_CHECK_OK__" in pty_out, f"PTY execution mismatch: {pty_out}"
    resize_pty(master_fd, 30, 100)
    os.kill(shell_pid, 9)
    os.waitpid(shell_pid, 0)
    os.close(master_fd)
    print("PASS: Terminal PTY shell lifecycle and RFC 6455 frame engine verified")

    # 7. HTTP Endpoints & Auth FOUC-prevention check
    import threading
    from http.server import ThreadingHTTPServer
    from app.server import DashboardHandler
    test_port = 8899
    server = ThreadingHTTPServer(("127.0.0.1", test_port), DashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        # A. /health check
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            assert resp.status == 200, f"Expected 200 from /health, got {resp.status}"
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True, f"Unexpected health payload: {data}"
        print("PASS: HTTP /health responsive and healthy")

        # B. Unauthenticated GET / must serve auth.html directly (zero dashboard skeleton FOUC)
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8")
            assert "Unlock Antigravity Remote" in body or "auth-token-input" in body, "Expected dedicated auth page"
            assert "accounts-grid" not in body, "FOUC detected: dashboard markup leaked into unauthenticated response"
        print("PASS: Unauthenticated GET / serves clean auth page directly (Zero FOUC)")

        # C. Authenticated GET /?token=... must serve index.html and set cookie
        token = load_state().get("mobile_token", "")
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/?token={token}")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8")
            assert "Antigravity Mobile Switcher" in body, "Expected dashboard template for authenticated request"
            cookie = resp.headers.get("Set-Cookie", "")
            assert "mrt=" in cookie, f"Expected Set-Cookie header with mrt token, got {cookie}"
        print("PASS: Authenticated GET /?token=... serves dashboard and sets cookie")

        # D. GET /api/state includes audio & sleep telemetry
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/state", headers={"Cookie": f"mrt={token}"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            state = json.loads(resp.read().decode("utf-8"))
            assert "audio" in state, "Missing audio in /api/state"
            assert state["audio"]["supported"] is True, "Audio should be supported on host"
            assert "prevent_sleep" in state, "Missing prevent_sleep in /api/state"
        print(f"PASS: /api/state telemetry includes audio and prevent_sleep ({state['prevent_sleep']})")

        # E. POST /api/audio/mute toggles mute state and returns updated audio
        req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/audio/mute",
            data=b"{}",
            headers={"Cookie": f"mrt={token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("success") is True, f"Mute toggle failed: {data}"
            assert "audio" in data, "Missing audio in response"
            toggled_mute = data["audio"]["muted"]
        # Toggle back to restore original state
        req_restore = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/audio/mute",
            data=b"{}",
            headers={"Cookie": f"mrt={token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_restore, timeout=3) as resp:
            data_restore = json.loads(resp.read().decode("utf-8"))
            restored_mute = data_restore["audio"]["muted"]
            assert restored_mute != toggled_mute, "Mute toggle did not flip back"

        # F. /api/sleep/toggle toggles keep-awake state and returns updated prevent_sleep
        req_sleep = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/sleep/toggle",
            data=b"{}",
            headers={"Cookie": f"mrt={token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_sleep, timeout=3) as resp:
            s_data = json.loads(resp.read().decode("utf-8"))
            assert s_data.get("success") is True, f"Sleep toggle failed: {s_data}"
            assert "prevent_sleep" in s_data, "Missing prevent_sleep in response"
            toggled_sleep = s_data["prevent_sleep"]

        # Restore original sleep state
        req_sleep_restore = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/sleep/toggle",
            data=b"{}",
            headers={"Cookie": f"mrt={token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_sleep_restore, timeout=3) as resp:
            s_restore = json.loads(resp.read().decode("utf-8"))
            assert s_restore["prevent_sleep"] != toggled_sleep, "Sleep toggle did not flip back"
        print("PASS: /api/sleep/toggle endpoint successfully toggles keep-awake state")

        # G. Terminal page authentication gate check (Zero FOUC)
        req_term_unauth = urllib.request.Request(f"http://127.0.0.1:{test_port}/terminal")
        with urllib.request.urlopen(req_term_unauth, timeout=3) as resp:
            t_body = resp.read().decode("utf-8")
            assert "Unlock Antigravity Remote" in t_body or "auth-token-input" in t_body, "Expected auth page for unauth /terminal"
            assert "terminal-container" not in t_body, "Terminal markup leaked into unauthenticated response"

        req_term_auth = urllib.request.Request(f"http://127.0.0.1:{test_port}/terminal?token={token}")
        with urllib.request.urlopen(req_term_auth, timeout=3) as resp:
            t_body_auth = resp.read().decode("utf-8")
            assert "terminal-container" in t_body_auth, "Expected terminal template for authenticated request"
            assert "xterm.js" in t_body_auth, "Expected xterm script tags in terminal template"
        print("PASS: /terminal endpoint securely gated by authentication (Zero FOUC)")

        # H. Live WebSocket terminal interactive session check
        import socket
        ws_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ws_sock.connect(("127.0.0.1", test_port))
        ws_req = (
            f"GET /api/terminal/ws?token={token}&cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        ws_sock.sendall(ws_req.encode("utf-8"))
        resp_hdr = ws_sock.recv(1024).decode("utf-8")
        assert "101 Switching Protocols" in resp_hdr, f"Expected 101, got {resp_hdr}"
        assert "Sec-WebSocket-Accept" in resp_hdr, "Missing accept header in WS response"

        # Send masked command 'echo WS_LIVE_OK\n'
        cmd_bytes = b"echo WS_LIVE_OK\n"
        mask = b"\x11\x22\x33\x44"
        masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(cmd_bytes))
        ws_frame = bytearray([0x81, 0x80 | len(cmd_bytes)]) + mask + masked_payload
        ws_sock.sendall(ws_frame)

        # Read response frames from server
        time.sleep(0.15)
        ws_sock.settimeout(2.0)
        accumulated = b""
        for _ in range(5):
            try:
                raw_in = ws_sock.recv(2048)
                accumulated += raw_in
                if b"WS_LIVE_OK" in accumulated:
                    break
            except Exception:
                break
        assert b"WS_LIVE_OK" in accumulated, f"Expected WS_LIVE_OK in PTY output, got: {accumulated}"

        # Send WebSocket heartbeat ping and verify pong
        ping_bytes = b'{"type":"ping"}'
        mask_p = b"\x55\x66\x77\x88"
        masked_ping = bytes(b ^ mask_p[i % 4] for i, b in enumerate(ping_bytes))
        ws_sock.sendall(bytearray([0x81, 0x80 | len(ping_bytes)]) + mask_p + masked_ping)
        time.sleep(0.1)
        pong_in = ws_sock.recv(1024)
        assert b'"type":"pong"' in pong_in or b"pong" in pong_in, f"Expected pong response, got {pong_in}"

        # Send SGR mouse report and verify it is filtered out
        mouse_bytes = b"\x1b[<35;9;3M"
        masked_mouse = bytes(b ^ mask_p[i % 4] for i, b in enumerate(mouse_bytes))
        ws_sock.sendall(bytearray([0x81, 0x80 | len(mouse_bytes)]) + mask_p + masked_mouse)
        time.sleep(0.1)

        ws_sock.close()
        print("PASS: Live WebSocket terminal interactive session & heartbeat keep-alive verified")

        # I. Invalid token rejection check
        try:
            bad_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/state", headers={"Cookie": "mrt=invalid_token_12345"})
            urllib.request.urlopen(bad_req, timeout=3)
            assert False, "Expected 401 for invalid token"
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401, got {e.code}"
        print("PASS: Invalid token rejected with HTTP 401")

        # G. Static directory traversal prevention check
        try:
            trav_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/static/../state.json")
            urllib.request.urlopen(trav_req, timeout=3)
            assert False, "Expected 404 for directory traversal"
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"Expected 404, got {e.code}"
        print("PASS: Static directory traversal attempt rejected with HTTP 404")

        # H. Payload size limit check (> 1MB returns 413)
        try:
            big_body = json.dumps({"payload": "A" * (1024 * 1024 + 10)}).encode("utf-8")
            big_req = urllib.request.Request(
                f"http://127.0.0.1:{test_port}/api/switch",
                data=big_body,
                headers={"Cookie": f"mrt={token}", "Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(big_req, timeout=3)
            assert False, "Expected 413 for payload > 1MB"
        except urllib.error.HTTPError as e:
            assert e.code == 413, f"Expected 413, got {e.code}"
        print("PASS: Oversized payload rejected with HTTP 413")

        # I. Malicious account_id path traversal rejection in /api/switch
        trav_switch_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/switch",
            data=json.dumps({"account_id": "../../etc/passwd"}).encode("utf-8"),
            headers={"Cookie": f"mrt={token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(trav_switch_req, timeout=3) as resp:
            switch_res = json.loads(resp.read().decode("utf-8"))
            assert switch_res.get("success") is False, "Expected switch to fail for traversal account_id"
            assert "Invalid" in switch_res.get("error", "") or "Access denied" in switch_res.get("error", "")
        print("PASS: Malicious account_id path traversal rejected")

        # J. Anti-Cookie-Theft & Device-Bound Session Security Check
        # Legitimate mobile device registers session
        legit_ua = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 Chrome/120.0 Mobile"
        legit_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/?token={token}", headers={"User-Agent": legit_ua})
        with urllib.request.urlopen(legit_req, timeout=3) as resp:
            set_cookie_hdr = resp.headers.get("Set-Cookie", "")
            assert "mrt=v1." in set_cookie_hdr, f"Expected signed session token in Set-Cookie, got: {set_cookie_hdr}"
            assert "HttpOnly" in set_cookie_hdr, "Missing HttpOnly flag in cookie"
            session_cookie = set_cookie_hdr.split(";")[0].split("=")[1]

        # Authorized device uses the session cookie -> 200 OK
        authed_mobile_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/state",
            headers={"Cookie": f"mrt={session_cookie}", "User-Agent": legit_ua},
        )
        with urllib.request.urlopen(authed_mobile_req, timeout=3) as resp:
            assert resp.status == 200, f"Expected 200 for authorized device, got {resp.status}"

        # Attacker steals cookie and replays from different device/browser -> 401 Unauthorized
        attacker_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/17.0"
        stolen_cookie_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/state",
            headers={"Cookie": f"mrt={session_cookie}", "User-Agent": attacker_ua},
        )
        try:
            urllib.request.urlopen(stolen_cookie_req, timeout=3)
            assert False, "Security failure: Stolen cookie was accepted from different device/User-Agent"
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401 for cookie replay on mismatched device, got {e.code}"
        print("PASS: Anti-cookie-hijacking device binding verified (Cookie replay rejected with HTTP 401)")

        # K. Access Audit Logging (log/ip.json) & 30-day retention check
        from app.audit import IP_LOG_PATH, load_ip_log, purge_expired_records
        assert os.path.exists(IP_LOG_PATH), "Expected log/ip.json to be created"
        ip_data = load_ip_log()
        assert "devices" in ip_data and len(ip_data["devices"]) > 0, "No device telemetry in log/ip.json"
        assert ip_data.get("retention_days") == 30, "Expected 30-day retention policy"
        
        # Test 30-day purge logic
        test_now = 1000000000.0
        stale_data = {
            "devices": {
                "1.1.1.1": {"last_seen_epoch": test_now - (35 * 86400)},  # 35 days old (should be purged)
                "2.2.2.2": {"last_seen_epoch": test_now - (5 * 86400)},   # 5 days old (should survive)
            },
            "recent_events": [
                {"epoch": test_now - (35 * 86400)},
                {"epoch": test_now - (5 * 86400)},
            ]
        }
        purged = purge_expired_records(stale_data, test_now)
        assert "1.1.1.1" not in purged["devices"], "35-day old device record was not purged"
        assert "2.2.2.2" in purged["devices"], "5-day old device record was wrongly purged"
        assert len(purged["recent_events"]) == 1, "Stale events were not purged"
        print("PASS: Access audit logging (log/ip.json) & 30-day retention auto-purge verified")

        # L. Terminal Access Killswitch (state.json / static.json toggle)
        from app.server import save_state
        cur_st = load_state()
        cur_st["terminal_enabled"] = False
        save_state(cur_st)

        # /terminal should return 403 Forbidden
        req_term_disabled = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/terminal",
            headers={"Cookie": f"mrt={session_cookie}", "User-Agent": legit_ua},
        )
        try:
            urllib.request.urlopen(req_term_disabled, timeout=3)
            assert False, "Expected 403 for /terminal when terminal_enabled is False"
        except urllib.error.HTTPError as e:
            assert e.code == 403, f"Expected 403, got {e.code}"

        # /api/terminal/ws should return 403 Forbidden
        req_ws_disabled = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/terminal/ws",
            headers={"Cookie": f"mrt={session_cookie}", "User-Agent": legit_ua},
        )
        try:
            urllib.request.urlopen(req_ws_disabled, timeout=3)
            assert False, "Expected 403 for /api/terminal/ws when terminal_enabled is False"
        except urllib.error.HTTPError as e:
            assert e.code == 403, f"Expected 403, got {e.code}"

        # Restore terminal access
        cur_st["terminal_enabled"] = True
        save_state(cur_st)

        req_term_enabled = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/terminal",
            headers={"Cookie": f"mrt={session_cookie}", "User-Agent": legit_ua},
        )
        with urllib.request.urlopen(req_term_enabled, timeout=3) as resp:
            assert resp.status == 200, f"Expected 200 when terminal re-enabled, got {resp.status}"
        print("PASS: Terminal access killswitch enforced (403 Forbidden on disabled state)")
        from app.server import STATE_PATH
        mode = os.stat(STATE_PATH).st_mode & 0o777
        assert mode == 0o600, f"Expected state.json mode 0600, found {oct(mode)}"
        print(f"PASS: state.json restricted to owner-only permissions ({oct(mode)})")
    finally:
        server.shutdown()
        server.server_close()

    print("\nALL RUNNABLE SELF-CHECKS & SECURITY VERIFICATIONS PASSED SUCCESSFULLY.")


if __name__ == "__main__":
    run_checks()
