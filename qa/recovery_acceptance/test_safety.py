"""Negative recovery gates; no business environment/DB/process/network used."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from qa.recovery_acceptance.safety import (
    assert_same_snapshot, copy_verified, file_manifest, require_restore_target, safe_relative, validate_manifest,
)


class RecoverySafetyTests(unittest.TestCase):
    def test_production_or_same_database_cannot_be_restored(self):
        own = "portal_pg_" + "a" * 32
        for source, target in ((own, own), ("portal_phase1", own), (own, "portal_phase1_restore_final"),
                               (own, own + ";DROP DATABASE portal_phase1")):
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                require_restore_target(source, target)
        require_restore_target(own, "portal_pg_" + "b" * 32)

    def test_archive_paths_reject_traversal_unknown_volume_and_windows_paths(self):
        for path in ("hr/../secret", "hr//item", "hr/./item", "/hr/item", "C:/hr/item",
                     "hr\\item", "runtime/secret", "hr", "hr/item\x00", "hr/:stream"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_relative(path)

    def test_invalid_manifest_metadata_is_rejected(self):
        for entry in ({"sha256": "abc", "size": 1}, {"sha256": "a" * 64, "size": True},
                      {"sha256": "a" * 64, "size": -1}, {"sha256": "a" * 64, "size": 1, "mode": 777}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                validate_manifest({"hr/item": entry})

    def make_private(self, root):
        for name in ("hr", "product", "tender"):
            (root / name).mkdir(parents=True)
        (root / "hr/item").write_bytes(b"synthetic private bytes")

    def test_tampered_bytes_fail_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            self.make_private(root)
            manifest = file_manifest(root)
            (root / "hr/item").write_bytes(b"corrupted")
            dest = Path(directory) / "restored"
            with self.assertRaises(RuntimeError):
                copy_verified(root, dest, manifest)
            self.assertFalse(dest.exists())

    def test_existing_destination_is_never_merged_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            self.make_private(root)
            dest = Path(directory) / "existing"
            dest.mkdir()
            sentinel = dest / "keep"
            sentinel.write_bytes(b"keep")
            with self.assertRaises(ValueError):
                copy_verified(root, dest, file_manifest(root))
            self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_extra_missing_or_modified_file_rejects_incomplete_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            self.make_private(root)
            manifest = file_manifest(root)
            (root / "hr/unexpected").write_bytes(b"extra")
            with self.assertRaises(RuntimeError):
                copy_verified(root, Path(directory) / "restored", manifest)
            (root / "hr/unexpected").unlink()
            (root / "hr/item").unlink()
            with self.assertRaises(RuntimeError):
                copy_verified(root, Path(directory) / "restored", manifest)

    def test_links_are_rejected_without_requiring_windows_symlink_privilege(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            self.make_private(root)
            original = Path.is_symlink
            with patch.object(Path, "is_symlink", lambda path: path.name == "item" or original(path)):
                with self.assertRaises(ValueError):
                    file_manifest(root)

    def test_source_or_sequence_changes_are_fail_closed(self):
        with self.assertRaises(RuntimeError):
            assert_same_snapshot({"seq": {"last_value": 2}}, {"seq": {"last_value": 3}}, "database")
        with self.assertRaises(RuntimeError):
            assert_same_snapshot({"sha256": "a"}, {"sha256": "b"}, "source")

    def test_valid_copy_preserves_manifest_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            self.make_private(root)
            manifest = file_manifest(root)
            dest = Path(directory) / "restored"
            copy_verified(root, dest, manifest)
            self.assertEqual(file_manifest(dest), manifest)
            self.assertEqual(file_manifest(root), manifest)


if __name__ == "__main__":
    unittest.main()
