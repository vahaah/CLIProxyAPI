"""Check the local EnableTracking procedure without printing private fields."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import sqlite3
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


def check_usage_tracking(snapshot_path=None, save_path=None):
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
    print("ok: usage tracking, private access, SQLite integrity, and persistent daily totals")


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-snapshot", type=Path)
    parser.add_argument("--snapshot", type=Path)
    arguments = parser.parse_args()
    check_usage_tracking(arguments.snapshot, arguments.save_snapshot)
