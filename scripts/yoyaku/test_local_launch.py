#!/usr/bin/env python3
"""Behavioral checks for the fork's explicit local launch boundary."""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import shutil
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("artifact", HERE / "local-artifact.py")
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)
install_spec = importlib.util.spec_from_file_location("installer", HERE / "install-canary.py")
installer = importlib.util.module_from_spec(install_spec)
install_spec.loader.exec_module(installer)


class LocalLaunchTests(unittest.TestCase):
    def policy(self, tag="yoyaku-lordsquire", config="Release", prod="0", file="", profile="", account="", name="CMUX YOYAKU", bundle_override="0"):
        return subprocess.run(["bash", "-c",
            'source "$1"; CMUX_UITEST_STACK_PASSWORD=fixture; CMUX_UI_TEST_MODE=1; '
            'cmux_yoyaku_local_launch_policy "${@:2}" || exit $?; '
            'test -z "${CMUX_UITEST_STACK_PASSWORD:-}${CMUX_UI_TEST_MODE:-}"',
            "policy", str(HERE / "local-launch.sh"), tag, config, prod, file, profile, account, name, bundle_override],
            capture_output=True, text=True).returncode

    def test_tui_artifact_uses_identical_frozen_upstream_inputs(self):
        result = subprocess.run(["bash", "-c", 'source "$1"; cmux_yoyaku_tui_source "$2"',
                                 "source", str(HERE / "local-launch.sh"), str(HERE.parent.parent)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), (HERE / "upstream-base").read_text().strip())

    def test_changed_tui_inputs_cannot_reuse_the_upstream_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "-q")
            (root / "cmux-tui").mkdir()
            (root / "cmux-tui/client").write_text("original")
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            for name in ["cmux-tui-artifacts.yml", "cmux-tui-build-package.yml"]:
                (workflows / name).write_text("frozen " + name)
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture", "--no-verify")
            (root / "scripts/yoyaku").mkdir(parents=True)
            (root / "scripts/yoyaku/upstream-base").write_text(git("rev-parse", "HEAD"))
            archive = root / ".github/upstream-workflows"
            shutil.copytree(workflows, archive)
            def accepted():
                return subprocess.run(["bash", "-c", 'source "$1"; cmux_yoyaku_tui_source "$2"',
                                       "source", str(HERE / "local-launch.sh"), str(root)],
                                      capture_output=True).returncode == 0
            self.assertTrue(accepted())
            (root / "cmux-tui/client").write_text("changed")
            self.assertFalse(accepted())
            git("checkout", "--", "cmux-tui")
            (archive / "cmux-tui-artifacts.yml").write_text("changed workflow")
            self.assertFalse(accepted())

    def test_signature_requirement_is_inline_not_a_filename(self):
        with patch.object(installer, "run") as command:
            installer.verify_signature(pathlib.Path("/fixture/Canary.app"))
        requirement = command.call_args.args
        self.assertEqual(requirement[2], "-R")
        self.assertTrue(requirement[3].startswith("=anchor apple generic"))

    def test_failed_staging_sign_removes_the_hidden_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source.app"
            (source / "Contents").mkdir(parents=True)
            with (source / "Contents/Info.plist").open("wb") as stream:
                import plistlib
                plistlib.dump({"CFBundleIdentifier": installer.IDENTITY,
                               "CMUXYOYAKULaunchMode": "local",
                               "CMUXYOYAKUSourceDirty": False,
                               "CMUXYOYAKUSourceSHA": "a" * 40}, stream)
            destination = root / "Applications/CMUX YOYAKU.app"
            state = root / "state"
            def fake_run(*args):
                if args[0] == "/usr/bin/ditto":
                    shutil.copytree(args[1], args[2])
                return ""
            with (patch.object(installer, "HOME", root),
                  patch.object(installer, "DESTINATION", destination),
                  patch.object(installer, "STATE", state),
                  patch.object(installer, "run", side_effect=fake_run),
                  patch.object(installer, "sign", side_effect=RuntimeError("fixture signing failure")),
                  self.assertRaisesRegex(RuntimeError, "fixture signing failure")):
                installer.install(source, "a" * 40, False)
            self.assertEqual(list(destination.parent.glob(".cmux-yoyaku-*.app")), [])

    def test_first_install_rollback_retires_canary_and_clears_accepted_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            destination = root / "Applications/CMUX YOYAKU.app"
            destination.mkdir(parents=True)
            state = root / "state"
            state.mkdir()
            receipt = {"generation": "first", "bundle_id": installer.IDENTITY,
                       "backup": None, "previous": None}
            receipt_path = state / "first.json"
            receipt_path.write_text(json.dumps(receipt))
            (state / "accepted.json").write_text(json.dumps(receipt))
            with (patch.object(installer, "DESTINATION", destination),
                  patch.object(installer, "STATE", state),
                  patch.object(installer, "verify_signature"),
                  patch.object(installer, "stop_owned_canary")):
                installer.rollback(receipt_path)
            self.assertFalse(destination.exists())
            self.assertFalse((state / "accepted.json").exists())
            self.assertEqual(len(list(state.glob("rolled-back-*.app"))), 1)

    def test_signed_out_release_needs_no_credential_and_scrubs_fixtures(self):
        self.assertEqual(self.policy(), 0)

    def test_local_canary_socket_mode_is_restricted_to_automation(self):
        result = subprocess.run(["bash", "-c",
            'source "$1"; test "$(cmux_yoyaku_socket_control_mode 1)" = automation',
            "socket-mode", str(HERE / "local-launch.sh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        invalid = subprocess.run(["bash", "-c", 'source "$1"; cmux_yoyaku_socket_control_mode 0',
            "socket-mode", str(HERE / "local-launch.sh")], capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
        reload_script = (HERE.parent / "reload.sh").read_text()
        self.assertIn('set_plist_env "$INFO_PLIST" CMUX_SOCKET_MODE "$(cmux_yoyaku_socket_control_mode 1)"', reload_script)
        self.assertIn('set_plist_env "$INFO_PLIST" CMUX_SOCKET_MODE "allowAll"', reload_script)

    def test_other_tags_debug_and_every_auth_input_are_rejected(self):
        for args in [("other",), ("yoyaku-lordsquire", "Debug"),
                     ("yoyaku-lordsquire", "Release", "1"),
                     ("yoyaku-lordsquire", "Release", "0", "/fixture"),
                     ("yoyaku-lordsquire", "Release", "0", "", "personal"),
                     ("yoyaku-lordsquire", "Release", "0", "", "", "person@example.test")]:
            with self.subTest(args=args):
                self.assertNotEqual(self.policy(*args), 0)

    def test_artifact_keeps_identity_and_cannot_auto_update(self):
        result = artifact.configure({"CFBundleIdentifier": "com.cmuxterm.app.debug.yoyaku.lordsquire",
                                     "SUFeedURL": "https://upstream.example.test/feed"}, "a" * 40, False)
        self.assertFalse(result["SUEnableAutomaticChecks"])
        self.assertNotIn("SUFeedURL", result)
        self.assertFalse(result["CMUXYOYAKUSourceDirty"])
        self.assertEqual(result["CMUXYOYAKUSourceSHA"], "a" * 40)

    def test_stable_artifact_is_never_modified(self):
        info = {"CFBundleIdentifier": "com.cmuxterm.app"}
        with self.assertRaises(ValueError):
            artifact.configure(info, "a" * 40, False)
        self.assertEqual(info, {"CFBundleIdentifier": "com.cmuxterm.app"})

    def test_local_mode_cannot_collapse_tag_staging_or_override_identity(self):
        self.assertNotEqual(self.policy(name="cmux"), 0)
        self.assertNotEqual(self.policy(name="cmux DEV"), 0)
        self.assertNotEqual(self.policy(bundle_override="1"), 0)

    def test_installation_rejects_foreign_dirty_and_wrong_revision_artifacts(self):
        clean = artifact.configure({"CFBundleIdentifier": installer.IDENTITY}, "a" * 40, False)
        installer.validate_source(clean, "a" * 40)
        for patch, sha in [({"CFBundleIdentifier": "com.cmuxterm.app"}, "a" * 40),
                           ({"CMUXYOYAKUSourceDirty": True}, "a" * 40),
                           ({}, "b" * 40), ({}, "short")]:
            with self.subTest(patch=patch, sha=sha), self.assertRaises(ValueError):
                installer.validate_source(clean | patch, sha)


if __name__ == "__main__":
    unittest.main()
