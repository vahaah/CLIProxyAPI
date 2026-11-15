"""Check the local EnableTracking procedure without printing private fields."""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import plistlib
import shlex
import sqlite3
import subprocess
import urllib.error
import urllib.request


def parse_environment(text):
    environment = {}
    for line in text.splitlines():
        fields = shlex.split(line, comments=True)
        if not fields:
            continue
        if len(fields) != 1 or "=" not in fields[0]:
            raise ValueError("Invalid collector environment assignment")
        name, value = fields[0].split("=", 1)
        environment[name] = value
    return environment


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_tailscale(environment):
    status = json.loads(subprocess.check_output(["tailscale", "serve", "status", "--json"], text=True))
    device = json.loads(subprocess.check_output(["tailscale", "status", "--json"], text=True))
    require(device["BackendState"] == "Running", "Tailscale is not connected")
    host = device["Self"]["DNSName"].rstrip(".")
    site = host + ":" + environment["APP_PORT"]
    target = "http://" + environment["APP_HOST"] + ":" + environment["APP_PORT"]
    require(status["Web"][site]["Handlers"]["/"]["Proxy"] == target, "Dashboard Serve route mismatch")
    require(status["TCP"][environment["APP_PORT"]]["HTTPS"] is True, "Private HTTPS required")
    require(not status.get("AllowFunnel", {}).get(site, False), "Dashboard Funnel is enabled")
    base = "https://" + site
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
    for path in ["/", "/readyz"]:
        with opener.open(base + path, timeout=15) as response:
            require(response.status == 200, "Private HTTPS dashboard unavailable")
    try:
        opener.open(base + "/api/v1/usage/overview?range=today", timeout=15).close()
    except urllib.error.HTTPError as error:
        require(error.code == 401, "Private HTTPS login protection failed")
    else:
        raise ValueError("Private HTTPS usage allows anonymous access")
    request = urllib.request.Request(
        base + "/api/v1/auth/login",
        data=json.dumps({"password": environment["LOGIN_PASSWORD"]}).encode(),
        headers={"Content-Type": "application/json", "X-CPA-Usage-Keeper-Request": "fetch", "Origin": base},
    )
    with opener.open(request, timeout=15) as response:
        require(response.status == 204, "Private HTTPS password login failed")
    require(bool(cookies) and all(cookie.secure for cookie in cookies), "Secure session cookie required")
    for route in ["overview", "analysis", "events"]:
        with opener.open(base + "/api/v1/usage/" + route + "?range=today", timeout=15) as response:
            require(response.status == 200 and isinstance(json.load(response), dict), "Authenticated usage query failed")
    print("ok [control:usage-tailnet]: private Serve route and eight HTTPS checks")


