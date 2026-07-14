"""Tests for side-effect-free reads of cached Stage 1 FEA packages."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

import cyclewash_fea_results as fea_results


EXACT_PACKAGE_HASH = "d871c798897415bf83a5c0f54d38848cec68e16c52f3eaa5b2246837ac4b7969"


def snapshot_tree(root: Path) -> dict[str, tuple[str, int, str]]:
    """Capture path types, file sizes, and content digests below root."""

    snapshot: dict[str, tuple[str, int, str]] = {}
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative_path] = ("directory", 0, "")
        elif path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            snapshot[relative_path] = ("file", path.stat().st_size, digest)
        else:
            snapshot[relative_path] = ("other", 0, "")
    return snapshot


class ReadOnlyStage1PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.source_package = project_root / "fea_results" / EXACT_PACKAGE_HASH

    def test_read_only_loader_preserves_valid_destination_and_backup(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / EXACT_PACKAGE_HASH
            backup = root / f".{EXACT_PACKAGE_HASH}.backup"
            shutil.copytree(self.source_package, destination)
            shutil.copytree(self.source_package, backup)
            before = snapshot_tree(root)

            self.assertTrue(hasattr(fea_results, "load_stage1_package_read_only"))
            package = fea_results.load_stage1_package_read_only(destination)

            self.assertEqual("cyclewash-fea-v1", package.schema_version)
            self.assertEqual(before, snapshot_tree(root))

    def test_read_only_loader_does_not_restore_backup_when_destination_is_missing(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = root / EXACT_PACKAGE_HASH
            backup = root / f".{EXACT_PACKAGE_HASH}.backup"
            shutil.copytree(self.source_package, backup)
            before = snapshot_tree(root)

            self.assertTrue(hasattr(fea_results, "load_stage1_package_read_only"))
            with self.assertRaises(ValueError):
                fea_results.load_stage1_package_read_only(destination)

            self.assertEqual(before, snapshot_tree(root))
            self.assertFalse(destination.exists())
            self.assertTrue(backup.is_dir())


if __name__ == "__main__":
    unittest.main()
