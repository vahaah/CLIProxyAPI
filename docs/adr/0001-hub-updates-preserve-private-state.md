# 0001. Hub updates preserve private state and the fork's quota routing

Status: Accepted
Date: 2026-11-07

## Context

The existing Mini hub builds this fork because official binaries omit its quota selector.
The installer keeps credentials outside the repository and replaces the binary with `os.replace`.
This record describes that existing contract.

## Decisions

### D1. Updates build the fork and retain private state

Merge a stable upstream release into the fork before installation.
Keep the custom selector and existing private configuration.
Save the installed binary before replacement.
Regenerate `client.json` with the local endpoint and the existing API key.
Retain server configuration, secrets, OAuth grants, and Claude/Codex preferences.

### Formal claim

The finite model retains configuration and credential identities through successful and failed builds, binary replacement, and service restart.
Replacement requires a successful build and saves the old binary.
The model assumes an existing installation, successful file operations after compilation, and one updater.
It does not prove crash durability or provider behavior.

### Category composition

`main` maps a ReleaseCandidate and HubInstallation to an updated HubInstallation through an artifact transformation.
Its effects include file access, build and launchd processes, environment reads, time, and optional panel download.
Observable equality compares installed binary bytes, previous binary bytes, and private state bytes.
This contract declares one arrow and no composite, functor, or monad.
An unchanged installation is the identity observation.
The installer keeps its effect order.

### Implementation correspondence

`main` builds a `.new` binary before `shutil.copy2` and `os.replace` change the installed binary.
The model maps these operations to Build, Reject, Install, and Restart.
Existing server configuration and secrets bypass initial creation.
The installer rewrites generated client connection settings in `client.json`.
The abstraction hides build commands and file replacement details from the caller.

The caller supplies the repository, Go toolchain, and existing private installation.

Deleting the installer moves credential preservation, backup, and service lifecycle rules into each update command.
The Python controls execute the existing installer in a temporary home with fake build and launchd processes.
Go regression checks separately establish request, authentication, and quota selector behavior.

### Verification

[criterion:SYNC-1] The candidate includes upstream `v8.0.59` and retains the fork's quota routing and installation contract.

> Note 2026-11-10: SYNC-1 also covers later stable release merges. `MINI_HUB.md` records the current release. D1 still applies.

[Model](../../formal/UpstreamSync.tla), [configuration](../../formal/UpstreamSync.cfg),
[control matrix](../../formal/upstream-sync-matrix.json), and [installer controls](../../formal/check_upstream_sync.py) define executable evidence.
Run `python3 formal/check_upstream_sync.py`, `go test ./...`, and the quota routing race tests.
Run `astack check proof` against the fixed pre-update commit.
The negative control makes Install replace the existing configuration and must violate PrivateStatePreserved.

## Consequences

Source builds retain routing behavior across upstream releases.
Updates require merge review, regression checks, and a working Go toolchain.
Binary rollback does not roll back credential rotations or journal writes.

## Alternatives considered

- Official release binaries remove the custom quota selector.
- Recreating configuration during an update can lose private keys and existing client settings.
