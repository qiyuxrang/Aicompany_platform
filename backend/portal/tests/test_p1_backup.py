import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from validation.p1_migration_backup import create_snapshot, verify_snapshot


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
