# 0002. Persist token totals in a local collector

Status: Accepted
Date: 2026-11-15

## Context

The proxy exposes usage events but does not retain daily history.
An in-memory queue cannot preserve totals across restarts.

## Decisions

### D1. Use a native collector with private persistent storage

Use the verified CPA Usage Keeper v2.1.0 native release.
Keep the proxy and its provider configuration in place.
Use an authenticated loopback listener, SQLite storage, London calendar days, and daily backups.
Start the collector before enabling usage emission through an ETag-checked configuration patch.
Retain raw history without automatic expiry.

### Formal claim

The finite procedure enables emission before committing one record.
A collector restart preserves the committed token total.
The model retains loopback access, authentication, and the existing management key.
It assumes a successful database commit and successful local filesystem operations.

### Category composition

The artifact procedure `EnableTracking` transforms an existing hub into a TrackedHub with a UsageStore.
Effects include filesystem access, network access, process control, environment reads, and time.
Equality compares declared settings and committed event and token totals.
One arrow declares no composite, functor, or monad.

### Implementation correspondence

The [procedure](../usage-tracking.md) maps configuration activation to Enable, SQLite ingestion to Commit, and the restart check to Restart.
The native collector hides subscription handling, schema migrations, deduplication, and daily aggregation.
Callers retain the private data directory and use the separate dashboard password.
Deleting the collector removes durable event storage and requires another persistence implementation.
The official release supplies the persistence implementation. This repository adds no replacement collector.

### Verification

[criterion:USAGE-1] Enable collection, retain daily totals after a restart, and keep the collector private.
Run `python3 formal/check_usage_tracking.py` against the installed hub.
Save committed event identities and daily counters before a collector restart.
Make sure that those identities and counters remain after the restart.
The negative model control makes Restart discard the committed total and must violate DurableTotals.

[Model](../../formal/UsageTracking.tla), [configuration](../../formal/UsageTracking.cfg), and
[control matrix](../../formal/usage-tracking-matrix.json) define the bounded proof.

## Limits and rollback

The model describes deployment order and committed-data retention, not SQLite internals or unlimited delivery guarantees.
Provider accounting, timezone conversion, and deduplication use the native collector's implementation.
Live checks establish installation correspondence independently of the model.
Disable usage emission and stop the collector to roll back.
Keep its data directory for later access.
