# Permanent Custom Domain Setup (Cloudflare Named Tunnel)

By default, Antigravity Remote starts a **Quick Tunnel** (`*.trycloudflare.com`), which requires zero configuration but generates a random temporary subdomain on startup.

If you own a domain (e.g. `yourdomain.com`), you can set up a **Cloudflare Named Tunnel** (100% free forever via Cloudflare Zero Trust). This gives you a **permanent, fixed URL** (e.g., `https://remote.yourdomain.com`) that never changes—even across extended internet outages, router disconnects, or PC reboots.

---

## 🚀 Method 1: Cloudflare Zero Trust Dashboard (Recommended)

This is the fastest and most foolproof approach. Cloudflare handles DNS records, SSL certificates, and token provisioning automatically.

### Step 1: Open Cloudflare Zero Trust
1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com).
2. Select **Zero Trust** from the left navigation sidebar.
3. If this is your first time, choose a free team name and select the Free plan (0 USD/mo).

### Step 2: Create a Tunnel
1. Navigate to **Networks** ➔ **Tunnels** in the Zero Trust sidebar.
2. Click **Add a tunnel**.
3. Select **Cloudflared** as the connector type and click **Next**.
4. Name your tunnel (e.g., `antigravity-remote`) and click **Save tunnel**.

### Step 3: Install the Connector Service
1. Choose **Debian / Ubuntu** (or your platform).
2. Copy the installation command provided under **Install and run a connector**:
   ```bash
   sudo cloudflared service install <YOUR_TOKEN>
   ```
3. Run this command in your host PC terminal. The installer will register and start `cloudflared.service` in `systemd`.
4. Return to the dashboard. The **Connectors** section will display your machine as **Active (Healthy / Green)**. Click **Next**.

### Step 4: Route Your Domain (Public Hostname)
1. In the **Public Hostname** section, configure:
   - **Subdomain**: `remote` (or any prefix such as `ag` or `pc`)
   - **Domain**: Select your domain from the dropdown (e.g., `yourdomain.com`)
   - **Path**: Leave blank
   - **Service Type**: `HTTP`
   - **URL**: `localhost:8077` (or `127.0.0.1:8077`)
2. Click **Save tunnel**.

Cloudflare will automatically provision your DNS CNAME record and issue an SSL certificate for `https://remote.yourdomain.com`.

---

## 🛠️ Method 2: Command Line Interface (CLI)

If you prefer managing tunnels purely via terminal without the web dashboard:

```bash
# 1. Authorize your Cloudflare account (opens browser to select domain)
cloudflared tunnel login

# 2. Create named tunnel
cloudflared tunnel create antigravity-remote

# 3. Associate your custom subdomain with the tunnel
cloudflared tunnel route dns antigravity-remote remote.yourdomain.com
```

Create a configuration file at `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_UUID>
credentials-file: /home/masud/.cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: remote.yourdomain.com
    service: http://127.0.0.1:8077
  - service: http_status:404
```

Install and run as a system service:
```bash
sudo cloudflared --config /home/masud/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

---

## 📱 Integrating with Antigravity Remote

Once your domain is routed, inform the launcher of your permanent domain so the desktop banner displays your permanent link:

Open `state.json` located in the root of the application:
```json
{
  "mobile_token": "your-secret-token",
  "custom_domain": "remote.yourdomain.com",
  "tunnel_mode": "permanent",
  "remote_url": ""
}
```

### Tunnel Modes in `state.json`

| Value | Description |
| :--- | :--- |
| `"permanent"` | Strictly binds to your Cloudflare Named Tunnel (`cloudflared.service`). Never generates temporary links. |
| `"auto"` *(Default)* | Automatically detects if `cloudflared.service` is active. If active, uses permanent domain; otherwise falls back to Quick Tunnel. |
| `"quick"` | Forces an ephemeral Quick Tunnel (`*.trycloudflare.com`), even if the permanent service is running. |
| `"dual"` | Runs **both** tunnels concurrently, displaying your permanent domain link and a temporary Quick Tunnel share link side by side. |
| `"local"` | Binds only to `127.0.0.1:8077` with no public cloud tunnel. |

---

## ⚡ On-The-Fly CLI Overrides

You can temporarily switch modes without modifying `state.json`:

```bash
# Force a temporary Quick Tunnel even while the permanent service is running
./run.sh --quick           # or -q, --try
python3 launcher.py --quick

# Run both permanent domain and temporary link simultaneously
./run.sh --dual            # or -d
python3 launcher.py --dual

# Force permanent domain mode
./run.sh --permanent       # or -p
python3 launcher.py --permanent

# Local only
./run.sh --local           # or -l
```

When running in **Dual Mode** (`--dual`), both connections proxy to `localhost:8077` simultaneously without port conflicts:
```text
==========================================================================
                 ANTIGRAVITY REMOTE - CONTROL CENTER
==========================================================================
  [✓] Local Port     : http://127.0.0.1:8077
  [✓] Permanent URL  : https://remote.yourdomain.com
  [✓] Quick Tunnel   : https://sample-words.trycloudflare.com
  [✓] Secret Token   : your-secret-token
--------------------------------------------------------------------------
  👉 PERMANENT LINK (Your Domain):
     https://remote.yourdomain.com/?token=your-secret-token

  👉 QUICK SHARE LINK (Temporary):
     https://sample-words.trycloudflare.com/?token=your-secret-token
==========================================================================
```
