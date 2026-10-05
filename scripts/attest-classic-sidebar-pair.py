#!/usr/bin/env python3
"""Attest the canonical dogfood pair from its existing build receipts.

Read-only: never signs, installs, launches, registers an extension or changes
permissions. A sealed receipt is not a live-runtime readiness receipt.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import stat
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tarfile


class PairVerificationError(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise PairVerificationError(message)


def run(arguments):
    return subprocess.run(arguments, check=True, capture_output=True,
        text=True, timeout=30)


def signature(path, expected_team_id, runner=run):
    runner(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)])
    display = runner(["/usr/bin/codesign", "--display", "--verbose=4", str(path)])
    output = display.stdout + display.stderr
    require("Signature=adhoc" not in output, "ad-hoc signature refused")
    authority = next((line.removeprefix("Authority=") for line in output.splitlines()
        if line.startswith("Authority=Developer ID Application:")), None)
    team = next((line.removeprefix("TeamIdentifier=") for line in output.splitlines()
        if line.startswith("TeamIdentifier=")), None)
    cdhash = next((line.removeprefix("CDHash=") for line in output.splitlines()
        if line.startswith("CDHash=")), None)
    require(authority is not None and team == expected_team_id,
        "Developer ID authority or expected team mismatch")
    require(cdhash is not None and re.fullmatch(r"[0-9a-f]{40,64}", cdhash),
        "missing code directory hash")
    requirement = runner(["/usr/bin/codesign", "--display", "--requirements", "-", str(path)])
    text = requirement.stdout + requirement.stderr
    designated = next((line.partition("designated => ")[2] for line in text.splitlines()
        if "designated => " in line), "")
    require(designated and not designated.startswith("cdhash "),
        "unstable designated requirement refused")
    return {"authority": authority, "teamID": team, "cdhash": cdhash,
        "designatedRequirement": designated, "strictVerified": True}


def bundle(path, expected_id, team, runner):
    path = Path(path).resolve(strict=True)
    info_path = path / "Contents/Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    require(info["CFBundleIdentifier"] == expected_id, "bundle identity mismatch")
    executable = (path / "Contents/MacOS" / info["CFBundleExecutable"]).resolve(strict=True)
    require(executable.is_relative_to(path), "executable escapes its signed bundle")
    seal = path / "Contents/_CodeSignature/CodeResources"
    require(seal.is_file(), "signed resource seal missing")
    return info, {"appPath": str(path), "bundleID": expected_id,
        "executablePath": str(executable), "executableSHA256": file_hash(executable),
        "infoPlistSHA256": file_hash(info_path), "resourceSealSHA256": file_hash(seal),
        "signature": signature(path, team, runner)}


def sdk_proof(source, revision, materialized):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "SDK revision must be exact")
    prefix = "Packages/macOS/CmuxExtensionKit"
    result = subprocess.run(["/usr/bin/git", "-C", str(source), "archive",
        revision, prefix], check=True, capture_output=True, timeout=30)
    require(len(result.stdout) <= 8 * 1024 * 1024, "SDK archive exceeds bound")
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive:
            if member.isdir():
                continue
            require(member.isfile(), "SDK archive contains a link or special file")
            relative = Path(member.name).relative_to(prefix)
            expected[str(relative)] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    materialized = Path(materialized)
    require((materialized / ".source-revision").read_text().strip() == revision,
        "compiled SDK pin marker mismatch")
    actual = {}
    for path in materialized.rglob("*"):
        relative = path.relative_to(materialized)
        if relative.parts[0] in {".build", ".git"} or str(relative) in {".source-revision", "VENDORED.md"}:
            continue
        require(not path.is_symlink(), "materialized SDK contains a symlink")
        if path.is_file():
            actual[str(relative)] = file_hash(path)
    require(actual == expected, "compiled SDK differs from its exact source object")
    tree = subprocess.run(["/usr/bin/git", "-C", str(source), "rev-parse",
        revision + ":" + prefix], check=True, capture_output=True, text=True,
        timeout=30).stdout.strip()
    return {"revision": revision, "source": str(Path(source).resolve(strict=True)),
        "treeSHA": tree, "filesFingerprint": fingerprint(expected)}


def requested_manifest(source_path, revision, extension_info):
    """Read only the exact built source object; no model-supplied scope list."""
    text = run(["/usr/bin/git", "-C", str(source_path), "show", revision +
        ":Sources/CortexSessionsExtension/CortexSessionsExtension.swift"]).stdout
    def scopes(name):
        match = re.search(name + r":\s*\[([^]]+)\]", text)
        require(match is not None, "built extension scope declaration missing")
        values = re.findall(r"\.([A-Za-z][A-Za-z0-9]*)", match.group(1))
        require(values and len(values) <= 64 and len(values) == len(set(values)),
            "built extension scope declaration invalid")
        return sorted(values)
    require("minimumAPIVersion: .sidebarV2_3" in text,
        "built extension does not declare the required API 2.3")
    identifier = extension_info.get("CortexSessionsExtensionIdentifier")
    require(isinstance(identifier, str) and identifier, "signed manifest identity missing")
    return {"id": identifier, "apiVersion": {"major": 2, "minor": 3},
        "readScopes": scopes("readScopes"), "actionScopes": scopes("actionScopes"),
        "sourcePath": "Sources/CortexSessionsExtension/CortexSessionsExtension.swift",
        "sourceSHA256": hashlib.sha256(text.encode()).hexdigest()}


def attest(pair_path, host_path, source_path, expected_team_id="YZYJJPX484", runner=run):
    pair = json.loads(Path(pair_path).read_text())
    host_receipt = json.loads(Path(host_path).read_text())
    sources = json.loads(Path(source_path).read_text())
    tag = pair["tag"]
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", tag), "invalid dogfood tag")
    require(pair["mainLaunchAllowed"] is False and pair["productionInstallAllowed"] is False
        and pair["hostFallbackAllowed"] is False, "production activation or fallback refused")
    require(sources["productionActivationAllowed"] is False
        and sources["sourceArchivesIncludeForeignWIP"] is False, "source isolation proof missing")
    require(pair["mainBundleID"] == "fr.yoyaku.cortex.dogfood." + tag,
        "Cortex tag identity mismatch")
    expected_host_id = "com.cmuxterm.app.debug." + tag.replace("-", ".")
    require(host_receipt["bundleID"] == expected_host_id, "CMUX tag identity mismatch")
    require(pair["extensionPoint"] == expected_host_id + ".cmux.sidebar",
        "paired extension point mismatch")
    require(pair["extensionBundleID"] == pair["mainBundleID"] + ".sessions",
        "extension parent identity mismatch")
    require(str(pair["cortexGitDirty"]).lower() == "false", "dirty Cortex build refused")
    require(pair["cortexGitSHA"] == sources["CortexArtifactSHA"]
        and host_receipt["hostGitSHA"] == sources["cmuxArtifactSHA"],
        "built source heads differ from immutable source receipt")
    host_info, host = bundle(host_receipt["appPath"], expected_host_id, expected_team_id, runner)
    cortex_info, cortex = bundle(pair["appPath"], pair["mainBundleID"], expected_team_id, runner)
    extension_info, extension = bundle(pair["extensionPath"], pair["extensionBundleID"], expected_team_id, runner)
    require(Path(extension["appPath"]).is_relative_to(Path(cortex["appPath"])),
        "extension escapes its signed parent")
    require(cortex_info.get("CortexDogfoodMainLaunchDisabled") is True,
        "tagged main launch guard missing")
    require(cortex_info.get("CortexGitSHA") == pair["cortexGitSHA"]
        and str(cortex_info.get("CortexGitDirty")).lower() == "false",
        "signed Cortex provenance mismatch")
    require(cortex_info.get("CortexPairedSDKRevision") == pair["sdkRevision"],
        "signed Cortex SDK revision mismatch")
    require(extension_info["EXAppExtensionAttributes"]["EXExtensionPointIdentifier"]
        == pair["extensionPoint"], "signed extension point mismatch")
    env = host_info.get("LSEnvironment", {})
    host_root = Path(host["appPath"])
    cli = host_root / "Contents/Resources/bin/cmux"
    shells = host_root / "Contents/Resources/shell-integration"
    require(cli.is_file() and shells.is_dir(), "bundled execution resources missing")
    require(Path(env.get("CMUX_BUNDLED_CLI_PATH", "")).resolve() == cli.resolve()
        and Path(env.get("CMUX_SHELL_INTEGRATION_DIR", "")).resolve() == shells.resolve(),
        "bundled execution paths are stale or escape the current host")
    require(Path(host_receipt["cliPath"]).resolve() == cli.resolve()
        and host_receipt["cliSHA256"] == file_hash(cli), "bundled CLI receipt mismatch")
    socket = "/tmp/cmux-debug-" + tag + ".sock"
    require(env.get("CMUX_SOCKET_PATH") == socket and env.get("CMUX_TAG") == tag
        and env.get("CMUX_BUNDLE_ID") == expected_host_id, "host socket isolation mismatch")
    sdk = sdk_proof(pair["sdkSource"], pair["sdkRevision"],
        Path(source_path).parent / "Cortex/Vendor/cmux-extension-kit")
    # The host and compiled extension must use the same interface tree.
    host_sdk_tree = subprocess.run(["/usr/bin/git", "-C", pair["sdkSource"],
        "rev-parse", sources["cmuxArtifactSHA"] + ":Packages/macOS/CmuxExtensionKit"],
        check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    require(host_sdk_tree == sdk["treeSHA"], "host and extension SDK interface trees differ")
    execution = {"cliPath": str(cli.resolve()), "cliSHA256": file_hash(cli),
        "shellIntegrationPath": str(shells.resolve()), "socketPath": socket,
        "hostAppPath": host["appPath"], "cortexAppPath": cortex["appPath"],
        "extensionPath": extension["appPath"], "sdkSource": sdk["source"],
        "cmuxAPIMinimum": pair["cmuxAPIMinimum"]}
    sealed = {"tag": tag, "extensionPoint": pair["extensionPoint"],
        "host": host, "cortex": cortex, "extension": extension, "sdk": sdk,
        "cmuxGitSHA": sources["cmuxArtifactSHA"], "cortexGitSHA": sources["CortexArtifactSHA"],
        "sourceReceiptSHA256": file_hash(source_path),
        "extensionManifest": requested_manifest(Path(source_path).parent / "Cortex",
            sources["CortexArtifactSHA"], extension_info)}
    return {"schemaVersion": 1, "verificationState": "sealed",
        "verified_pair_fingerprint": fingerprint(sealed),
        "executable_config_fingerprint": fingerprint(execution),
        "pair": sealed, "execution": execution, "runtimeVerified": False,
        "productionInstallAllowed": False, "mainLaunchAllowed": False,
        "producerSHA256": file_hash(__file__),
        "buildInputs": {"pairConfig": str(Path(pair_path).resolve(strict=True)),
            "hostReceipt": str(Path(host_path).resolve(strict=True)),
            "sourceReceipt": str(Path(source_path).resolve(strict=True)),
            "expectedTeamID": expected_team_id}}


def revalidate(manifest_path, runner=run):
    """Recheck actual files and signatures; never trust a model-made receipt."""
    previous = json.loads(Path(manifest_path).read_text())
    require(previous.get("verificationState") == "sealed"
        and previous.get("runtimeVerified") is False, "unsupported readiness claim")
    inputs = previous["buildInputs"]
    actual = attest(inputs["pairConfig"], inputs["hostReceipt"],
        inputs["sourceReceipt"], inputs["expectedTeamID"], runner)
    require(actual == previous, "pair changed after immutable attestation")
    return actual


def process_executable(pid):
    require(type(pid) is int and pid > 0, "invalid native process ID")
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    path = ctypes.create_string_buffer(4096)
    count = library.proc_pidpath(pid, path, len(path))
    require(count > 0, "native process disappeared")
    return str(Path(path.value.decode()).resolve(strict=True))


def process_identity(pid):
    executable = process_executable(pid)
    started = run(["/bin/ps", "-p", str(pid), "-o", "lstart="]).stdout.strip()
    require(started, "native process start identity missing")
    return {"pid": pid, "executablePath": executable,
        "startedAt": started}


def extension_processes(executable):
    # Iterate process IDs, then resolve kernel executable paths. Never match a
    # command-line substring containing a model or conversation argument.
    pids = run(["/bin/ps", "-axo", "pid="]).stdout.split()
    found = []
    for value in pids:
        try:
            pid = int(value)
            if process_executable(pid) != executable:
                continue
            identity = process_identity(pid)
        except (PairVerificationError, FileNotFoundError, subprocess.CalledProcessError):
            continue
        if identity["executablePath"] == executable:
            found.append(identity)
    require(0 < len(found) <= 16, "paired extension process is not running")
    return sorted(found, key=lambda item: item["pid"])


def require_socket(path):
    require(stat.S_ISSOCK(Path(path).stat().st_mode), "isolated native socket is not present")


def observe_runtime(sealed, runner=run, process_reader=process_identity,
                    process_finder=extension_processes):
    """Verify existing native status, exact socket and actual process identities.

    Old hosts without effective grant telemetry remain fail closed. Configured
    preferences are deliberately separate from in-memory, generation-bound grants.
    """
    execution, pair = sealed["execution"], sealed["pair"]
    socket = execution["socketPath"]
    require_socket(socket)
    def rpc(method):
        output = runner([execution["cliPath"], "--socket", socket, "--json", "rpc", method, "{}"]).stdout
        require(len(output.encode()) <= 256 * 1024, "runtime metadata exceeds bound")
        value = json.loads(output)
        if "result" in value:
            require(value.get("ok") is not False, "native RPC failed")
            value = value["result"]
        require(isinstance(value, dict), "native metadata result is not an object")
        return value
    identify = rpc("system.identify")
    for key, expected in {"app_bundle_path": pair["host"]["appPath"],
        "app_executable_path": pair["host"]["executablePath"],
        "app_cli_path": execution["cliPath"], "bundle_identifier": pair["host"]["bundleID"],
        "socket_path": socket}.items():
        require(identify.get(key) == expected, "live host identity mismatch: " + key)
    capabilities = rpc("system.capabilities")
    require(capabilities.get("socket_path") == socket, "capability socket mismatch")
    methods = capabilities.get("methods", [])
    required = {"system.identify", "system.capabilities", "extension.sidebar.status",
        "extension.sidebar.snapshot", "workspace.context.export", "workspace.context.import"}
    require(required.issubset(set(methods)), "native contract methods missing")
    status = rpc("extension.sidebar.status")
    require(status.get("provider_id") == "cmux.sidebar.extensions"
        and status.get("provider_active") is True and status.get("connected") is True
        and status.get("selected_bundle_id") == pair["extension"]["bundleID"],
        "paired extension is not selected and snapshot-acknowledged")
    hosts = status.get("hosts")
    require(isinstance(hosts, list) and 0 < len(hosts) <= 16, "live host inventory invalid")
    manifest = pair["extensionManifest"]
    telemetry = []
    for host in hosts:
        require(host.get("bundle_id") == pair["extension"]["bundleID"]
            and host.get("state") == "connected", "selected host is not connected")
        fields = {"manifest_id", "api_version", "connection_generation", "grant_revision",
            "effective_read_scopes", "effective_action_scopes"}
        require(fields.issubset(host), "runtime_grants_not_observable")
        require(host["manifest_id"] == manifest["id"]
            and host["api_version"] == manifest["apiVersion"], "runtime manifest/API mismatch")
        for field in ["generation", "connection_generation", "grant_revision"]:
            require(type(host.get(field)) is int and host[field] >= 0, "invalid runtime generation fence")
        require(sorted(host["effective_read_scopes"]) == manifest["readScopes"]
            and sorted(host["effective_action_scopes"]) == manifest["actionScopes"],
            "runtime effective grant is incomplete or differs from built manifest")
        native = process_reader(host.get("pid"))
        require(native["executablePath"] == pair["host"]["executablePath"],
            "connected host process is not the sealed executable")
        native_signature = signature(str(native["pid"]), pair["host"]["signature"]["teamID"], runner)
        require(native_signature == pair["host"]["signature"], "running host signature differs from sealed pair")
        native["signature"] = native_signature
        telemetry.append({key: host[key] for key in ["host_id", "bundle_id", "identity_id", "generation",
            "manifest_id", "api_version", "connection_generation", "grant_revision",
            "effective_read_scopes", "effective_action_scopes"]} | {"process": native})
    host_pids = {row["process"]["pid"] for row in telemetry}
    require(len(host_pids) == 1, "multiple native host processes refused")
    pid = next(iter(host_pids))
    sockets = runner(["/usr/sbin/lsof", "-a", "-p", str(pid), "-U", "-Fn"]).stdout.splitlines()
    require("n" + socket in sockets, "selected process does not own the isolated socket")
    extensions = process_finder(pair["extension"]["executablePath"])
    require(all(item["executablePath"] == pair["extension"]["executablePath"] for item in extensions)
        and 0 < len(extensions) <= 16, "paired extension executable is not running")
    for extension in extensions:
        native_signature = signature(str(extension["pid"]), pair["extension"]["signature"]["teamID"], runner)
        require(native_signature == pair["extension"]["signature"], "running extension signature differs from sealed pair")
        extension["signature"] = native_signature
    configured_data = runner(["/usr/bin/defaults", "export", pair["host"]["bundleID"], "-"]).stdout
    require(len(configured_data.encode()) <= 1024 * 1024, "configured preferences exceed bound")
    preferences = plistlib.loads(configured_data.encode())
    raw = preferences.get("cmuxExtensionSidebar.grants.v1")
    require(isinstance(raw, bytes), "configured grant record missing")
    configured = json.loads(raw).get(pair["extension"]["bundleID"])
    require(isinstance(configured, dict), "configured paired grant record missing")
    require(configured.get("manifestID") == manifest["id"]
        and configured.get("apiVersion") == manifest["apiVersion"], "configured manifest identity mismatch")
    configured = {key: configured[key] for key in ["manifestID", "apiVersion", "readScopes", "actionScopes"]}
    # Read status again after filesystem/process checks. Any reconnect, revoke,
    # revision change or replacement invalidates this sample rather than racing readiness.
    after = rpc("extension.sidebar.status")
    require(after == status, "native connection or grant changed during runtime verification")
    runtime = {"socketPath": socket, "hostProcess": telemetry[0]["process"],
        "extensionProcesses": extensions, "hosts": sorted(telemetry, key=lambda item: item["host_id"]),
        "configuredGrant": configured, "configuredGrantFingerprint": fingerprint(configured),
        "effectiveGrantSource": "native-host-memory-at-connection-generation",
        "apiVersion": manifest["apiVersion"], "nativeMethods": sorted(required),
        "socketProtocolVersion": capabilities.get("version")}
    return runtime


def attest_live(sealed_path, runner=run, process_reader=process_identity,
                process_finder=extension_processes):
    sealed = revalidate(sealed_path, runner)
    runtime = observe_runtime(sealed, runner, process_reader, process_finder)
    return {"schemaVersion": 1, "verificationState": "live", "runtimeVerified": True,
        "verified_pair_fingerprint": sealed["verified_pair_fingerprint"],
        "executable_config_fingerprint": sealed["executable_config_fingerprint"],
        "runtime_fingerprint": fingerprint(runtime), "runtime": runtime,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "sealedManifest": str(Path(sealed_path).resolve(strict=True)),
        "producerSHA256": file_hash(__file__), "productionInstallAllowed": False,
        "mainLaunchAllowed": False}


def revalidate_live(manifest_path, runner=run, process_reader=process_identity,
                    process_finder=extension_processes):
    previous = json.loads(Path(manifest_path).read_text())
    require(previous.get("verificationState") == "live" and previous.get("runtimeVerified") is True,
        "live verification receipt missing")
    actual = attest_live(previous["sealedManifest"], runner, process_reader, process_finder)
    for key in ["schemaVersion", "verificationState", "runtimeVerified", "verified_pair_fingerprint",
        "executable_config_fingerprint", "runtime_fingerprint", "producerSHA256",
        "productionInstallAllowed", "mainLaunchAllowed"]:
        require(actual[key] == previous.get(key), "live pair identity changed: " + key)
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-config", type=Path)
    parser.add_argument("--host-receipt", type=Path)
    parser.add_argument("--source-receipt", type=Path)
    parser.add_argument("--expected-team-id", default="YZYJJPX484")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--live", type=Path, help="Existing sealed manifest to verify against actual runtime")
    parser.add_argument("--validate-live", type=Path)
    args = parser.parse_args()
    modes = [args.validate, args.live, args.validate_live]
    require(sum(bool(mode) for mode in modes) <= 1, "choose one verification mode")
    if args.validate or args.validate_live:
        require(not any([args.pair_config, args.host_receipt, args.source_receipt, args.output]),
            "validation cannot replace build inputs")
        result = revalidate_live(args.validate_live) if args.validate_live else revalidate(args.validate)
    elif args.live:
        require(args.output and not any([args.pair_config, args.host_receipt, args.source_receipt]),
            "live verification requires a sealed manifest and new output")
        result = attest_live(args.live)
    else:
        require(all([args.pair_config, args.host_receipt, args.source_receipt, args.output]),
            "all canonical build receipts and a new output are required")
        result = attest(args.pair_config, args.host_receipt, args.source_receipt, args.expected_team_id)
    if args.output:
        require(not args.output.exists(), "immutable attestation output already exists")
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as destination:
            destination.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("schemaVersion", "verificationState",
        "verified_pair_fingerprint", "executable_config_fingerprint", "runtimeVerified")}))


if __name__ == "__main__":
    main()
