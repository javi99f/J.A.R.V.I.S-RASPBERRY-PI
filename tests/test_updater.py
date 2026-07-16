import hashlib
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from omar_ai_core.updater import (
    ReleaseInfo,
    UpdateError,
    UpdateManager,
    _safe_relative_name,
    is_newer,
)


class _Response:
    def __init__(self, url, payload=None):
        self.url = url
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **_kwargs):
        return _Response(url, self.responses[url])


def _write_update_zip(path: Path, version: str = "0.4.0") -> None:
    files = {
        "VERSION": version,
        "requirements.txt": "requests>=2\n",
        "launch_assistant.sh": "#!/bin/sh\n",
        "start_jarvis_pi.sh": "#!/bin/sh\n",
        "omar_ai_core/__init__.py": "",
        "omar_ai_core/runtime.py": "UPDATED = True\n",
        "omar_ai_core/updater.py": "UPDATED = True\n",
        ".env": "GEMINI_API_KEY=must-not-overwrite\n",
        "config/liquid_visual.json": "{}\n",
    }
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)


class UpdaterTests(unittest.TestCase):
    def test_version_order_handles_stable_and_prerelease(self):
        self.assertTrue(is_newer("0.4.0", "0.3.9"))
        self.assertTrue(is_newer("0.4.0", "0.4.0-rc1"))
        self.assertFalse(is_newer("0.4.0-rc1", "0.4.0"))

    def test_archive_paths_reject_traversal_and_absolute_names(self):
        for name in ("../secret", "folder/../../secret", "/etc/passwd", "C:\\secret"):
            with self.subTest(name=name):
                with self.assertRaises(UpdateError):
                    _safe_relative_name(name)

    def test_check_reads_matching_github_release_manifest(self):
        api = "https://api.github.com/repos/example/Jarvis/releases/latest"
        manifest_url = "https://github.com/example/Jarvis/releases/download/v0.4.0/jarvis-pi-manifest.json"
        package_url = "https://github.com/example/Jarvis/releases/download/v0.4.0/jarvis-pi-arm64.zip"
        release_payload = {
            "tag_name": "v0.4.0",
            "prerelease": False,
            "assets": [
                {"name": "jarvis-pi-manifest.json", "browser_download_url": manifest_url},
                {"name": "jarvis-pi-arm64.zip", "browser_download_url": package_url},
            ],
        }
        manifest = {
            "schema_version": 1,
            "version": "0.4.0",
            "platform": "linux-aarch64",
            "package_asset": "jarvis-pi-arm64.zip",
            "sha256": "a" * 64,
            "size": 1234,
            "notes": "Nueva versión",
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "VERSION").write_text("0.3.0\n", encoding="utf-8")
            manager = UpdateManager(
                root,
                repository="example/Jarvis",
                session=_Session({api: release_payload, manifest_url: manifest}),
            )
            result = manager.check_for_updates()
        self.assertTrue(result.available)
        self.assertEqual(result.release.version, "0.4.0")
        self.assertEqual(result.release.package_url, package_url)

    def test_install_preserves_user_data_and_can_roll_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "omar_ai_core").mkdir()
            (root / "config").mkdir()
            (root / "VERSION").write_text("0.3.0\n", encoding="utf-8")
            (root / "requirements.txt").write_text("requests>=1\n", encoding="utf-8")
            (root / "omar_ai_core" / "runtime.py").write_text("OLD = True\n", encoding="utf-8")
            (root / ".env").write_text("GEMINI_API_KEY=real-local-key\n", encoding="utf-8")
            (root / "config" / "liquid_visual.json").write_text('{"size":109}\n', encoding="utf-8")
            archive = root / "update.zip"
            _write_update_zip(archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            release = ReleaseInfo(
                version="0.4.0",
                tag="v0.4.0",
                package_url="https://github.com/example/Jarvis/update.zip",
                package_asset="jarvis-pi-arm64.zip",
                sha256=digest,
                size=archive.stat().st_size,
            )
            manager = UpdateManager(root, repository="example/Jarvis")

            def local_download(_release, destination):
                shutil.copy2(archive, destination)

            with patch.object(manager, "_download", side_effect=local_download), patch.object(
                manager, "_validate_installation"
            ):
                result = manager.install(release, install_dependencies=False)

            self.assertEqual((root / "VERSION").read_text().strip(), "0.4.0")
            self.assertIn("UPDATED", (root / "omar_ai_core" / "runtime.py").read_text())
            self.assertIn("real-local-key", (root / ".env").read_text())
            self.assertEqual(
                json.loads((root / "config" / "liquid_visual.json").read_text())["size"], 109
            )
            self.assertTrue(Path(result.backup_path).is_dir())

            restored = manager.rollback_latest()
            self.assertEqual(restored, "0.3.0")
            self.assertEqual((root / "VERSION").read_text().strip(), "0.3.0")
            self.assertIn("OLD", (root / "omar_ai_core" / "runtime.py").read_text())

    def test_extract_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "bad.zip"
            info = zipfile.ZipInfo("omar_ai_core/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(info, "target")
            manager = UpdateManager(root, repository="example/Jarvis")
            with self.assertRaises(UpdateError):
                manager._extract_safely(archive, root / "staging")


if __name__ == "__main__":
    unittest.main()
