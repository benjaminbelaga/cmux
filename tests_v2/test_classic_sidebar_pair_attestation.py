#!/usr/bin/env python3
"""Synthetic negative proofs for canonical pair attestation; no app launches."""
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pair_attestation",
    REPO / "scripts/attest-classic-sidebar-pair.py")
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


class PairAttestationTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="pair-attestation-fixture-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.host = self.root / "cmux DEV proof.app"
        self.cortex = self.root / "Cortex Dogfood proof.app"
        self.extension = self.cortex / "Contents/Extensions/Sidebar.appex"
        self.host_id = "com.cmuxterm.app.debug.proof"
        self.cortex_id = "fr.yoyaku.cortex.dogfood.proof"
        self.sha = "a" * 40
        self.tree = "b" * 40
        for app, identifier in [(self.host, self.host_id), (self.cortex, self.cortex_id),
                (self.extension, self.cortex_id + ".sessions")]:
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/_CodeSignature").mkdir()
            (app / "Contents/MacOS/fixture").write_bytes(b"synthetic executable")
            (app / "Contents/_CodeSignature/CodeResources").write_bytes(b"synthetic seal")
            self.write_info(app, {"CFBundleIdentifier": identifier, "CFBundleExecutable": "fixture"})
        resources = self.host / "Contents/Resources"
        (resources / "bin").mkdir(parents=True)
        (resources / "shell-integration").mkdir()
        self.cli = resources / "bin/cmux"
        self.cli.write_bytes(b"synthetic CLI")
        self.env = {"CMUX_BUNDLED_CLI_PATH": str(self.cli),
            "CMUX_SHELL_INTEGRATION_DIR": str(resources / "shell-integration"),
            "CMUX_SOCKET_PATH": "/tmp/cmux-debug-proof.sock", "CMUX_TAG": "proof",
            "CMUX_BUNDLE_ID": self.host_id}
        self.update_info(self.host, LSEnvironment=self.env)
        self.update_info(self.cortex, CortexDogfoodMainLaunchDisabled=True,
            CortexGitSHA=self.sha, CortexGitDirty="false", CortexPairedSDKRevision=self.sha)
        self.update_info(self.extension,
            EXAppExtensionAttributes={"EXExtensionPointIdentifier": self.host_id + ".cmux.sidebar"})
        self.pair = {"tag": "proof", "mainBundleID": self.cortex_id,
            "extensionBundleID": self.cortex_id + ".sessions",
            "extensionPoint": self.host_id + ".cmux.sidebar", "mainLaunchAllowed": False,
            "productionInstallAllowed": False, "hostFallbackAllowed": False,
            "cortexGitDirty": "false", "cortexGitSHA": self.sha, "sdkRevision": self.sha,
            "sdkSource": str(self.root), "appPath": str(self.cortex),
            "extensionPath": str(self.extension), "cmuxAPIMinimum": "2.3"}
        self.host_receipt = {"appPath": str(self.host), "bundleID": self.host_id,
            "hostGitSHA": self.sha, "cliPath": str(self.cli),
            "cliSHA256": producer.file_hash(self.cli), "buildAndStrictSignatureVerified": True}
        self.sources = {"productionActivationAllowed": False,
            "sourceArchivesIncludeForeignWIP": False, "cmuxArtifactSHA": self.sha,
            "CortexArtifactSHA": self.sha}
        self.pair_file = self.root / "pair.json"
        self.host_file = self.root / "host-build-receipt.json"
        self.source_file = self.root / "receipt.json"
        self.persist()
        self.signature_text = "Authority=Developer ID Application: Fixture (YZYJJPX484)\nTeamIdentifier=YZYJJPX484\nCDHash=" + "c" * 40
        self.manifest = {"id": self.cortex_id + ".sessions", "apiVersion": {"major": 2, "minor": 3},
            "readScopes": ["workspaceContext", "workspaceList"],
            "actionScopes": ["bindAgentSession", "editWorkspaceContext"],
            "sourcePath": "Sources/CortexSessionsExtension/CortexSessionsExtension.swift",
            "sourceSHA256": "1" * 64}
        manifest_patch = patch.object(producer, "requested_manifest", return_value=self.manifest)
        manifest_patch.start(); self.addCleanup(manifest_patch.stop)
        self.sdk_patch = patch.object(producer, "sdk_proof", return_value={
            "revision": self.sha, "source": str(self.root), "treeSHA": self.tree,
            "filesFingerprint": "d" * 64})
        self.sdk_patch.start()
        self.addCleanup(self.sdk_patch.stop)
        self.git_patch = patch.object(producer.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, self.tree + "\n", ""))
        self.git_mock = self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    @staticmethod
    def write_info(app, value):
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(value))

    def update_info(self, app, **values):
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        info.update(values)
        self.write_info(app, info)

    def persist(self):
        for path, value in [(self.pair_file, self.pair), (self.host_file, self.host_receipt),
                (self.source_file, self.sources)]:
            path.write_text(json.dumps(value))

    def runner(self, arguments):
        text = ("designated => identifier fixture and anchor apple generic\n"
            if "--requirements" in arguments else self.signature_text)
        return subprocess.CompletedProcess(arguments, 0, "", text)

    def attest(self):
        return producer.attest(self.pair_file, self.host_file, self.source_file, runner=self.runner)

    def test_actual_sealed_artifact_never_claims_live_readiness(self):
        manifest = self.attest()
        self.assertEqual(manifest["verificationState"], "sealed")
        self.assertFalse(manifest["runtimeVerified"])
        self.assertNotEqual(manifest["verified_pair_fingerprint"],
            manifest["executable_config_fingerprint"])

    def test_boolean_signature_receipt_cannot_authorize_ad_hoc_code(self):
        self.signature_text = "Signature=adhoc\nTeamIdentifier=not set\nCDHash=" + "c" * 40
        with self.assertRaisesRegex(producer.PairVerificationError, "ad-hoc"):
            self.attest()

    def test_other_developer_team_is_refused(self):
        self.signature_text = self.signature_text.replace("TeamIdentifier=YZYJJPX484", "TeamIdentifier=FOREIGN")
        with self.assertRaisesRegex(producer.PairVerificationError, "team mismatch"):
            self.attest()

    def test_deleted_reload_staging_path_is_refused(self):
        self.env["CMUX_BUNDLED_CLI_PATH"] = str(self.root / ".deleted-reload.app/Contents/Resources/bin/cmux")
        self.update_info(self.host, LSEnvironment=self.env)
        with self.assertRaisesRegex(producer.PairVerificationError, "stale or escape"):
            self.attest()

    def test_other_tag_socket_is_refused(self):
        self.env["CMUX_SOCKET_PATH"] = "/tmp/cmux.sock"
        self.update_info(self.host, LSEnvironment=self.env)
        with self.assertRaisesRegex(producer.PairVerificationError, "socket isolation"):
            self.attest()

    def test_source_heads_must_match_actual_pair_build_receipts(self):
        self.sources["cmuxArtifactSHA"] = "e" * 40
        self.persist()
        with self.assertRaisesRegex(producer.PairVerificationError, "source heads"):
            self.attest()

    def test_compiled_host_and_extension_sdk_trees_must_match(self):
        self.git_mock.return_value.stdout = "f" * 40 + "\n"
        with self.assertRaisesRegex(producer.PairVerificationError, "interface trees"):
            self.attest()

    def test_revalidation_refuses_binary_tampering(self):
        manifest = self.attest()
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        producer.revalidate(path, runner=self.runner)
        (self.extension / "Contents/MacOS/fixture").write_bytes(b"changed synthetic executable")
        with self.assertRaisesRegex(producer.PairVerificationError, "pair changed"):
            producer.revalidate(path, runner=self.runner)

    def live_fixture(self):
        self.sealed = self.attest()
        pair, execution = self.sealed["pair"], self.sealed["execution"]
        self.host_process = {"pid": 4242, "executablePath": pair["host"]["executablePath"], "startedAt": "fixture start"}
        self.extension_process = {"pid": 4343, "executablePath": pair["extension"]["executablePath"], "startedAt": "fixture start"}
        self.status = {"provider_id": "cmux.sidebar.extensions", "provider_active": True,
            "connected": True, "selected_bundle_id": self.manifest["id"], "hosts": [{
                "host_id": "host", "bundle_id": self.manifest["id"], "identity_id": "identity",
                "generation": 3, "connection_generation": 2, "grant_revision": 1,
                "manifest_id": self.manifest["id"], "api_version": self.manifest["apiVersion"],
                "effective_read_scopes": self.manifest["readScopes"],
                "effective_action_scopes": self.manifest["actionScopes"], "state": "connected", "pid": 4242}]}
        self.identify = {"app_bundle_path": pair["host"]["appPath"],
            "app_executable_path": pair["host"]["executablePath"],
            "app_cli_path": execution["cliPath"], "bundle_identifier": self.host_id,
            "socket_path": execution["socketPath"]}
        self.methods = ["system.identify", "system.capabilities", "extension.sidebar.status",
            "extension.sidebar.snapshot", "workspace.context.export", "workspace.context.import"]
        self.grants = {self.manifest["id"]: {"manifestID": self.manifest["id"],
            "apiVersion": self.manifest["apiVersion"], "readScopes": self.manifest["readScopes"],
            "actionScopes": self.manifest["actionScopes"]}}
        socket_patch = patch.object(producer, "require_socket")
        socket_patch.start(); self.addCleanup(socket_patch.stop)
        self.running_signature_override = None
        self.status_calls = 0
        self.status_after = None
        def runner(args):
            if "rpc" in args:
                method = args[-2]
                if method == "system.identify": value = self.identify
                elif method == "system.capabilities": value = {"socket_path": execution["socketPath"], "version": 2, "methods": self.methods}
                else:
                    self.status_calls += 1
                    value = self.status_after if self.status_calls > 1 and self.status_after else self.status
                return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
            if args[0] == "/usr/sbin/lsof": output = "p4242\nn" + execution["socketPath"] + "\n"
            elif args[0] == "/usr/bin/defaults":
                output = plistlib.dumps({"cmuxExtensionSidebar.grants.v1": json.dumps(self.grants).encode()}).decode()
            elif args[0] == "/usr/bin/codesign" and args[-1] in ["4242", "4343"] and self.running_signature_override and "--verbose=4" in args:
                output = self.running_signature_override
            else: return self.runner(args)
            return subprocess.CompletedProcess(args, 0, output, "")
        self.runtime_runner = runner

    def observe(self):
        return producer.observe_runtime(self.sealed, self.runtime_runner,
            lambda pid: self.host_process, lambda executable: [self.extension_process])

    def test_live_proof_uses_actual_effective_scopes_and_native_processes(self):
        self.live_fixture()
        observed = self.observe()
        self.assertEqual(observed["hostProcess"]["pid"], 4242)
        self.assertEqual(observed["apiVersion"], {"major": 2, "minor": 3})
        self.assertEqual(observed["socketProtocolVersion"], 2)
        self.assertEqual(observed["effectiveGrantSource"], "native-host-memory-at-connection-generation")

    def test_old_connected_ack_and_configured_grants_do_not_claim_effective_grants(self):
        self.live_fixture()
        del self.status["hosts"][0]["grant_revision"]
        with self.assertRaisesRegex(producer.PairVerificationError, "runtime_grants_not_observable"):
            self.observe()

    def test_revoked_in_memory_scope_is_refused_even_if_defaults_allow_it(self):
        self.live_fixture()
        self.status["hosts"][0]["effective_action_scopes"] = []
        with self.assertRaisesRegex(producer.PairVerificationError, "effective grant"):
            self.observe()

    def test_generation_change_during_observation_is_refused(self):
        self.live_fixture()
        self.status_after = json.loads(json.dumps(self.status))
        self.status_after["hosts"][0]["connection_generation"] = 3
        with self.assertRaisesRegex(producer.PairVerificationError, "changed during"):
            self.observe()

    def test_running_cdhash_must_match_sealed_executable(self):
        self.live_fixture()
        self.running_signature_override = self.signature_text.replace("c" * 40, "f" * 40)
        with self.assertRaisesRegex(producer.PairVerificationError, "running host signature"):
            self.observe()

    def test_socket_response_from_other_app_is_refused(self):
        self.live_fixture()
        self.identify["bundle_identifier"] = "com.cmuxterm.app"
        with self.assertRaisesRegex(producer.PairVerificationError, "live host identity"):
            self.observe()

    def test_process_at_wrong_executable_path_is_refused(self):
        self.live_fixture()
        self.host_process["executablePath"] = "/Applications/cmux.app/Contents/MacOS/cmux"
        with self.assertRaisesRegex(producer.PairVerificationError, "sealed executable"):
            self.observe()

    def test_forged_live_boolean_is_refused(self):
        manifest = self.attest()
        manifest["runtimeVerified"] = True
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(producer.PairVerificationError, "readiness claim"):
            producer.revalidate(path, runner=self.runner)


if __name__ == "__main__":
    unittest.main()
