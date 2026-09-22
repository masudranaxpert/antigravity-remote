"""Access audit logging engine for Antigravity Remote.

Tracks client IP, device information, Cloudflare geo headers, and authentication
status in log/ip.json with automatic 30-day retention and atomic persistence.
Pure Python standard library only.
"""
import datetime
import json
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "log")
IP_LOG_PATH = os.path.join(LOG_DIR, "ip.json")
RETENTION_SECONDS = 30 * 86400  # 30 days retention


def parse_device_info(user_agent: str) -> dict:
    """Extract device type, OS, and browser from a User-Agent string."""
    ua = user_agent or ""
    if not ua:
        return {
            "device_type": "Unknown",
            "os": "Unknown",
            "browser": "Unknown",
            "summary": "Unknown Device",
        }

    # Detect Device Type
    is_tablet = bool(re.search(r"iPad|Tablet|PlayBook|Silk", ua, re.IGNORECASE))
    is_mobile = bool(re.search(r"Mobile|Android|iPhone|iPod|BlackBerry|IEMobile|Opera Mini", ua, re.IGNORECASE)) and not is_tablet

    if is_tablet:
        device_type = "Tablet"
    elif is_mobile:
        device_type = "Mobile"
    elif re.search(r"curl|Postman|python-requests|wget|HTTPClient", ua, re.IGNORECASE):
        device_type = "Script/Tool"
    else:
        device_type = "Desktop"

    # Detect OS
    os_name = "Unknown OS"
    if "iPhone" in ua or "iPad" in ua:
        ver_match = re.search(r"OS (\d+[_\.]\d+)", ua)
        os_ver = ver_match.group(1).replace("_", ".") if ver_match else ""
        os_name = f"iOS {os_ver}".strip()
    elif "Android" in ua:
        ver_match = re.search(r"Android (\d+(\.\d+)?)", ua)
        os_ver = ver_match.group(1) if ver_match else ""
        os_name = f"Android {os_ver}".strip()
    elif "Windows NT 10.0" in ua:
        os_name = "Windows 10/11"
    elif "Windows NT" in ua:
        os_name = "Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_name = "macOS"
    elif "Linux" in ua:
        os_name = "Linux"

    # Detect Browser
    browser = "Unknown Browser"
    if "Edg/" in ua:
        ver = re.search(r"Edg/(\d+)", ua)
        browser = f"Edge {ver.group(1)}" if ver else "Edge"
    elif "Chrome/" in ua and "Chromium" not in ua:
        ver = re.search(r"Chrome/(\d+)", ua)
        browser = f"Chrome {ver.group(1)}" if ver else "Chrome"
    elif "Firefox/" in ua:
        ver = re.search(r"Firefox/(\d+)", ua)
        browser = f"Firefox {ver.group(1)}" if ver else "Firefox"
    elif "Safari/" in ua and "Chrome" not in ua:
        ver = re.search(r"Version/(\d+)", ua)
        browser = f"Safari {ver.group(1)}" if ver else "Safari"
    elif "curl" in ua:
        browser = "curl"
    elif "python-requests" in ua:
        browser = "Python Requests"

    summary = f"{device_type} ({os_name}; {browser})"
    return {
        "device_type": device_type,
        "os": os_name,
        "browser": browser,
        "summary": summary,
    }


def extract_client_ip(headers, client_address) -> str:
    """Extract true client IP respecting Cloudflare and proxy forwarding headers."""
    # Cloudflare true client IP has highest priority
    cf_ip = headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return cf_ip

    # X-Forwarded-For fallback (take first public hop)
    xff = headers.get("X-Forwarded-For", "").strip()
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[0]

    # Socket peer address
    if client_address and len(client_address) > 0:
        return str(client_address[0])

    return "127.0.0.1"


def format_12hr_timestamp(epoch: float) -> str:
    """Format epoch timestamp to 12-hour AM/PM string."""
    dt = datetime.datetime.fromtimestamp(epoch)
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")


