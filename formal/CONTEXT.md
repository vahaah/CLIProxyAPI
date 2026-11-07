# Mini hub installation

**HubInstallation** is the native proxy binary, its private configuration, and its launchd service.
The installer owns binary replacement and keeps existing credentials and configuration.
_Code_: `scripts/mini-hub/install.py:main`

**ReleaseCandidate** combines an upstream release with the fork's quota selector and service integration.
Build and regression checks establish whether the candidate can replace the installed binary.

**PrivateState** contains the existing server configuration, secrets, OAuth grants, and Claude/Codex preferences.
An update retains this state and saves the previous binary for recovery.
The installer regenerates `client.json` with the local endpoint and the existing API key.
