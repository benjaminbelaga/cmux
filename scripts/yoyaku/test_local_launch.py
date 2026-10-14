#!/usr/bin/env python3
"""Behavioral checks for the fork's explicit local launch boundary."""
import importlib.util
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

    def test_signed_out_release_needs_no_credential_and_scrubs_fixtures(self):
        self.assertEqual(self.policy(), 0)

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
