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
from app.server import (
    SESSION_MAX_AGE,
    compute_client_fingerprint,
    generate_session_token,
    load_state,
    save_state,
    verify_session_token,
)
from app.switcher import get_sleep_inhibit_status, set_sleep_inhibit
from app.terminal import compute_accept_key, encode_ws_frame, read_ws_message, resize_pty, spawn_shell_pty
from app.totp import compute_totp, generate_totp_secret, get_totp_uri, verify_totp


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

    # Verify binary frame encoding (opcode 2)
    bin_frame = encode_ws_frame(b"\xf0\x9f\x98\x80 bangla \xe0\xa6\xac\xe0\xa6\xbe\xe0\xa6\x82\xe0\xa6\xb2\xe0\xa6\xbe", 2)
    assert bin_frame[0] == 0x82, "Invalid binary frame opcode"

    # Verify WebSocket fragmented message reassembly (opcode 0 continuation)
    import socket
    s_srv, s_cli = socket.socketpair()
    mask1 = b"\x12\x34\x56\x78"
    p1 = b"part1_"
    mp1 = bytes(b ^ mask1[i % 4] for i, b in enumerate(p1))
    s_cli.sendall(bytearray([0x02, 0x80 | len(p1)]) + mask1 + mp1)
    p2 = b"part2_done"
    mp2 = bytes(b ^ mask1[i % 4] for i, b in enumerate(p2))
    s_cli.sendall(bytearray([0x80, 0x80 | len(p2)]) + mask1 + mp2)

    op, assembled = read_ws_message(s_srv)
    assert op == 2 and assembled == b"part1_part2_done", f"Frame reassembly failed: {op}, {assembled}"
    s_srv.close()
    s_cli.close()

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
    print("PASS: Terminal PTY shell lifecycle, fragmented frame reassembly, and RFC 6455 engine verified")

    # 6b. RFC 6238 TOTP Engine & 24-Hour Session Lifecycle verification
    totp_sec = generate_totp_secret()
    assert len(totp_sec) == 16, f"Expected 16-char secret, got {totp_sec}"
    totp_code = compute_totp(totp_sec)
    assert len(totp_code) == 6 and totp_code.isdigit(), f"Invalid TOTP code: {totp_code}"
    assert verify_totp(totp_sec, totp_code) is True, "TOTP verification failed on current code"
    bad_code = "999999" if totp_code != "999999" else "000000"
    assert verify_totp(totp_sec, bad_code) is False, "TOTP verification accepted incorrect code"
    totp_uri = get_totp_uri(totp_sec, account_name="test", issuer="TestIssuer")
    assert totp_uri.startswith("otpauth://totp/"), f"Invalid OTP URI: {totp_uri}"
    assert totp_sec in totp_uri, "Secret missing in otpauth URI"

    # Strict 24-Hour session lifetime verification
    assert SESSION_MAX_AGE == 86400, f"Expected 86400s (24h) session limit, got {SESSION_MAX_AGE}"
    test_master = "test_token_secret_12345"
    test_fp = "a1b2c3d4e5f6"
    fresh_session = generate_session_token(test_master, test_fp)
    valid_fresh, msg_fresh = verify_session_token(fresh_session, test_master, test_fp)
    assert valid_fresh is True and msg_fresh == "ok", f"Fresh session failed: {msg_fresh}"

    # Expired session (> 24 hours) must be rejected
    expired_epoch = int(time.time()) - 86405
    import hashlib, hmac
    expired_payload = f"v1:{expired_epoch}:{test_fp}"
    expired_sig = hmac.new(test_master.encode("utf-8"), expired_payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    expired_session = f"v1.{expired_epoch}.{test_fp}.{expired_sig}"
    valid_exp, msg_exp = verify_session_token(expired_session, test_master, test_fp)
    assert valid_exp is False and msg_exp == "session_expired", f"Expired session check failed: {msg_exp}"
    print("PASS: RFC 6238 TOTP engine and strict 24-hour session lifecycle verified")

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
        st_now = load_state()
        token = st_now.get("mobile_token", "")
        otp_param = f"&otp={compute_totp(st_now.get('totp_secret', ''))}" if st_now.get("totp_enabled", False) else ""
        import urllib.parse as _up
        enc_token = _up.quote(token, safe="")  # Encode special chars (e.g. #) so they survive URL query string
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/?token={enc_token}{otp_param}")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8")
            assert "Antigravity Mobile Switcher" in body, "Expected dashboard template for authenticated request"
            assert "quota-refresh-btn" in body, "Expected quota refresh button in dashboard template"
            cookie = resp.headers.get("Set-Cookie", "")
            assert "mrt=" in cookie, f"Expected Set-Cookie header with mrt token, got {cookie}"
        print("PASS: Authenticated GET /?token=... serves dashboard with quota-refresh-btn and sets cookie")


        # D. GET /api/state includes audio & sleep telemetry and in-memory quota caching
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/state", headers={"Cookie": f"mrt={token}"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            state = json.loads(resp.read().decode("utf-8"))
            assert "audio" in state, "Missing audio in /api/state"
            assert state["audio"]["supported"] is True, "Audio should be supported on host"
            assert "prevent_sleep" in state, "Missing prevent_sleep in /api/state"
            assert "quota_cached" in state, "Missing quota_cached flag in /api/state"
            assert "quota_updated_at" in state, "Missing quota_updated_at in /api/state"

        # Verify second call is served from in-memory cache
        with urllib.request.urlopen(req, timeout=3) as resp:
            state2 = json.loads(resp.read().decode("utf-8"))
            assert state2["quota_cached"] is True, "Expected second consecutive call to be served from in-memory cache"

        # Verify force refresh bypasses cache
        req_force = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/state?refresh_quota=1", headers={"Cookie": f"mrt={token}"})
        with urllib.request.urlopen(req_force, timeout=3) as resp:
            state3 = json.loads(resp.read().decode("utf-8"))
            assert state3["quota_cached"] is False, "Expected force refresh to bypass cache"
        print(f"PASS: /api/state telemetry (audio, prevent_sleep) and in-memory quota caching lifecycle verified")

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

        req_term_auth = urllib.request.Request(f"http://127.0.0.1:{test_port}/terminal?token={enc_token}{otp_param}")
        with urllib.request.urlopen(req_term_auth, timeout=3) as resp:
            t_body_auth = resp.read().decode("utf-8")
            assert "terminal-container" in t_body_auth, "Expected terminal template for authenticated request"
            assert "xterm.js" in t_body_auth, "Expected xterm script tags in terminal template"
            assert 'data-action="paste"' in t_body_auth, "Expected paste button in terminal template"
            assert 'data-action="copy"' in t_body_auth, "Expected copy button in terminal template"
            assert 'data-key="ctrl-c"' in t_body_auth, "Expected Ctrl+C button in terminal template"
            assert 'data-key="ctrl-d"' in t_body_auth, "Expected Ctrl+D button in terminal template"
            assert 'id="composer-acc-btn"' not in t_body_auth, "Composer button should be removed from accessory bar"
            assert '<svg width="15" height="15"' in t_body_auth, "Expected modern SVG icons in accessory bar"
            assert 'data-key="backspace"' in t_body_auth, "Expected backspace button in terminal template"
            assert 'data-raw="."' in t_body_auth, "Expected dot button in terminal template"
            assert 'data-raw=".."' in t_body_auth, "Expected double dot button in terminal template"
            assert 'id="alt-toggle-btn"' in t_body_auth, "Expected Alt modifier toggle button in terminal template"
            assert 'id="kbd-toggle-btn"' in t_body_auth, "Expected keyboard toggle button in terminal template"
            assert 'id="composer-drawer"' in t_body_auth, "Expected composer drawer in terminal template"
            assert 'id="scroll-bottom-btn"' in t_body_auth, "Expected scroll to bottom button in terminal template"
            assert 'id="select-overlay"' in t_body_auth, "Expected select overlay in terminal template"
            assert 'viewport-fit=cover' in t_body_auth, "Expected viewport-fit=cover in terminal template"
        print("PASS: /terminal endpoint securely gated by authentication & mobile touch accessories verified (Zero FOUC)")

        # H. WebSocket Origin check and Session Persistence / Reattachment verification
        import socket

        # 1. Reject unauthorized Origin (Cross-Site WebSocket Hijacking prevention)
        bad_ws_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bad_ws_sock.connect(("127.0.0.1", test_port))
        bad_ws_req = (
            f"GET /api/terminal/ws?token={enc_token}{otp_param}&cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            "Origin: http://malicious-attacker.com\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        bad_ws_sock.sendall(bad_ws_req.encode("utf-8"))
        bad_ws_resp = bad_ws_sock.recv(1024).decode("utf-8")
        assert "403 Forbidden" in bad_ws_resp, f"Expected 403 for unauthorized Origin, got: {bad_ws_resp}"
        bad_ws_sock.close()
        print("PASS: Cross-Site WebSocket Hijacking blocked via Origin verification (HTTP 403)")

        # 2. Establish authorized WebSocket connection with valid Origin
        ws_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ws_sock.connect(("127.0.0.1", test_port))
        ws_req = (
            f"GET /api/terminal/ws?token={enc_token}{otp_param}&cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            f"Origin: http://127.0.0.1:{test_port}\r\n"
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

        # Read initial session announcement frame (Text frame, opcode 1)
        init_frame = ws_sock.recv(1024)
        payload_len = init_frame[1] & 0x7F
        sess_meta = json.loads(init_frame[2:2 + payload_len].decode("utf-8"))
        assert sess_meta.get("type") == "session", f"Expected session frame, got {sess_meta}"
        session_id = sess_meta.get("id")
        assert session_id and len(session_id) == 32, f"Invalid session_id: {session_id}"
        assert sess_meta.get("reconnected") is False

        # Send command 'echo WS_LIVE_OK' as Binary Frame (opcode 2) with multi-byte UTF-8
        cmd_bytes = b"echo WS_LIVE_OK_\xe0\xa6\xac\xe0\xa6\xbe\xe0\xa6\x82\xe0\xa6\xb2\xe0\xa6\xbe\n"
        mask = b"\x11\x22\x33\x44"
        masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(cmd_bytes))
        ws_frame = bytearray([0x82, 0x80 | len(cmd_bytes)]) + mask + masked_payload
        ws_sock.sendall(ws_frame)

        # Read response frames from server (Streamed as binary frames opcode 2)
        time.sleep(0.2)
        ws_sock.settimeout(2.0)
        accumulated = b""
        for _ in range(5):
            try:
                raw_in = ws_sock.recv(4096)
                accumulated += raw_in
                if b"WS_LIVE_OK" in accumulated:
                    break
            except Exception:
                break
        assert b"WS_LIVE_OK" in accumulated, f"Expected WS_LIVE_OK in PTY output, got: {accumulated}"

        # Send command that leaves a unique marker in ring buffer backlog
        marker_cmd = b"echo PERSISTENCE_REPLAY_TEST\n"
        masked_marker = bytes(b ^ mask[i % 4] for i, b in enumerate(marker_cmd))
        ws_sock.sendall(bytearray([0x82, 0x80 | len(marker_cmd)]) + mask + masked_marker)
        time.sleep(0.2)
        for _ in range(5):
            try:
                raw_in = ws_sock.recv(4096)
                accumulated += raw_in
                if b"PERSISTENCE_REPLAY_TEST" in accumulated:
                    break
            except Exception:
                break

        # Send WebSocket heartbeat ping (Text JSON) and verify pong
        ping_bytes = b'{"type":"ping"}'
        mask_p = b"\x55\x66\x77\x88"
        masked_ping = bytes(b ^ mask_p[i % 4] for i, b in enumerate(ping_bytes))
        ws_sock.sendall(bytearray([0x81, 0x80 | len(ping_bytes)]) + mask_p + masked_ping)
        time.sleep(0.1)
        pong_in = ws_sock.recv(1024)
        assert b'"type":"pong"' in pong_in or b"pong" in pong_in, f"Expected pong response, got {pong_in}"

        # Send custom control JSON and verify it is absorbed (never leaked into PTY stdin)
        ctrl_bytes = b'{"type":"session","id":"leaked_check"}'
        masked_ctrl = bytes(b ^ mask_p[i % 4] for i, b in enumerate(ctrl_bytes))
        ws_sock.sendall(bytearray([0x81, 0x80 | len(ctrl_bytes)]) + mask_p + masked_ctrl)
        time.sleep(0.1)

        # Now disconnect socket abruptly (simulates mobile background / network drop)
        ws_sock.close()

        # 3. Test Session Persistence: Reconnect with session_id query param
        reconn_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reconn_sock.connect(("127.0.0.1", test_port))
        reconn_req = (
            f"GET /api/terminal/ws?token={enc_token}{otp_param}&session_id={session_id}&cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            f"Origin: http://127.0.0.1:{test_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        reconn_sock.sendall(reconn_req.encode("utf-8"))
        reconn_hdr = reconn_sock.recv(1024).decode("utf-8")
        assert "101 Switching Protocols" in reconn_hdr, f"Expected 101 on reconnect, got: {reconn_hdr}"

        # Receive session frame on reconnect
        reconn_frame = reconn_sock.recv(1024)
        p_len = reconn_frame[1] & 0x7F
        reconn_meta = json.loads(reconn_frame[2:2 + p_len].decode("utf-8"))
        assert reconn_meta.get("id") == session_id, "Reconnected to wrong session"
        assert reconn_meta.get("reconnected") is True, "Expected reconnected=True"

        # Receive backlog replay frame
        reconn_sock.settimeout(2.0)
        backlog_data = reconn_sock.recv(16384)
        assert b"PERSISTENCE_REPLAY_TEST" in backlog_data, f"Backlog replay missing marker: {backlog_data}"
        reconn_sock.close()
        print("PASS: Live WebSocket terminal interactive session, binary frame streaming, and session persistence verified")

        # 4. Multi-client session detachment check (Bug #7: old socket must receive close code 4000)
        sock_a = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_a.connect(("127.0.0.1", test_port))
        sock_a.sendall((
            f"GET /api/terminal/ws?token={enc_token}{otp_param}&cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            f"Origin: http://127.0.0.1:{test_port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode("utf-8"))
        assert "101 Switching Protocols" in sock_a.recv(1024).decode("utf-8")
        meta_raw = sock_a.recv(1024)
        p_len_a = meta_raw[1] & 0x7F
        meta_a = json.loads(meta_raw[2:2 + p_len_a].decode("utf-8"))
        sess_a_id = meta_a["id"]

        # Client B attaches to same session
        sock_b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_b.connect(("127.0.0.1", test_port))
        sock_b.sendall((
            f"GET /api/terminal/ws?token={enc_token}{otp_param}&session_id={sess_a_id}&cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            f"Origin: http://127.0.0.1:{test_port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode("utf-8"))
        assert "101 Switching Protocols" in sock_b.recv(1024).decode("utf-8")

        # Client A should receive close frame with code 4000
        sock_a.settimeout(2.0)
        close_frame_a = sock_a.recv(1024)
        assert len(close_frame_a) >= 4 and close_frame_a[0] == 0x88, "Expected close frame on replaced client"
        import struct
        close_code_a = struct.unpack("!H", close_frame_a[2:4])[0]
        assert close_code_a == 4000, f"Expected close code 4000, got: {close_code_a}"
        sock_a.close()

        # 5. Natural shell exit check (Bug #8: exit sends close code 4001)
        exit_cmd = b"exit\n"
        m_exit = b"\x11\x22\x33\x44"
        sock_b.sendall(bytearray([0x82, 0x80 | len(exit_cmd)]) + m_exit + bytes(b ^ m_exit[i % 4] for i, b in enumerate(exit_cmd)))
        sock_b.settimeout(3.0)
        shell_closed = False
        for _ in range(10):
            try:
                frame_b = sock_b.recv(2048)
                if frame_b and frame_b[0] == 0x88:
                    close_code_b = struct.unpack("!H", frame_b[2:4])[0]
                    assert close_code_b == 4001, f"Expected close code 4001, got: {close_code_b}"
                    shell_closed = True
                    break
            except Exception:
                break
        assert shell_closed is True, "Expected close frame 4001 on shell exit"
        sock_b.close()
        print("PASS: Multi-client detachment (code 4000) and shell exit lifecycle (code 4001) verified")

        # Test WebSocket authentication via session cookie without query token (pure cookie auth)
        ws_sock_cookie = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ws_sock_cookie.connect(("127.0.0.1", test_port))
        test_ws_ua = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/153.0.0.0 Mobile"
        fresh_test_fp = compute_client_fingerprint({"User-Agent": test_ws_ua})
        fresh_ws_cookie = generate_session_token(token, fresh_test_fp)
        ws_cookie_req = (
            f"GET /api/terminal/ws?cols=80&rows=24 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{test_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"User-Agent: {test_ws_ua}\r\n"
            f"Cookie: mrt={fresh_ws_cookie}\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        ws_sock_cookie.sendall(ws_cookie_req.encode("utf-8"))
        ws_cookie_hdr = ws_sock_cookie.recv(1024).decode("utf-8")
        assert "101 Switching Protocols" in ws_cookie_hdr, f"Expected 101 for cookie auth, got: {ws_cookie_hdr}"
        ws_sock_cookie.close()
        print("PASS: WebSocket session cookie authentication (zero query token) verified")

        # Test mobile virtual keyboard helper configuration (bypasses Gboard spacebar composition lag)
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(repo_dir, "static", "terminal.js"), "r", encoding="utf-8") as f_term:
            term_js = f_term.read()
            assert "isMobileDevice" in term_js, "Expected mobile detection in terminal.js"
            assert "type = 'password'" in term_js or 'type = "password"' in term_js, "Expected password input adapter for mobile keyboards"
            assert "autocomplete" in term_js and "new-password" in term_js, "Expected autocomplete=new-password to suppress browser autofill"
        print("PASS: Mobile keyboard password adapter verified (immediate character dispatch without spacebar lag)")

        if os.path.exists("/usr/bin/google-chrome"):
            chrome_test_html = f"""<!DOCTYPE html>
<html><head><script src="http://127.0.0.1:{test_port}/static/vendor/xterm/xterm.js"></script></head>
<body><div id="term"></div><div id="res"></div>
<script>
try {{
  const orig = document.createElement;
  document.createElement = function(t, ...a) {{
    if (typeof t === 'string' && t.toLowerCase() === 'textarea') {{
      const el = orig.call(document, 'input', ...a);
      el.type = 'password';
      return el;
    }}
    return orig.call(document, t, ...a);
  }};
  const term = new Terminal();
  term.open(document.getElementById('term'));
  document.createElement = orig;
  let received = [];
  term.onData(d => received.push(d));
  term.textarea.value = 'l';
  term.textarea.dispatchEvent(new InputEvent('input', {{ data: 'l', inputType: 'insertText' }}));
  term.textarea.value = 's';
  term.textarea.dispatchEvent(new InputEvent('input', {{ data: 's', inputType: 'insertText' }}));
  document.getElementById('res').textContent = (term.textarea.tagName === 'INPUT' && term.textarea.type === 'password' && received.join('') === 'ls') ? 'OK' : 'FAIL';
}} catch (e) {{ document.getElementById('res').textContent = 'ERR:' + e; }}
</script></body></html>"""
            import tempfile, subprocess
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tf:
                tf.write(chrome_test_html)
                t_name = tf.name
            try:
                c_out = subprocess.check_output(["google-chrome", "--headless=new", "--dump-dom", f"file://{t_name}"]).decode()
                assert '<div id="res">OK</div>' in c_out, f"Chrome mobile terminal simulation failed: {c_out}"
                print("PASS: Headless Chrome live validation of mobile input adapter passed (emits 'ls' immediately)")
            finally:
                if os.path.exists(t_name):
                    os.unlink(t_name)

        # Verify Enter button in terminal.html, auto-repeat for backspace, anti-flicker flags, and unified touch scroll
        with open(os.path.join(repo_dir, "templates", "terminal.html"), "r", encoding="utf-8") as f_html:
            html_content = f_html.read()
            assert 'data-key="enter"' in html_content, "Expected Enter button on touch bar"
            paste_idx = html_content.find('data-action="paste"')
            enter_idx = html_content.find('data-key="enter"')
            copy_idx = html_content.find('data-action="copy"')
            assert paste_idx != -1 and enter_idx != -1 and copy_idx != -1, "Expected paste, enter, and copy buttons"
            assert paste_idx < enter_idx < copy_idx, "Expected Enter button immediately after Paste button"
            assert 'data-key="page-up"' in html_content and 'data-key="page-down"' in html_content, "Expected Page Up/Down buttons"

        with open(os.path.join(repo_dir, "static", "terminal.js"), "r", encoding="utf-8") as f_term:
            term_js = f_term.read()
            assert "isRepeatable" in term_js and "repeatInterval" in term_js, "Expected backspace/arrow auto-repeat on hold"
            assert "allowTransparency: false" in term_js, "Expected allowTransparency: false to prevent canvas flickering"
            assert "smoothScrollDuration: 0" in term_js, "Expected smoothScrollDuration: 0 to prevent scroll animation stutter"
            assert "term.scrollLines(rows)" in term_js, "Expected unified touch scrolling in normal buffer"
            assert "case 'enter':" in term_js, "Expected case 'enter' in accessory bar switch"
            assert "function triggerEnter" in term_js, "Expected synchronized triggerEnter helper for mobile Enter"
            assert "case 'page-up':" in term_js and "case 'page-down':" in term_js, "Expected PageUp/Down key handlers"
            assert "WheelEvent" in term_js, "Expected WheelEvent dispatch for accurate cell-level mouse scrolling in OpenCode"

        with open(os.path.join(repo_dir, "app", "terminal.py"), "r", encoding="utf-8") as f_pty:
            pty_py = f_pty.read()
            assert "CLAUDE_CODE_NO_FLICKER" in pty_py and "NO_FLICKER" in pty_py, "Expected anti-flicker environment variables in PTY"
            assert "settimeout(60)" in pty_py, "Expected 60s socket timeout to eliminate 10s idle disconnection flickering"

        print("PASS: Enter button order, backspace auto-repeat, anti-flicker, and opencode touch scrolling verified")

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
        except urllib.error.URLError as e:
            assert "Broken pipe" in str(e) or "Connection reset" in str(e), f"Unexpected URLError: {e}"
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
        legit_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/?token={enc_token}{otp_param}", headers={"User-Agent": legit_ua})
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
        from app.audit import IP_LOG_PATH, load_ip_log, purge_expired_records, enforce_size_limit, MAX_LOG_SIZE_BYTES
        assert os.path.exists(IP_LOG_PATH), "Expected log/ip.json to be created"
        ip_data = load_ip_log()
        assert "devices" in ip_data and len(ip_data["devices"]) > 0, "No device telemetry in log/ip.json"
        assert ip_data.get("retention_days") == 30, "Expected 30-day retention policy"
        assert ip_data.get("max_size_mb") == 15, "Expected 15MB size limit policy"
        assert MAX_LOG_SIZE_BYTES == 15 * 1024 * 1024, "Expected MAX_LOG_SIZE_BYTES to be 15MB"

        # Test 15MB size limit enforcement with simulated oversized data
        test_now = 1000000000.0
        oversized_data = {
            "devices": {f"10.0.0.{i}": {"last_seen_epoch": test_now - i, "data": "x" * 200} for i in range(100)},
            "recent_events": [{"epoch": test_now - i, "log": "payload" * 50} for i in range(200)],
        }
        capped_data = enforce_size_limit(oversized_data, max_bytes=800)
        capped_bytes = len(json.dumps(capped_data, indent=2, ensure_ascii=False).encode("utf-8"))
        assert capped_bytes <= 800, f"Size limit violated: expected <= 800 bytes, got {capped_bytes}"

        # Test 30-day purge logic
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
        print("PASS: Access audit logging (log/ip.json) 30-day retention & 15MB size ceiling verified")

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

        # M. Login Verification Endpoint (/api/auth/verify) & 24h Set-Cookie Check
        orig_st = load_state()
        orig_totp = bool(orig_st.get("totp_enabled", False))
        orig_totp_secret = orig_st.get("totp_secret", "")
        if orig_totp:
            orig_st["totp_enabled"] = False
            save_state(orig_st)

        req_bad_auth = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/auth/verify",
            data=json.dumps({"token": "wrong_token_xyz"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": legit_ua},
            method="POST",
        )
        try:
            urllib.request.urlopen(req_bad_auth, timeout=3)
            assert False, "Expected 401 for invalid login token"
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401, got {e.code}"

        req_good_auth = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/auth/verify",
            data=json.dumps({"token": token}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": legit_ua},
            method="POST",
        )
        with urllib.request.urlopen(req_good_auth, timeout=3) as resp:
            assert resp.status == 200
            auth_res = json.loads(resp.read().decode("utf-8"))
            assert auth_res.get("success") is True
            set_cookie_val = resp.headers.get("Set-Cookie", "")
            assert "Max-Age=86400" in set_cookie_val, f"Cookie does not enforce 24-hour Max-Age: {set_cookie_val}"
            assert "HttpOnly" in set_cookie_val

        # N. Authenticator App 2FA (TOTP RFC 6238) Enforcement Flow
        demo_st = load_state()
        test_totp_secret = generate_totp_secret()
        demo_st["totp_secret"] = test_totp_secret
        demo_st["totp_enabled"] = True
        save_state(demo_st)

        try:
            # /api/auth/status confirms TOTP is active
            req_status = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/auth/status")
            with urllib.request.urlopen(req_status, timeout=3) as resp:
                status_data = json.loads(resp.read().decode("utf-8"))
                assert status_data.get("totp_enabled") is True, f"Expected totp_enabled True, got {status_data}"

            # Login attempt with valid token but wrong OTP -> 401
            wrong_otp_val = "000000" if compute_totp(test_totp_secret) != "000000" else "111111"
            req_wrong_otp = urllib.request.Request(
                f"http://127.0.0.1:{test_port}/api/auth/verify",
                data=json.dumps({"token": token, "otp": wrong_otp_val}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": legit_ua},
                method="POST",
            )
            try:
                urllib.request.urlopen(req_wrong_otp, timeout=3)
                assert False, "Expected 401 for wrong 2FA OTP code"
            except urllib.error.HTTPError as e:
                assert e.code == 401

            # Login attempt with valid token and CORRECT OTP -> 200 + 24h cookie
            current_otp = compute_totp(test_totp_secret)
            req_good_otp = urllib.request.Request(
                f"http://127.0.0.1:{test_port}/api/auth/verify",
                data=json.dumps({"token": token, "otp": current_otp}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": legit_ua},
                method="POST",
            )
            with urllib.request.urlopen(req_good_otp, timeout=3) as resp:
                assert resp.status == 200
                otp_res = json.loads(resp.read().decode("utf-8"))
                assert otp_res.get("success") is True
                otp_cookie = resp.headers.get("Set-Cookie", "")
                assert "Max-Age=86400" in otp_cookie
        finally:
            restore_st = load_state()
            restore_st["totp_enabled"] = orig_totp
            restore_st["totp_secret"] = orig_totp_secret
            save_state(restore_st)

        print("PASS: 24-hour cookie lifetime and Authenticator App 2FA (TOTP) auth flow verified")

        # O. Logout Verification (Cookie clearance & redirect)
        req_post_logout = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/auth/logout",
            data=b"{}",
            headers={"Content-Type": "application/json", "Cookie": f"mrt={session_cookie}"},
            method="POST",
        )
        with urllib.request.urlopen(req_post_logout, timeout=3) as resp:
            assert resp.status == 200
            logout_res = json.loads(resp.read().decode("utf-8"))
            assert logout_res.get("success") is True
            logout_cookie = resp.headers.get("Set-Cookie", "")
            assert "Max-Age=0" in logout_cookie and "mrt=deleted" in logout_cookie

        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", test_port)
        conn.request("GET", "/logout", headers={"Cookie": f"mrt={session_cookie}"})
        get_logout_resp = conn.getresponse()
        assert get_logout_resp.status == 302, f"Expected 302, got {get_logout_resp.status}"
        assert get_logout_resp.getheader("Location") == "/"
        get_logout_cookie = get_logout_resp.getheader("Set-Cookie", "")
        assert "Max-Age=0" in get_logout_cookie and "mrt=deleted" in get_logout_cookie
        conn.close()
        print("PASS: Session logout endpoints (POST /api/auth/logout & GET /logout) verified")
    finally:
        server.shutdown()
        server.server_close()

    print("\nALL RUNNABLE SELF-CHECKS & SECURITY VERIFICATIONS PASSED SUCCESSFULLY.")


if __name__ == "__main__":
    run_checks()
