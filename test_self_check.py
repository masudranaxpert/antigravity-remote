"""Self-check verification script for Antigravity Mobile Switcher.

Runs assert-based checks against live detection and running server.
No test frameworks or fixtures required.
"""
import urllib.request
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.detector import get_live_antigravity_identity, get_official_remote_info, load_accounts


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

    # 4. HTTP Health Endpoint check (starts transient test server if not running)
    server_started = False
    server = None
    try:
        urllib.request.urlopen("http://127.0.0.1:8077/health", timeout=0.5)
    except Exception:
        import threading
        from http.server import ThreadingHTTPServer
        from app.server import DashboardHandler
        server = ThreadingHTTPServer(("127.0.0.1", 8077), DashboardHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        server_started = True

    try:
        req = urllib.request.Request("http://127.0.0.1:8077/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            assert resp.status == 200, f"Expected 200 from /health, got {resp.status}"
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True, f"Unexpected health payload: {data}"
        print("PASS: HTTP /health responsive and healthy")
    finally:
        if server_started and server:
            server.shutdown()
            server.server_close()

    print("\nALL RUNNABLE SELF-CHECKS PASSED SUCCESSFULLY.")


if __name__ == "__main__":
    run_checks()
