# CMUX YOYAKU local canary

This downstream branch carries the explicit-delegation workspace guard and a
separate local terminal build. Upstream contribution: manaflow-ai/cmux#20327.
`agentctl` remains the only Lord/Squire delegation authority. CMUX folders do
not grant roles, and moving or dissolving a folder does not revoke a mandate.

## Source and CI

The fork's downstream default is `yoyaku/lordsquire`; `main` and the upstream PR
branch are preserved. Upstream workflow definitions are archived under
`.github/upstream-workflows`, not activated in this fork. Only the dedicated
YOYAKU workflow runs, on a GitHub-hosted disposable Mac, without production
secrets, publishing or private fleet dependencies. Keep upstream imports in
separate commits and archive any newly introduced workflow before pushing.

The source and workflow revision, executed selected-test count, native result
summary and logs are retained together. A skipped job or zero-test run is not
acceptance. Fork checks do not substitute for upstream required checks.

## Local terminal build

```sh
CMUX_DEV_BACKEND_MODE=local ./scripts/reload.sh --tag yoyaku-lordsquire \
  --name 'CMUX YOYAKU' --release --local-only --no-global-cli-links
```

The explicit local mode permits only that Release tag, rejects auto-login
profiles and credentials, and removes inherited auth fixtures. It does not
authenticate a user, grant a Cloud capability or change Cloud authorization.
The tagged bundle has its own identity, preferences, session and socket. Its
automatic upstream updates are disabled. The official application, its open
sessions and global CLI selection are not installation targets.

Install only a clean committed artifact after native verification, with the
local Developer ID signing and canary installation receipt. The matching Cortex
sidebar must use this host's extension point and a distinct companion identity.
All control operations must resolve the exact host; never fall back to the
official socket when the canary is unavailable.
