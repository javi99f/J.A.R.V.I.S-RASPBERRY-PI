from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dist-release"
PACKAGE_NAME = "jarvis-pi-arm64.zip"
MANIFEST_NAME = "jarvis-pi-manifest.json"
RELEASE_NOTES_NAME = "jarvis-release-notes.md"
ROOT_FILES = {
    ".env.example",
    "VERSION",
    "CHANGELOG.md",
    "README.md",
    "RASPBERRY_PI_4_TEST.md",
    "UPDATES_GITHUB.md",
    "requirements.txt",
    "install_pi4.sh",
    "launch_assistant.sh",
    "start_jarvis_pi.sh",
    "configure_pi_display.sh",
    "assistantctl",
    "audio_check.py",
    "start_assistant.py",
    "diagnose_pi_live.sh",
    "tools/diagnose_pi_live.py",
}
TREE_ROOTS = {
    "omar_ai_core",
    "assets",
}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".pid"}


def _version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version or version.startswith("v"):
        raise SystemExit("VERSION debe contener solo X.Y.Z, sin la letra v.")
    return version


def release_notes_for_version(notes: str, version: str) -> str:
    heading = f"## Cambios para la version {version}"
    start = notes.find(heading)
    if start < 0:
        raise SystemExit(f"Falta la sección exacta de notas: {heading}")
    next_heading = notes.find("\n## ", start + len(heading))
    selected = notes[start: next_heading if next_heading >= 0 else None].strip()
    if not any(line.startswith("- ") for line in selected.splitlines()):
        raise SystemExit(f"La sección {heading} no contiene cambios en formato de lista.")
    return selected + "\n"


def _release_files() -> list[Path]:
    files: list[Path] = []
    for relative in sorted(ROOT_FILES):
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit(f"Falta el archivo obligatorio: {relative}")
        files.append(path)
    for tree_name in sorted(TREE_ROOTS):
        tree = ROOT / tree_name
        if not tree.is_dir():
            raise SystemExit(f"Falta la carpeta obligatoria: {tree_name}")
        for path in tree.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.suffix.lower() in EXCLUDED_SUFFIXES:
                continue
            files.append(path)
    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix())


def _zip_info(relative: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def build(output: Path, notes: str) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    package_path = output / PACKAGE_NAME
    manifest_path = output / MANIFEST_NAME
    release_notes = release_notes_for_version(notes, _version())
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in _release_files():
            relative = path.relative_to(ROOT).as_posix()
            executable = relative.endswith(".sh") or relative == "assistantctl"
            bundle.writestr(_zip_info(relative, 0o755 if executable else 0o644), path.read_bytes())

    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "version": _version(),
        "platform": "linux-aarch64",
        "package_asset": PACKAGE_NAME,
        "sha256": digest,
        "size": package_path.stat().st_size,
        "requires_restart": True,
        "notes": release_notes.strip(),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / RELEASE_NOTES_NAME).write_text(release_notes, encoding="utf-8")
    return package_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye una actualización ARM64 de Jarvis")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--notes", default="")
    parser.add_argument("--notes-file", type=Path)
    args = parser.parse_args()
    notes = args.notes
    if args.notes_file:
        notes = args.notes_file.read_text(encoding="utf-8")
    package, manifest = build(args.output.resolve(), notes)
    print(package)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
