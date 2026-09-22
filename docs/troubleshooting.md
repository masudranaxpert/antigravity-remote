# Troubleshooting & Frequently Asked Questions (FAQ)

This guide provides solutions to common questions, error messages, and edge cases.

---

## 🔍 Common Issues & Solutions

### 1. Port 8077 is already in use (`Address already in use`)
**Cause:** Another instance of Antigravity Remote or a previous background process is still bound to port 8077.  
**Solution:** Find and terminate the process holding the port:
```bash
# Check what is listening on port 8077
fuser 8077/tcp

# Terminate process cleanly
fuser -k 8077/tcp
```

---

### 2. "Cloudflare Tunnel disconnected unexpectedly" or tunnel URL fails to generate
**Causes:**
- No active internet connection.
- A local firewall or restrictive network is blocking outbound UDP/QUIC traffic to Cloudflare.
- Rate limits on trycloudflare.com from rapid restarts.

**Solutions:**
1. Verify `cloudflared` is executable:
   ```bash
   ~/.local/bin/cloudflared --version
   ```
2. Test standalone quick tunnel:
   ```bash
   cloudflared tunnel --url http://127.0.0.1:8077
   ```
3. If running on a restricted enterprise network, Cloudflare may failover to HTTPS. Wait 15 seconds for reconnection.

---

### 3. Keyring error: "secret-tool lookup failed"
**Cause:** `libsecret-tools` is not installed, or your GNOME Keyring daemon is locked.  
**Solution:**
1. Install `libsecret-tools`:
   ```bash
   sudo apt install -y libsecret-tools
   ```
2. If your session is headless or SSH, unlock the keyring:
   ```bash
   eval $(gnome-keyring-daemon --start)
   ```

---

### 4. Desktop shortcut shows generic terminal icon (`>_`)
**Cause:** GNOME Wayland desktop database has not re-indexed the `com.antigravity.remote.desktop` application ID.  
**Solution:**
1. Run the desktop update utility:
   ```bash
   update-desktop-database ~/.local/share/applications/
   gio set ~/Desktop/com.antigravity.remote.desktop metadata::trusted true
   ```
2. Log out and log back in, or press `Alt + F2`, type `r`, and press Enter (on X11).

---

### 5. Switching accounts in mobile UI succeeds, but PC IDE does not restart
**Cause:** Antigravity was installed via a non-standard snap binary path or has dangling Chromium Singleton locks.  
**Solution:**
1. Verify snap installation paths:
   ```bash
   which antigravity || ls /snap/bin/antigravity
   ```
2. Clear stale Chromium Singleton lock files:
   ```bash
   rm -f ~/.config/Antigravity/Singleton*
   ```

---

### 6. Where is the mobile security token stored?
The token is located in `state.json` inside the application root directory:
```json
{
  "mobile_token": "kRyxKoPlmIu76udnBKx2OORdYGwU5-q9",
  "remote_url": ""
}
```
You can edit this token at any time. When modified, restart `launcher.py` to apply.

---

### 7. Lost Authenticator phone or 2FA verification failing?
**Cause:** Phone clock drift or lost Authenticator app configuration.  
**Solution:**
You can reset or bypass 2FA directly from your host PC terminal without needing the phone:
```bash
# Disable 2FA requirement immediately
python3 launcher.py --disable-totp

# Or reconfigure a fresh Authenticator key
python3 launcher.py --setup-totp
```

---

### 8. Why does my mobile browser log out after 24 hours?
**Cause:** Security policy enforcement.  
**Details:** To protect your host machine from unauthorized access via old, abandoned mobile browser tabs, all session cookies have a strict 24-hour lifetime (`Max-Age=86400`). After 24 hours, you simply enter your secret token (and 6-digit 2FA code if enabled) to renew the session for another 24 hours.

---

### 9. Web terminal disconnects after being idle in mobile browser
**Cause:** Mobile operating systems (iOS / Android) aggressively sleep background WebSocket network sockets, or Cloudflare drops idle connections after 60-100 seconds.  
**Details:** Antigravity Remote incorporates an automatic 15-second ping/pong heartbeat and an exponential backoff auto-reconnector in `terminal.js`. When you switch back to the browser tab, the terminal automatically re-establishes the connection within 1-2 seconds.