def load_ip_log() -> dict:
    """Load audit log from disk, ensuring directory and permissions exist."""
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    if not os.path.exists(IP_LOG_PATH):
        return {
            "version": 1,
            "retention_days": 30,
            "last_updated": format_12hr_timestamp(time.time()),
            "devices": {},
            "recent_events": [],
        }

    try:
        os.chmod(IP_LOG_PATH, 0o600)
    except OSError:
        pass

    try:
        with open(IP_LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data.get("devices"), dict):
                data["devices"] = {}
            if not isinstance(data.get("recent_events"), list):
                data["recent_events"] = []
            return data
    except Exception:
        return {
            "version": 1,
            "retention_days": 30,
            "last_updated": format_12hr_timestamp(time.time()),
            "devices": {},
            "recent_events": [],
        }


def save_ip_log(data: dict) -> None:
    """Atomically save audit log to log/ip.json with 0o600 permissions."""
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    tmp = IP_LOG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, IP_LOG_PATH)
    try:
        os.chmod(IP_LOG_PATH, 0o600)
    except OSError:
        pass


def purge_expired_records(data: dict, now: float) -> dict:
    """Purge any devices or log events older than 30 days."""
    cutoff = now - RETENTION_SECONDS

    # Filter devices where last seen is older than 30 days
    surviving_devices = {}
    for ip, dev in data.get("devices", {}).items():
        last_epoch = dev.get("last_seen_epoch", 0)
        if last_epoch >= cutoff:
            surviving_devices[ip] = dev
    data["devices"] = surviving_devices

    # Filter recent events
    surviving_events = [
        ev for ev in data.get("recent_events", [])
        if ev.get("epoch", 0) >= cutoff
    ]
    # Keep up to 200 most recent events
    data["recent_events"] = surviving_events[-200:]
    return data


def record_client_access(
    headers,
    client_address,
    path: str,
    method: str = "GET",
    status_code: int = 200,
    authenticated: bool = False,
    auth_reason: str = "ok",
) -> dict:
    """Record an incoming HTTP/WebSocket request in log/ip.json.

    Deduplicates by IP and User-Agent, updates metrics, and purges expired entries.
    """
    now = time.time()
    now_str = format_12hr_timestamp(now)
    ip = extract_client_ip(headers, client_address)

    # Cloudflare Geo Location Headers
    country = headers.get("CF-IPCountry", "Local/Unknown").strip()
    ray = headers.get("CF-Ray", "").strip()
    ray_dc = ray.split("-")[1] if "-" in ray else ("Cloudflare" if ray else "Direct")

    user_agent = headers.get("User-Agent", "").strip()
    dev_info = parse_device_info(user_agent)

    data = load_ip_log()
    data = purge_expired_records(data, now)

    # Device tracking key: IP address
    devices = data["devices"]
    if ip not in devices:
        devices[ip] = {
            "ip": ip,
            "country": country,
            "datacenter": ray_dc,
            "device_type": dev_info["device_type"],
            "device_summary": dev_info["summary"],
            "os": dev_info["os"],
            "browser": dev_info["browser"],
            "user_agent": user_agent,
            "first_seen": now_str,
            "first_seen_epoch": now,
            "last_seen": now_str,
            "last_seen_epoch": now,
            "total_requests": 1,
            "authenticated": authenticated,
            "last_auth_reason": auth_reason,
            "last_path": path,
            "last_status": status_code,
        }
    else:
        existing = devices[ip]
        existing["country"] = country if country != "Local/Unknown" else existing.get("country", country)
        existing["datacenter"] = ray_dc if ray_dc != "Direct" else existing.get("datacenter", ray_dc)
        existing["device_summary"] = dev_info["summary"]
        existing["user_agent"] = user_agent or existing.get("user_agent", "")
        existing["last_seen"] = now_str
        existing["last_seen_epoch"] = now
        existing["total_requests"] = existing.get("total_requests", 0) + 1
        existing["last_path"] = path
        existing["last_status"] = status_code
        if authenticated:
            existing["authenticated"] = True
        existing["last_auth_reason"] = auth_reason

    # Add to recent event stream
    event = {
        "timestamp": now_str,
        "epoch": now,
        "ip": ip,
        "country": country,
        "device": dev_info["summary"],
        "method": method,
        "path": path,
        "status": status_code,
        "authenticated": authenticated,
        "reason": auth_reason,
    }
    data["recent_events"].append(event)
    data["last_updated"] = now_str

    save_ip_log(data)
    return devices[ip]
