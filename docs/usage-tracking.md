# Persistent token totals

The Mini uses [CPA Usage Keeper v2.1.0](https://github.com/Willxup/cpa-usage-keeper/releases/tag/v2.1.0).
The collector subscribes to usage events and stores them in SQLite.
Daily totals use `Europe/London`, including daylight saving changes.
Collection starts when you enable tracking. It cannot recover earlier requests.

## Open the dashboard

Open <http://127.0.0.1:8318> on the Mini.
Copy the separate dashboard password:

```sh
pbcopy < ~/.config/cliproxyapi-mini/usage-keeper-password
```

Sign in and select Today or a date range.
The dashboard shows input, output, cache, and reasoning tokens by model and account.
The listener accepts local connections only.

## EnableTracking

Use the native `darwin_arm64` release on an Apple Silicon Mini.
Examine its archive SHA-256 before installation:

```text
2c59d4d51f5fd00ad12b18b94b41ceecb023bc69735e99fb7b6f599f2d476e5b
```

Keep the executable and its private `.env` under `~/.local/share/cliproxyapi-mini/usage-keeper/`.
Use the existing proxy management key for `CPA_MANAGEMENT_KEY`.
Generate a separate `LOGIN_PASSWORD`.
Use file mode `0600` and directory mode `0700`.

Set these non-secret values in `.env`:

```dotenv
CPA_BASE_URL=http://127.0.0.1:8317
APP_HOST=127.0.0.1
APP_PORT=8318
AUTH_ENABLED=true
TZ=Europe/London
BACKUP_ENABLED=true
BACKUP_INTERVAL=24h
BACKUP_RETENTION_DAYS=7
USAGE_RAW_RETENTION_DAYS=0
CPA_REQUEST_LOG_ACCESS_ENABLED=false
```

Set `WORK_DIR` to the absolute path of the collector's `data` directory.
Register `com.vahaah.cliproxyapi-mini.usage-keeper` as a per-user launchd service.
Use `RunAtLoad`, `KeepAlive`, the private working directory, and umask `0077`.
Start the collector before you enable proxy collection.
Make sure that the collector reports the `subscribe_receiving` state.

Save a private backup of the proxy configuration.
Patch the v8 configuration with its current `ETag` through `If-Match`.
Change only these settings:

```yaml
observability:
  usage:
    usage-statistics-enabled: true
    redis-usage-queue-retention-seconds: 3600
```

The proxy applies these settings without a restart.
Keep full request logs disabled.

## Storage and recovery

The database is `~/.local/share/cliproxyapi-mini/usage-keeper/data/app.db`.
The configuration requests a 24-hour backup interval and seven-day retention.
The collector uses the adjacent `backups` directory.
Examine the backup logs for scheduled runs.
Raw usage records have no automatic expiry.
The user service starts at login and restarts after a crash.

Run the deployment checks:

```sh
python3 formal/check_usage_tracking.py
```

For a restart check, save a private snapshot of recorded events and daily totals:

```sh
python3 formal/check_usage_tracking.py --save-snapshot ~/.config/cliproxyapi-mini/usage-check.json
```

Restart the collector:

```sh
launchctl kickstart -k "gui/$(id -u)/com.vahaah.cliproxyapi-mini.usage-keeper"
```

Compare the snapshot after the collector becomes ready:

```sh
python3 formal/check_usage_tracking.py --snapshot ~/.config/cliproxyapi-mini/usage-check.json
```

The check requires exact saved event counts and allows daily counters to grow.
The snapshot contains at most 100 event rows and 100 daily aggregate rows.
Use SQLite's backup API for a live manual backup.
Do not copy only `app.db` while SQLite has an active WAL.

To disable collection, set `usage-statistics-enabled` to `false` through the v8 configuration API.
Disable automatic startup and stop the collector:

```sh
launchctl disable "gui/$(id -u)/com.vahaah.cliproxyapi-mini.usage-keeper"
launchctl bootout "gui/$(id -u)/com.vahaah.cliproxyapi-mini.usage-keeper"
```

Retain its data directory to keep existing history.
To restore the service, use these commands:

```sh
launchctl enable "gui/$(id -u)/com.vahaah.cliproxyapi-mini.usage-keeper"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.vahaah.cliproxyapi-mini.usage-keeper.plist"
```

Enable proxy collection after the collector becomes ready.
The in-memory proxy queue does not guarantee delivery during an outage or a crash.
Committed SQLite records remain available after a collector restart.
