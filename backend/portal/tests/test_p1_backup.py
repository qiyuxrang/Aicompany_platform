import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from validation.p1_migration_backup import create_snapshot, verify_snapshot
from validation.p1_isolated_acceptance import copy_private_files, file_state
from validation.private_path_safety import checked_path


class MigrationBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("baseline", encoding="utf-8")
        self.private = self.root / "private"
        self.private.mkdir()
        (self.private / "sample.docx").write_bytes(b"draft")
        self.dump = self.root / "database.dump"
        self.dump.write_bytes(b"PGDMP sample")
        self.destination = self.root / "snapshot"

    def snapshot(self):
        return create_snapshot(self.destination, [self.repo], self.private, self.dump)

    def test_existing_destination_does_not_change_marker(self):
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_bytes(b"untouched")
        with self.assertRaises(FileExistsError):
            self.snapshot()
        self.assertEqual(marker.read_bytes(), b"untouched")

    def test_missing_private_file_rejects_snapshot_verification(self):
        self.snapshot()
        (self.destination / "private" / "sample.docx").unlink()
        with self.assertRaises(ValueError):
            verify_snapshot(self.destination)

    def test_tampered_file_rejects_snapshot_verification(self):
        self.snapshot()
        (self.destination / "private" / "sample.docx").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            verify_snapshot(self.destination)

    def test_manifest_excludes_sensitive_configuration(self):
        (self.repo / ".runtime").mkdir()
        (self.repo / ".runtime" / "p1-validation.env").write_text("PORTAL_SECRET_KEY=sentinel-secret", encoding="utf-8")
        self.snapshot()
        manifest = (self.destination / "backup_manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("sentinel-secret", manifest)
        self.assertNotIn("p1-validation.env", manifest)
        self.assertTrue(json.loads(manifest)["files"])

    def test_valid_snapshot_can_be_verified_and_private_files_restored_without_overwrite(self):
        manifest = self.snapshot()
        self.assertEqual(verify_snapshot(self.destination), manifest)
        restored = self.root / "restored"
        before = copy_private_files(self.destination / "private", restored)
        self.assertEqual(before, file_state(restored))
        self.assertEqual((restored / "sample.docx").read_bytes(), b"draft")
        (restored / "keep").write_bytes(b"untouched")
        with self.assertRaises(FileExistsError):
            copy_private_files(self.destination / "private", restored)
        self.assertEqual((restored / "keep").read_bytes(), b"untouched")

    def test_rejects_symlink_in_private_storage(self):
        try:
            (self.private / "escape.docx").symlink_to(self.dump)
        except OSError:
            self.skipTest("当前 Windows 账户无符号链接权限")
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_rejects_source_overlap(self):
        with self.assertRaises(ValueError):
            create_snapshot(self.private / "nested", [self.repo], self.private, self.dump)

    def test_rejects_unignored_backup_inside_repository(self):
        with self.assertRaises(ValueError):
            create_snapshot(self.repo / "unignored-backup", [self.repo], self.private, self.dump)


@unittest.skipUnless(os.name == "nt", "Real junction negative cases require Windows")
class WindowsJunctionBackupTests(unittest.TestCase):
    """All junctions and their synthetic targets belong to one retained runtime UUID."""
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(__file__).resolve().parents[3]
        runtime = checked_path(cls.workspace / ".runtime", must_exist=True)
        if not runtime.resolve().is_relative_to(cls.workspace.resolve()):
            raise ValueError("Owned test runtime escaped the workspace")
        cls.identifier = uuid.uuid4().hex
        cls.owned = checked_path(runtime / "legacy-backup-path-tests" / cls.identifier)
        if not cls.owned.is_relative_to(runtime) or cls.owned.exists():
            raise ValueError("Fresh owned runtime UUID required")
        cls.owned.mkdir(parents=True, exist_ok=False)
        cls.marker = {"uuid": cls.identifier, "root": str(cls.owned)}
        (cls.owned / "owner.json").write_text(json.dumps(cls.marker), encoding="utf-8")
        cls.outside = cls.owned / "outside-synthetic-target"
        cls.outside.mkdir()
        cls.sentinel = b"outside synthetic marker must remain unchanged"
        (cls.outside / "keep.txt").write_bytes(cls.sentinel)
        cls.checks, cls.cleanup_checks = [], []

    @classmethod
    def assert_owner(cls):
        root = checked_path(cls.owned, must_exist=True)
        runtime = checked_path(cls.workspace / ".runtime", must_exist=True)
        if (root.parent != runtime / "legacy-backup-path-tests" or root.name != cls.identifier
                or json.loads((root / "owner.json").read_text(encoding="utf-8")) != cls.marker
                or checked_path(cls.outside, root=root, must_exist=True) != root / "outside-synthetic-target"):
            raise ValueError("Test ownership mismatch; refusing any cleanup")
        if (cls.outside / "keep.txt").read_bytes() != cls.sentinel:
            raise ValueError("Outside synthetic target changed")

    def setUp(self):
        self.assert_owner()
        self.case = self.owned / self._testMethodName
        self.case.mkdir(exist_ok=False)
        self.repo, self.private = self.case / "repo", self.case / "private"
        self.repo.mkdir()
        (self.repo / "README.md").write_bytes(b"synthetic repo")
        self.private.mkdir()
        (self.private / "sample.docx").write_bytes(b"draft")
        self.dump = self.case / "database.dump"
        self.dump.write_bytes(b"PGDMP synthetic")
        self.destination = self.case / "snapshot"

    def junction(self, link):
        self.assert_owner()
        link = checked_path(link, root=self.owned)
        if link.exists() or not link.parent.is_dir():
            raise ValueError("Fresh root-local junction path required")
        executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not executable:
            self.fail("PowerShell is required for the real Windows junction check")
        environment = {key: value for key, value in os.environ.items() if key.upper() in
                       {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE"}}
        environment.update(P1_TEST_JUNCTION_LINK=str(link), P1_TEST_JUNCTION_TARGET=str(self.outside))
        command = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:P1_TEST_JUNCTION_LINK -Target $env:P1_TEST_JUNCTION_TARGET | Out-Null"
        self.addCleanup(self.remove_junction, link)
        result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", command],
            env=environment, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, "Owned PowerShell junction creation failed")
        self.assertTrue(link.is_junction())
        self.assertEqual(link.resolve(strict=True), self.outside.resolve(strict=True))
        return link

    @classmethod
    def remove_junction(cls, link):
        cls.assert_owner()
        if not link.exists() and not link.is_junction():
            # Startup failure may occur before New-Item creates anything.
            cls.cleanup_checks.append({"link": str(link.relative_to(cls.owned)),
                "link_removed": True, "outside_target_untouched": True, "creation_not_observed": True})
            return
        # Check lexical ownership and exact target, then unlink the junction only.
        # No recursive removal and no traversal/deletion of its outside target.
        if (not link.absolute().is_relative_to(cls.owned) or not link.is_junction()
                or link.resolve(strict=True) != cls.outside.resolve(strict=True)):
            raise ValueError("Unproven junction ownership; refusing cleanup")
        link.rmdir()
        cls.assert_owner()
        if link.exists() or link.is_junction():
            raise ValueError("Owned junction cleanup was not verified")
        cls.cleanup_checks.append({"link": str(link.relative_to(cls.owned)), "link_removed": True,
                                   "outside_target_untouched": True})

    def snapshot_rejected_before_copy(self, private):
        with patch("validation.p1_migration_backup.shutil.copy2") as copy:
            with self.assertRaises(ValueError):
                create_snapshot(self.destination, [self.repo], private, self.dump)
            copy.assert_not_called()
        self.assertFalse(self.destination.exists())
        self.assert_owner()
        self.checks.append(self._testMethodName)

    def test_private_root_junction_is_rejected_before_snapshot_creation(self):
        self.snapshot_rejected_before_copy(self.junction(self.case / "linked-private"))

    def test_nested_private_junction_is_rejected_before_any_copy(self):
        self.junction(self.private / "escape")
        self.snapshot_rejected_before_copy(self.private)

    def test_existing_snapshot_junction_is_rejected_before_external_hash_read(self):
        create_snapshot(self.destination, [self.repo], self.private, self.dump)
        self.junction(self.destination / "private/escape")
        (self.destination / "backup_manifest.json").write_text(json.dumps({"repos": [], "files": [
            {"path": "private/escape/keep.txt", "sha256": "0" * 64}]}), encoding="utf-8")
        with patch("validation.p1_migration_backup._sha") as checksum:
            with self.assertRaises(ValueError):
                verify_snapshot(self.destination)
            checksum.assert_not_called()
        self.assert_owner()
        self.checks.append(self._testMethodName)

    def test_legacy_restore_rejects_nested_source_junction_before_copytree(self):
        self.junction(self.private / "escape")
        target = self.case / "restored"
        with patch("validation.p1_isolated_acceptance.shutil.copytree") as copy:
            with self.assertRaises(ValueError):
                copy_private_files(self.private, target)
            copy.assert_not_called()
        self.assertFalse(target.exists())
        self.assert_owner()
        self.checks.append(self._testMethodName)

    def test_legacy_restore_rejects_destination_ancestor_junction_before_copytree(self):
        link = self.junction(self.case / "restore-parent")
        target = link / "must-not-exist"
        with patch("validation.p1_isolated_acceptance.shutil.copytree") as copy:
            with self.assertRaises(ValueError):
                copy_private_files(self.private, target)
            copy.assert_not_called()
        self.assertFalse((self.outside / "must-not-exist").exists())
        self.assert_owner()
        self.checks.append(self._testMethodName)

    @classmethod
    def tearDownClass(cls):
        cls.assert_owner()
        complete = len(cls.checks) == 5 and len(cls.cleanup_checks) == 5
        (cls.owned / "evidence.json").write_text(json.dumps({
            "uuid": cls.identifier, "outcome": "PASS" if complete else "FAIL",
            "actual_windows_junction_checks": cls.checks, "cleanup": cls.cleanup_checks,
            "outside_marker_unchanged": True, "synthetic_files_retained": True,
            "linux_symlink_execution_claimed": False}, indent=2), encoding="utf-8")
        print("Owned junction evidence:", cls.owned / "evidence.json")
