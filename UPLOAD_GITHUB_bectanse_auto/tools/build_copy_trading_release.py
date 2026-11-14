#!/usr/bin/env python3
"""Build a deterministic, allowlisted Windows Copy Trading release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_BASENAME = "bectanse-copy-trading-release"
OUTPUT_NAMES = {
    f"dist/{ARCHIVE_BASENAME}.zip",
    f"dist/{ARCHIVE_BASENAME}.zip.sha256",
}


def release_files(root: Path = ROOT) -> list[Path]:
    paths = [
        *root.joinpath("copy_trading").rglob("*.py"),
        root / "trading_journal" / "__init__.py",
        root / "trading_journal" / "schema.py",
        root / "trading_journal" / "providers" / "__init__.py",
        root / "trading_journal" / "providers" / "base.py",
        root / "trading_journal" / "providers" / "mt5.py",
        root / "requirements-copy-trading-worker.lock",
        root / "COPY_TRADING_RELEASE_VERSION",
        root / "tools" / "probe_mt5_terminal_demo.py",
        root / "tools" / "build_copy_trading_release.py",
        root / "docs" / "COPY_TRADING_V1_RUNBOOK.md",
        root / "docs" / "COPY_TRADING_WINDOWS_OPERATIONS.md",
        *root.joinpath("scripts", "windows").glob("copy-trading-*.ps1"),
        root / "scripts" / "windows" / "install-copy-trading-worker.ps1",
        root / "scripts" / "windows" / "install-mt5-terminals.ps1",
        root / "scripts" / "windows" / "test-terminal-interactive.ps1",
    ]
    resolved: dict[str, Path] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Release input missing or symlinked: {path}")
        relative = path.relative_to(root).as_posix()
        _safe_archive_path(relative)
        resolved[relative] = path
    return [resolved[name] for name in sorted(resolved)]


def _safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError(f"Hidden files are not allowed in release packages: {name}")
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(root: Path, paths: list[Path], source_commit: str) -> dict:
    version = (root / "COPY_TRADING_RELEASE_VERSION").read_text(encoding="utf-8").strip()
    if not version or any(char in version for char in "/\\\r\n"):
        raise ValueError("COPY_TRADING_RELEASE_VERSION must be a non-empty version label")
    files = []
    for path in paths:
        content = path.read_bytes()
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size": len(content),
            "sha256": _sha256(content),
        })
    return {
        "schema_version": 1,
        "release_version": version,
        "source_commit": source_commit,
        "runtime": {"os": "windows", "architecture": "x64", "python": "3.14.10"},
        "dependency_lock": "requirements-copy-trading-worker.lock",
        "files": files,
    }


def write_archive(root: Path, output: Path, source_commit: str) -> str:
    paths = release_files(root)
    manifest = build_manifest(root, paths, source_commit)
    package_name = f"{ARCHIVE_BASENAME}-{manifest['release_version']}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".tmp", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED) as archive:
            entries = [(path.relative_to(root).as_posix(), path.read_bytes()) for path in paths]
            entries.append(("COPY_TRADING_RELEASE_MANIFEST.json",
                            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")))
            for name, content in sorted(entries):
                _safe_archive_path(name)
                info = zipfile.ZipInfo(f"{package_name}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = (0o100644 & 0xFFFF) << 16
                info.extra = b""
                info.comment = b""
                archive.writestr(info, content)
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    archive_hash = _sha256(output.read_bytes())
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    with tempfile.NamedTemporaryFile(dir=checksum_path.parent, suffix=".tmp", mode="w",
                                     encoding="ascii", delete=False) as temp:
        temp.write(f"{archive_hash}  {output.name}\n")
        checksum_temp = Path(temp.name)
    try:
        os.replace(checksum_temp, checksum_path)
    finally:
        if checksum_temp.exists():
            checksum_temp.unlink()
    return archive_hash


def current_commit(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.PIPE
    ).strip()


def ensure_clean_source(root: Path) -> None:
    prefix = subprocess.check_output(
        ["git", "rev-parse", "--show-prefix"], cwd=root, text=True
    ).strip().replace("\\", "/")
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    unexpected = []
    for line in status.splitlines():
        name = line[3:].strip().strip('"').replace("\\", "/")
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
        if name not in OUTPUT_NAMES:
            unexpected.append(line)
    if unexpected:
        raise RuntimeError(
            "Build release archives from a committed clean source tree; pending paths: "
            + ", ".join(item[3:].strip() for item in unexpected)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"{ARCHIVE_BASENAME}.zip")
    parser.add_argument("--allow-dirty-source", action="store_true",
                        help="For local inspection only; do not distribute this archive.")
    args = parser.parse_args()
    if not args.allow_dirty_source:
        ensure_clean_source(ROOT)
    commit = current_commit(ROOT)
    digest = write_archive(ROOT, args.output.resolve(), commit)
    print(f"release={args.output.resolve()}")
    print(f"source_commit={commit}")
    print(f"sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
