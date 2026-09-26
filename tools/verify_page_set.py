#!/usr/bin/env python3
"""Read-only verifier for a local synthetic page set manifest.

Input contract:
- The manifest must be UTF-8 JSON.
- The top-level object must contain only the key ``pages``.
- ``pages`` must be an ordered list of objects containing only:
  ``page_id``, ``relative_path``, and ``sha256``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any


def _make_page_record(page_id: Any, relative_path: Any) -> dict[str, Any]:
    return {
        "page_id": page_id if isinstance(page_id, str) else None,
        "relative_path": relative_path if isinstance(relative_path, str) else None,
        "errors": [],
    }


def _is_absolute_relative_path(value: str) -> bool:
    return (
        PurePath(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _normalize_relative_path(value: str) -> str:
    return PurePosixPath(value).as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_candidate_path(root_path: Path, candidate_path: Path) -> dict[str, Any]:
    try:
        relative_parts = candidate_path.relative_to(root_path).parts
    except ValueError:
        return {"escapes_root": True, "broken_symlink": False}

    current_path = root_path
    for part in relative_parts:
        current_path = current_path / part
        try:
            resolved_current = current_path.resolve(strict=False)
        except (OSError, RuntimeError):
            return {"escapes_root": False, "broken_symlink": False, "resolution_error": True}
        if not resolved_current.is_relative_to(root_path):
            return {"escapes_root": True, "broken_symlink": False}
        if current_path.is_symlink():
            link_target = Path(os.readlink(current_path))
            if not link_target.is_absolute():
                link_target = current_path.parent / link_target
            try:
                resolved_target = link_target.resolve(strict=False)
            except (OSError, RuntimeError):
                return {"escapes_root": False, "broken_symlink": False, "resolution_error": True}
            if not resolved_target.is_relative_to(root_path):
                return {"escapes_root": True, "broken_symlink": False}
            if not link_target.exists():
                return {"escapes_root": False, "broken_symlink": True}
            current_path = resolved_target
            continue

        if not current_path.exists():
            return {"escapes_root": False, "broken_symlink": False}

    return {"escapes_root": False, "broken_symlink": False}


def verify_page_set(manifest_path: Path, root_path: Path, expected_count: int) -> dict[str, Any]:
    report: dict[str, Any] = {
        "structure": {
            "verified": False,
            "expected_count": expected_count,
            "manifest_count": None,
            "errors": [],
            "pages": [],
        },
        "bytes": {
            "verified": False,
            "errors": [],
            "pages": [],
        },
        "authority": "NOT_EVALUATED",
        "release": "NOT_EVALUATED",
    }

    try:
        root_resolved = root_path.resolve(strict=True)
    except FileNotFoundError as exc:
        report["structure"]["errors"].append(f"root path does not exist: {exc}")
        report["bytes"]["errors"].append("byte verification not performed because root path does not exist")
        return report
    except (OSError, RuntimeError) as exc:
        report["structure"]["errors"].append(f"root path is not readable: {exc}")
        report["bytes"]["errors"].append("byte verification not performed because root directory is unavailable")
        return report

    if not root_resolved.is_dir():
        report["structure"]["errors"].append("root path must reference a directory")
        report["bytes"]["errors"].append("byte verification not performed because root path is not a directory")
        return report

    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report["structure"]["errors"].append(f"manifest is not readable UTF-8 JSON: {exc}")
        report["bytes"]["errors"].append("byte verification not performed because manifest could not be read")
        return report

    if not isinstance(manifest, dict):
        report["structure"]["errors"].append("manifest top-level value must be an object")
        report["bytes"]["errors"].append("byte verification not performed because manifest structure is invalid")
        return report

    manifest_keys = set(manifest.keys())
    if manifest_keys != {"pages"}:
        report["structure"]["errors"].append("manifest top-level object must contain only the key 'pages'")

    pages = manifest.get("pages")
    if not isinstance(pages, list):
        report["structure"]["errors"].append("'pages' must be a list")
        report["bytes"]["errors"].append("byte verification not performed because 'pages' is invalid")
        return report

    report["structure"]["manifest_count"] = len(pages)

    if len(pages) != expected_count:
        report["structure"]["errors"].append(
            f"expected_count mismatch: expected {expected_count}, found {len(pages)}"
        )

    seen_ids: dict[str, int] = {}
    seen_paths: dict[str, int] = {}

    for index, page in enumerate(pages, start=1):
        page_record = _make_page_record(
            page.get("page_id") if isinstance(page, dict) else None,
            page.get("relative_path") if isinstance(page, dict) else None,
        )
        page_record["index"] = index
        report["structure"]["pages"].append(page_record)
        report["bytes"]["pages"].append(
            {
                "page_id": page_record["page_id"],
                "relative_path": page_record["relative_path"],
                "verified": False,
                "errors": [],
            }
        )

        if not isinstance(page, dict):
            page_record["errors"].append("page entry must be an object")
            report["bytes"]["pages"][-1]["errors"].append(
                "byte verification not performed because page structure is invalid"
            )
            continue

        page_keys = set(page.keys())
        if page_keys != {"page_id", "relative_path", "sha256"}:
            page_record["errors"].append(
                "page entry must contain only 'page_id', 'relative_path', and 'sha256'"
            )

        page_id = page.get("page_id")
        relative_path = page.get("relative_path")
        sha256_value = page.get("sha256")

        if not isinstance(page_id, str):
            page_record["errors"].append("page_id must be a string")
        if not isinstance(relative_path, str):
            page_record["errors"].append("relative_path must be a string")
        if not isinstance(sha256_value, str):
            page_record["errors"].append("sha256 must be a string")

        if isinstance(page_id, str):
            expected_page_id = f"P{index:03d}"
            if not (len(page_id) == 4 and page_id.startswith("P") and page_id[1:].isdigit()):
                page_record["errors"].append("page_id must match P001 format")
            if page_id != expected_page_id:
                page_record["errors"].append(
                    f"page_id order error: expected {expected_page_id} at position {index}, found {page_id}"
                )
            if page_id in seen_ids:
                page_record["errors"].append(
                    f"duplicate page_id: {page_id} already used at position {seen_ids[page_id]}"
                )
            else:
                seen_ids[page_id] = index

        normalized_path: str | None = None
        candidate_path: Path | None = None
        if isinstance(relative_path, str):
            normalized_path = _normalize_relative_path(relative_path)
            path_parts = PurePosixPath(relative_path).parts

            if _is_absolute_relative_path(relative_path):
                page_record["errors"].append("relative_path must not be absolute")
            if ".." in path_parts:
                page_record["errors"].append("relative_path must not contain '..'")
            if normalized_path in seen_paths:
                page_record["errors"].append(
                    f"duplicate relative_path: {normalized_path} already used at position {seen_paths[normalized_path]}"
                )
            else:
                seen_paths[normalized_path] = index

            candidate_path = root_resolved / PurePosixPath(relative_path)
            path_inspection = _inspect_candidate_path(root_resolved, candidate_path)
            if path_inspection["escapes_root"]:
                page_record["errors"].append("relative_path resolves outside the root directory")
            if path_inspection.get("resolution_error"):
                page_record["errors"].append("relative_path cannot be resolved safely")

        if isinstance(sha256_value, str):
            if len(sha256_value) != 64 or any(character not in "0123456789abcdef" for character in sha256_value):
                page_record["errors"].append("sha256 must be 64 lowercase hexadecimal characters")

        byte_page = report["bytes"]["pages"][-1]
        byte_page["page_id"] = page_id if isinstance(page_id, str) else None
        byte_page["relative_path"] = relative_path if isinstance(relative_path, str) else None

        if page_record["errors"]:
            byte_page["errors"].append("byte verification not performed because page structure is invalid")
            continue

        assert candidate_path is not None
        assert isinstance(sha256_value, str)

        path_inspection = _inspect_candidate_path(root_resolved, candidate_path)
        if path_inspection["escapes_root"]:
            byte_page["errors"].append("resolved file is outside the root directory")
            continue
        if path_inspection.get("resolution_error"):
            byte_page["errors"].append("file path cannot be resolved safely")
            continue
        if path_inspection["broken_symlink"]:
            byte_page["errors"].append("symlink target is missing")
            continue

        try:
            resolved_path = candidate_path.resolve(strict=True)
        except FileNotFoundError:
            byte_page["errors"].append("file is missing")
            continue
        except RuntimeError:
            byte_page["errors"].append("file path cannot be resolved safely")
            continue
        except OSError as exc:
            byte_page["errors"].append(f"file is not readable: {exc}")
            continue

        if not resolved_path.is_relative_to(root_resolved):
            byte_page["errors"].append("resolved file is outside the root directory")
            continue

        if not resolved_path.is_file():
            byte_page["errors"].append("path does not reference a regular file")
            continue

        try:
            actual_sha256 = _sha256_file(resolved_path)
        except OSError as exc:
            byte_page["errors"].append(f"file is not readable: {exc}")
            continue

        if actual_sha256 != sha256_value:
            byte_page["errors"].append("sha256 mismatch")
            continue

        byte_page["verified"] = True

    structure_pages = report["structure"]["pages"]
    byte_pages = report["bytes"]["pages"]
    report["structure"]["verified"] = not report["structure"]["errors"] and all(
        not page["errors"] for page in structure_pages
    )
    report["bytes"]["verified"] = not report["bytes"]["errors"] and all(page["verified"] for page in byte_pages)

    for page in structure_pages:
        page.pop("index", None)

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a local page-set manifest against local file bytes.")
    parser.add_argument("--manifest", required=True, help="Path to the UTF-8 JSON manifest file.")
    parser.add_argument("--root", required=True, help="Root directory containing the referenced files.")
    parser.add_argument(
        "--expected-count",
        required=True,
        type=int,
        help="Expected number of manifest page entries.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_page_set(
        manifest_path=Path(args.manifest),
        root_path=Path(args.root),
        expected_count=args.expected_count,
    )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["structure"]["verified"] and report["bytes"]["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
