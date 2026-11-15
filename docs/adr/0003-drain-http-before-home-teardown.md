# 0003. Drain HTTP before Home teardown

Status: Accepted
Date: 2026-11-15

## Context

The API server supports an optional shutdown budget.
Closing the Home registry first cancels the resources that active requests still need.
A timer-based test can release its request before shutdown starts.

## Decisions

### D1. HTTP shutdown precedes execution resource teardown

Reject new HTTP requests.
Apply the configured drain policy before resource teardown.
Retain the service-owned Home lifetime until this HTTP stop finishes.
A zero budget still closes HTTP connections immediately.
An exhausted budget closes the remaining HTTP connections before resource teardown.
Use the HTTP shutdown callback to release the API regression handler.

### Formal claim

The model keeps `ExecutionResources` available while HTTP remains open for an `ActiveRequest`.
The graceful case completes that request before teardown.
The immediate case skips the drain.
The exhausted case closes HTTP before teardown.
The model represents budgets as outcomes, not elapsed time.

### Category composition

The artifact arrow `Shutdown` transforms an active service into a stopped service.
Effects include network closure, process cancellation, clocks, and resource cleanup.
Equality compares the HTTP outcome and execution-resource lifetime.
One arrow declares no composite, functor, or monad.

### Implementation correspondence

`Service.Shutdown` calls `Server.Stop` before it cancels service workers or closes the Home registry.
`Service.Run` gives Home an independent lifetime and ends it through explicit shutdown.
`Server.Stop` implements completion, immediate closure, and budget exhaustion.
The API test uses `RegisterOnShutdown` to release its handler after shutdown starts.
Callers must repeat these lifetime rules if the service does not manage the shutdown order.
The Home wire protocol does not change.

### Verification

[criterion:SHUTDOWN-1] Retain Home resources through the HTTP drain.
Close them after completion or forced HTTP closure.
Run the API shutdown tests and `TestServiceShutdownRetainsHomeResourcesDuringHTTPDrain` with the race detector.
The SDK regression requires an exact HTTP 200 body for completion and connection errors for immediate and exhausted budgets.
The negative control cancels resources in RequestShutdown and must violate ResourcesOutliveHTTP.

[Model](../../formal/ShutdownSafety.tla), [configuration](../../formal/ShutdownSafety.cfg), and
[control matrix](../../formal/shutdown-safety-matrix.json) define the executable contract.

## Consequences and limits

Home resources remain usable until the HTTP drain ends.
The model assumes live resources at shutdown and does not prove clock bounds or third-party behavior.
Go integration checks establish implementation correspondence.
Revert the shutdown change to roll back.
