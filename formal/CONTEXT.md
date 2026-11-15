# Mini hub installation

**HubInstallation** is the native proxy binary, its private configuration, and its launchd service.
The installer owns binary replacement and keeps existing credentials and configuration.
_Code_: `scripts/mini-hub/install.py:main`

**ReleaseCandidate** combines an upstream release with the fork's quota selector and service integration.
Build and regression checks establish whether the candidate can replace the installed binary.

**PrivateState** contains the existing server configuration, secrets, OAuth grants, and Claude/Codex preferences.
An update retains this state and saves the previous binary for recovery.
The installer regenerates `client.json` with the local endpoint and the existing API key.

# Persistent usage tracking

**TrackedHub** is the existing proxy with usage collection enabled and a local authenticated collector.
The deployment procedure changes usage settings without replacing existing provider credentials.

**UsageStore** is the collector's private SQLite database, daily aggregates, and scheduled backups.
The collector owns its schema and commits incoming usage events before aggregation.

**DailyTotal** is the sum of recorded usage for a calendar day in `Europe/London`.
A collector restart preserves committed records and their aggregates.

# Service shutdown

**ShutdownBudget** selects immediate HTTP closure or a bounded interval for active requests to finish.
The API server owns this policy.

**ActiveRequest** is an accepted HTTP request that can still use Home execution resources.
It completes or loses its connection before the service closes those resources.

**ExecutionResources** are the Home subscriber lifetime, execution registry, and request-owned upstream contexts.
The service retains them through HTTP draining and closes them during final teardown.
