#!/usr/bin/env python3
"""Record source identity and opt this separately installed local tag out of Sparkle."""
import pathlib
import plistlib
import subprocess
import sys


def configure(info, sha, dirty):
    if info.get("CFBundleIdentifier") != "com.cmuxterm.app.debug.yoyaku.lordsquire":
        raise ValueError("local artifact must have the exact YOYAKU canary identity")
    info.update(CMUXYOYAKULaunchMode="local", CMUXYOYAKUSourceSHA=sha,
                CMUXYOYAKUSourceDirty=dirty, SUEnableAutomaticChecks=False,
                SUAutomaticallyUpdate=False, SUAllowsAutomaticUpdates=False)
    info.pop("SUFeedURL", None)
    return info


if __name__ == "__main__":
    path, repo = map(pathlib.Path, sys.argv[1:])
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True))
    with path.open("rb") as stream:
        info = configure(plistlib.load(stream), sha, dirty)
    with path.open("wb") as stream:
        plistlib.dump(info, stream)
