import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "verify_page_set.py"


class VerifyPageSetCliTests(unittest.TestCase):
    def run_cli(self, manifest_path: Path, root_path: Path, expected_count: int) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT_PATH),
                "--manifest",
                str(manifest_path),
                "--root",
                str(root_path),
                "--expected-count",
                str(expected_count),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def write_manifest(self, path: Path, pages: list[dict[str, str]]) -> None:
        path.write_text(json.dumps({"pages": pages}, indent=2), encoding="utf-8")

    def page(self, page_id: str, relative_path: str, content: bytes) -> dict[str, str]:
        return {
            "page_id": page_id,
            "relative_path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def test_valid_three_page_set_verifies_structure_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            page_specs = [
                ("P001", "pages/p001.bin", b"synthetic-page-1"),
                ("P002", "pages/p002.bin", b"synthetic-page-2"),
                ("P003", "pages/p003.bin", b"synthetic-page-3"),
            ]

            pages = []
            for page_id, relative_path, content in page_specs:
                file_path = root / relative_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)
                pages.append(self.page(page_id, relative_path, content))

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(manifest_path, pages)

            result = self.run_cli(manifest_path, root, expected_count=3)

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output["structure"]["verified"])
            self.assertTrue(output["bytes"]["verified"])
            self.assertEqual(output["authority"], "NOT_EVALUATED")
            self.assertEqual(output["release"], "NOT_EVALUATED")
            self.assertTrue(all(page["verified"] for page in output["bytes"]["pages"]))

    def test_changed_bytes_fail_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            file_path = root / "pages" / "p001.bin"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            original = b"expected-bytes"
            file_path.write_bytes(original)

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(manifest_path, [self.page("P001", "pages/p001.bin", original)])

            file_path.write_bytes(b"changed-bytes")
            result = self.run_cli(manifest_path, root, expected_count=1)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertTrue(output["structure"]["verified"])
            self.assertFalse(output["bytes"]["verified"])
            self.assertIn("sha256 mismatch", output["bytes"]["pages"][0]["errors"])

    def test_missing_file_does_not_produce_verified_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            missing_bytes = b"manifest-metadata-only"
            manifest_path = temp_path / "manifest.json"
            self.write_manifest(
                manifest_path,
                [self.page("P001", "pages/p001.bin", missing_bytes)],
            )

            result = self.run_cli(manifest_path, root, expected_count=1)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertTrue(output["structure"]["verified"])
            self.assertFalse(output["bytes"]["verified"])
            self.assertFalse(output["bytes"]["pages"][0]["verified"])
            self.assertIn("file is missing", output["bytes"]["pages"][0]["errors"])

    def test_duplicate_gap_and_order_errors_fail_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            file_paths = [
                ("P001", "pages/p001.bin", b"one"),
                ("P003", "pages/p002.bin", b"two"),
                ("P003", "pages/p003.bin", b"three"),
            ]

            pages = []
            for page_id, relative_path, content in file_paths:
                file_path = root / relative_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)
                pages.append(self.page(page_id, relative_path, content))

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(manifest_path, pages)

            result = self.run_cli(manifest_path, root, expected_count=3)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertFalse(output["structure"]["verified"])
            page_two_errors = output["structure"]["pages"][1]["errors"]
            page_three_errors = output["structure"]["pages"][2]["errors"]
            self.assertTrue(any("expected P002" in error for error in page_two_errors))
            self.assertTrue(any("duplicate page_id" in error for error in page_three_errors))

    def test_malformed_hash_fails_structure_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            file_path = root / "pages" / "p001.bin"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b"synthetic")

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(
                manifest_path,
                [{"page_id": "P001", "relative_path": "pages/p001.bin", "sha256": "ABC"}],
            )

            result = self.run_cli(manifest_path, root, expected_count=1)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertFalse(output["structure"]["verified"])
            self.assertIn(
                "sha256 must be 64 lowercase hexadecimal characters",
                output["structure"]["pages"][0]["errors"],
            )
            self.assertIn(
                "byte verification not performed because page structure is invalid",
                output["bytes"]["pages"][0]["errors"],
            )

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            outside = temp_path / "outside.bin"
            outside.write_bytes(b"synthetic")

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(
                manifest_path,
                [self.page("P001", "../outside.bin", b"synthetic")],
            )

            result = self.run_cli(manifest_path, root, expected_count=1)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertFalse(output["structure"]["verified"])
            self.assertIn("relative_path must not contain '..'", output["structure"]["pages"][0]["errors"])
            self.assertIn(
                "relative_path resolves outside the root directory",
                output["structure"]["pages"][0]["errors"],
            )

    def test_expected_count_mismatch_fails_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            file_path = root / "pages" / "p001.bin"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            content = b"synthetic"
            file_path.write_bytes(content)

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(manifest_path, [self.page("P001", "pages/p001.bin", content)])

            result = self.run_cli(manifest_path, root, expected_count=2)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertFalse(output["structure"]["verified"])
            self.assertIn("expected_count mismatch: expected 2, found 1", output["structure"]["errors"])

    def test_root_must_be_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root_file = temp_path / "not-a-directory"
            root_file.write_text("synthetic", encoding="utf-8")

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(manifest_path, [])

            result = self.run_cli(manifest_path, root_file, expected_count=0)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertFalse(output["structure"]["verified"])
            self.assertFalse(output["bytes"]["verified"])
            self.assertIn("root path must reference a directory", output["structure"]["errors"])
            self.assertIn(
                "byte verification not performed because root path is not a directory",
                output["bytes"]["errors"],
            )

    def test_symlink_escaping_root_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlink creation is unavailable on this platform")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "root"
            root.mkdir()

            outside_dir = temp_path / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "secret.bin"
            content = b"synthetic"
            outside_file.write_bytes(content)

            link_dir = root / "linked"
            try:
                os.symlink(outside_dir, link_dir, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            manifest_path = temp_path / "manifest.json"
            self.write_manifest(
                manifest_path,
                [self.page("P001", "linked/secret.bin", content)],
            )

            result = self.run_cli(manifest_path, root, expected_count=1)

            self.assertNotEqual(result.returncode, 0)
            output = json.loads(result.stdout)
            self.assertFalse(output["structure"]["verified"])
            self.assertIn(
                "relative_path resolves outside the root directory",
                output["structure"]["pages"][0]["errors"],
            )


if __name__ == "__main__":
    unittest.main()