def check_usage_tracking(snapshot_path=None, save_path=None, tailscale=False):
    """[control:usage-live]"""
    home = Path.home()
    config = home / ".config/cliproxyapi-mini"
    keeper = home / ".local/share/cliproxyapi-mini/usage-keeper"
    environment = parse_environment((keeper / ".env").read_text())
    for name, value in {
        "APP_HOST": "127.0.0.1", "APP_PORT": "8318", "AUTH_ENABLED": "true",
        "TZ": "Europe/London", "BACKUP_ENABLED": "true", "BACKUP_INTERVAL": "24h",
        "BACKUP_RETENTION_DAYS": "7", "USAGE_RAW_RETENTION_DAYS": "0",
    }.items():
        require(environment[name] == value, name)
    require(environment["LOGIN_PASSWORD"] != environment["CPA_MANAGEMENT_KEY"], "Separate dashboard password required")
    for path in [keeper, keeper / "data"]:
        require(path.stat().st_mode & 0o077 == 0, "Private directory permissions")
    for path in [keeper / ".env", config / "usage-keeper-password"]:
        require(path.stat().st_mode & 0o077 == 0, "Private file permissions")
    agent = home / "Library/LaunchAgents/com.vahaah.cliproxyapi-mini.usage-keeper.plist"
    service = plistlib.loads(agent.read_bytes())
    require(service["RunAtLoad"] and service["KeepAlive"], "Automatic collector startup required")
    require(service["Umask"] == 0o077, "Private service umask required")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        environment["CPA_BASE_URL"] + "/v8/management/config",
        headers={"Authorization": "Bearer " + environment["CPA_MANAGEMENT_KEY"]},
    )
    with opener.open(request, timeout=15) as response:
        settings = json.load(response)
    usage = settings["observability"]["usage"]
    require(usage["usage-statistics-enabled"] is True, "Usage collection disabled")
    require(usage["redis-usage-queue-retention-seconds"] == 3600, "Usage retention mismatch")
    require(settings["observability"]["logs"]["request-log"] is False, "Full request logging enabled")
    dashboard_url = "http://" + environment["APP_HOST"] + ":" + environment["APP_PORT"]
    bindings = subprocess.check_output([
        "/usr/sbin/lsof", "-nP", "-iTCP:" + environment["APP_PORT"], "-sTCP:LISTEN", "-Fn",
    ], text=True)
    listeners = [line[1:] for line in bindings.splitlines() if line.startswith("n")]
    require(listeners == [environment["APP_HOST"] + ":" + environment["APP_PORT"]], "Collector listener is not private")
    with opener.open(dashboard_url + "/readyz", timeout=15) as response:
        require(response.status == 200, "Collector not ready")
    try:
        opener.open(dashboard_url + "/api/v1/usage/overview", timeout=15).close()
    except urllib.error.HTTPError as error:
        require(error.code == 401, "Dashboard requires a login")
    else:
        raise ValueError("Dashboard allows unauthenticated usage access")
    database = keeper / "data/app.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "SQLite integrity failure")
        events, tokens = connection.execute("SELECT COUNT(*), COALESCE(SUM(total_tokens), 0) FROM usage_events").fetchone()
        days, daily_tokens = connection.execute("SELECT COUNT(DISTINCT bucket_start), COALESCE(SUM(total_tokens), 0) FROM usage_overview_daily_stats").fetchone()
        snapshot = {
            "events": connection.execute("SELECT event_key,input_tokens,output_tokens,total_tokens FROM usage_events ORDER BY id DESC LIMIT 100").fetchall(),
            "daily": connection.execute("SELECT id,request_count,input_tokens,output_tokens,total_tokens FROM usage_overview_daily_stats ORDER BY id DESC LIMIT 100").fetchall(),
        }
        if snapshot_path:
            saved = json.loads(snapshot_path.read_text())
            require(saved["events"] and saved["daily"], "A restart snapshot needs recorded usage")
            for event_key, *counts in saved["events"]:
                actual = connection.execute("SELECT input_tokens,output_tokens,total_tokens FROM usage_events WHERE event_key=?", (event_key,)).fetchone()
                require(actual is not None and list(actual) == counts, "Committed event changed after restart")
            for row_id, *counts in saved["daily"]:
                actual = connection.execute("SELECT request_count,input_tokens,output_tokens,total_tokens FROM usage_overview_daily_stats WHERE id=?", (row_id,)).fetchone()
                require(actual is not None and all(a >= b for a, b in zip(actual, counts)), "Daily counter decreased after restart")
    require(events > 0 and tokens > 0, "A real token record must reach persistent storage")
    require(days > 0 and daily_tokens > 0, "Daily totals must contain real token usage")
    if save_path:
        save_path.write_text(json.dumps(snapshot))
        save_path.chmod(0o600)
    if tailscale:
        check_tailscale(environment)
    print("ok: usage tracking, private access, SQLite integrity, and persistent daily totals")


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-snapshot", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--tailscale", action="store_true")
    arguments = parser.parse_args()
    check_usage_tracking(arguments.snapshot, arguments.save_snapshot, arguments.tailscale)
