#!/usr/bin/env python3
"""Install only the committed, separately identified YOYAKU local application."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid

IDENTITY = "com.cmuxterm.app.debug.yoyaku.lordsquire"
TEAM = "YZYJJPX484"
SIGNER = "Developer ID Application: YOYAKU (YZYJJPX484)"
HOME = Path.home()
DESTINATION = HOME / "Applications/CMUX YOYAKU.app"
STATE = HOME / ".local/state/cmux-yoyaku"


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def info_at(app):
    with (app / "Contents/Info.plist").open("rb") as stream:
        return plistlib.load(stream)


def validate_source(info, sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("a full source SHA is required")
    if info.get("CFBundleIdentifier") != IDENTITY:
        raise ValueError("refusing an official or foreign application")
    if info.get("CMUXYOYAKULaunchMode") != "local" or info.get("CMUXYOYAKUSourceDirty") is not False:
        raise ValueError("only a committed clean local-mode artifact can be installed")
    if info.get("CMUXYOYAKUSourceSHA") != sha:
        raise ValueError("source revision mismatch")


def verify_signature(app):
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    requirement = f'anchor apple generic and identifier "{IDENTITY}" and certificate leaf[subject.OU] = "{TEAM}"'
    run("/usr/bin/codesign", "--verify", "-R", "=" + requirement, str(app))


def sign(app):
    magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf"}
    paths = sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            is_macho = stream.read(4) in magic
        if is_macho:
            run("/usr/bin/codesign", "--force", "--sign", SIGNER, "--options", "runtime",
                "--timestamp", "--preserve-metadata=entitlements", str(path))
    for path in paths + [app]:
        if path.is_dir() and not path.is_symlink() and path.suffix in {".app", ".appex", ".framework", ".xpc", ".bundle", ".systemextension"}:
            run("/usr/bin/codesign", "--force", "--sign", SIGNER, "--options", "runtime",
                "--timestamp", "--preserve-metadata=entitlements", str(path))
    verify_signature(app)


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def stop_owned_canary():
    executable = str(DESTINATION / "Contents/MacOS/cmux")
    def pids():
        found = []
        for line in run("/bin/ps", "-axo", "pid=,command=").splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and (fields[1] == executable or fields[1].startswith(executable + " ")):
                found.append(int(fields[0]))
        return found
    if pids():
        run("/usr/bin/osascript", "-e", f'tell application id "{IDENTITY}" to quit')
        until = time.monotonic() + 15
        while pids() and time.monotonic() < until:
            time.sleep(0.2)
        if pids():
            raise RuntimeError("the owned canary did not quit; installation refused")


def install(source, sha, launch):
    validate_source(info_at(source), sha)
    DESTINATION.parent.mkdir(exist_ok=True)
    if DESTINATION.is_symlink():
        raise ValueError("refusing a symlink installation target")
    if DESTINATION.exists():
        verify_signature(DESTINATION)
    generation = uuid.uuid4().hex
    staging = DESTINATION.parent / f".cmux-yoyaku-{generation}.app"
    run("/usr/bin/ditto", str(source), str(staging))
    try:
        info = info_at(staging)
        env = info.setdefault("LSEnvironment", {})
        env["CMUX_BUNDLE_ID"] = IDENTITY
        env["CMUX_BUNDLED_CLI_PATH"] = str(DESTINATION / "Contents/Resources/bin/cmux")
        env["CMUX_SHELL_INTEGRATION_DIR"] = str(DESTINATION / "Contents/Resources/shell-integration")
        with (staging / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(info, stream)
        sign(staging)
        # These files prevent native fallback to the official host's settings.
        config = HOME / ".config/cmux-yoyaku/cmux.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        if not config.exists():
            with config.open("x") as stream:
                json.dump({"schemaVersion": 1, "automation": {"socketControlMode": "automation"}}, stream)
        ghostty = HOME / "Library/Application Support" / IDENTITY / "config.ghostty"
        ghostty.parent.mkdir(parents=True, exist_ok=True)
        if not ghostty.exists():
            ghostty.write_text("# CMUX YOYAKU isolated local configuration\n")
        stop_owned_canary()
        backup = STATE / "backups" / f"{generation}.app"
        backup.parent.mkdir(exist_ok=True)
        prior = json.loads((STATE / "accepted.json").read_text()) if (STATE / "accepted.json").exists() else None
        if DESTINATION.exists():
            DESTINATION.replace(backup)
        try:
            staging.replace(DESTINATION)
            verify_signature(DESTINATION)
            receipt = {"generation": generation, "source_sha": sha, "bundle_id": IDENTITY, "team": TEAM,
                       "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "destination": str(DESTINATION), "backup": str(backup) if backup.exists() else None,
                       "previous": prior,
                       "info_sha256": hashlib.sha256((DESTINATION / "Contents/Info.plist").read_bytes()).hexdigest()}
            atomic_json(STATE / f"{generation}.json", receipt)
            atomic_json(STATE / "accepted.json", receipt)
        except BaseException:
            if DESTINATION.exists():
                DESTINATION.replace(STATE / f"failed-{generation}.app")
            if backup.exists():
                backup.replace(DESTINATION)
            raise
        if launch:
            run("/usr/bin/open", "-g", "-n", str(DESTINATION))
        print(json.dumps({"installed": True, "source_sha": sha, "receipt": str(STATE / f"{generation}.json")}))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def rollback(receipt_path):
    receipt = json.loads(receipt_path.read_text())
    accepted = json.loads((STATE / "accepted.json").read_text())
    if receipt.get("generation") != accepted.get("generation") or receipt.get("bundle_id") != IDENTITY:
        raise ValueError("rollback receipt is not the current canary generation")
    backup = Path(receipt.get("backup") or "/nonexistent")
    if backup.parent.resolve() != (STATE / "backups").resolve() or backup.is_symlink():
        raise ValueError("rollback backup is outside the owned archive")
    verify_signature(backup)
    stop_owned_canary()
    DESTINATION.replace(STATE / f"rolled-back-{uuid.uuid4().hex}.app")
    backup.replace(DESTINATION)
    if receipt.get("previous"):
        atomic_json(STATE / "accepted.json", receipt["previous"])
    print(json.dumps({"restored": True, "bundle_id": IDENTITY}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    delivery = actions.add_parser("install")
    delivery.add_argument("--source", type=Path, required=True)
    delivery.add_argument("--sha", required=True)
    delivery.add_argument("--launch", action="store_true")
    recovery = actions.add_parser("rollback")
    recovery.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "install":
            install(args.source.resolve(strict=True), args.sha, args.launch)
        else:
            rollback(args.receipt.resolve(strict=True))
