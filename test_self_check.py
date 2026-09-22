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

    # 6. HTTP Endpoints & Auth FOUC-prevention check
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

        # G. Invalid token rejection check
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

        # J. State file permission check (0o600)
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
