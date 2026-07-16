from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests

from .settings import BASE_DIR, get_secret


MANIFEST_ASSET = "jarvis-pi-manifest.json"
DEFAULT_PACKAGE_ASSET = "jarvis-pi-arm64.zip"
UPDATE_DIR_NAME = ".updates"
MAX_PACKAGE_BYTES = 250 * 1024 * 1024
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
VERSION_PATTERN = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
ALLOWED_DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
PROTECTED_TOP_LEVEL = {
    ".env",
    ".git",
    ".updates",
    ".venv",
    "build",
    "config",
    "dist",
    "dist-installer",
    "memory",
    "runtime",
}
REQUIRED_PACKAGE_FILES = {
    "VERSION",
    "requirements.txt",
    "launch_assistant.sh",
    "start_jarvis_pi.sh",
    "omar_ai_core/__init__.py",
    "omar_ai_core/runtime.py",
    "omar_ai_core/updater.py",
}


class UpdateError(RuntimeError):
    """Expected, user-facing update failure."""


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    tag: str
    package_url: str
    package_asset: str
    sha256: str
    size: int
    notes: str = ""
    prerelease: bool = False


@dataclass(frozen=True, slots=True)
class UpdateCheck:
    current_version: str
    available: bool
    release: ReleaseInfo | None


@dataclass(frozen=True, slots=True)
class InstallResult:
    previous_version: str
    installed_version: str
    backup_path: str
    restart_required: bool = True


def read_current_version(base_dir: Path = BASE_DIR) -> str:
    try:
        value = (base_dir / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        value = "0.0.0"
    return value if VERSION_PATTERN.fullmatch(value) else "0.0.0"


def _version_key(value: str) -> tuple[int, int, int, int, tuple[str, ...]]:
    match = VERSION_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise UpdateError(f"Versión no válida: {value!r}")
    prerelease = match.group("prerelease")
    # Stable builds sort after pre-releases with the same numeric version.
    stable_rank = 1 if prerelease is None else 0
    parts = tuple((prerelease or "").lower().split("."))
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        stable_rank,
        parts,
    )


def is_newer(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_name(raw_name: str) -> Path:
    normalized = raw_name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or bool(re.match(r"^[A-Za-z]:", normalized))
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise UpdateError(f"Ruta insegura en el paquete: {raw_name!r}")
    return Path(*pure.parts)


class UpdateManager:
    """Checks and installs versioned Raspberry Pi releases from GitHub.

    User data is never part of an update transaction.  Code is staged first,
    every overwritten file is backed up, and a failed validation restores the
    previous tree before returning control to JARVIS.
    """

    def __init__(
        self,
        base_dir: Path = BASE_DIR,
        repository: str | None = None,
        allow_prerelease: bool | None = None,
        session=None,
        python_executable: str | None = None,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.repository = (
            repository if repository is not None else get_secret("UPDATE_REPOSITORY")
        ).strip()
        configured_prerelease = get_secret("UPDATE_ALLOW_PRERELEASE", "0").lower()
        self.allow_prerelease = (
            allow_prerelease
            if allow_prerelease is not None
            else configured_prerelease in {"1", "true", "yes", "on"}
        )
        self.session = session or requests.Session()
        pi_python = self.base_dir / ".venv" / "bin" / "python"
        self.python_executable = python_executable or (
            str(pi_python) if pi_python.exists() else sys.executable
        )
        self.update_dir = self.base_dir / UPDATE_DIR_NAME
        self.state_path = self.update_dir / "state.json"
        self.lock_path = self.update_dir / "update.lock"

    def _validate_repository(self) -> None:
        if not self.repository:
            raise UpdateError(
                "El repositorio de actualizaciones no está configurado. "
                "Añade UPDATE_REPOSITORY=usuario/repositorio al archivo .env."
            )
        if not REPOSITORY_PATTERN.fullmatch(self.repository):
            raise UpdateError("UPDATE_REPOSITORY debe tener el formato usuario/repositorio.")

    @staticmethod
    def _validate_download_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_DOWNLOAD_HOSTS:
            raise UpdateError("GitHub devolvió una dirección de descarga no permitida.")

    def _get(self, url: str, *, stream: bool = False):
        self._validate_download_url(url)
        try:
            response = self.session.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"Jarvis-Pi-Updater/{read_current_version(self.base_dir)}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=(8, 45),
                stream=stream,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise UpdateError(f"No se pudo contactar con GitHub: {exc}") from exc
        final_url = str(getattr(response, "url", url) or url)
        self._validate_download_url(final_url)
        return response

    def _release_payload(self) -> dict:
        self._validate_repository()
        api_root = f"https://api.github.com/repos/{self.repository}/releases"
        if not self.allow_prerelease:
            payload = self._get(f"{api_root}/latest").json()
            if not isinstance(payload, dict):
                raise UpdateError("GitHub devolvió una respuesta de versión no válida.")
            return payload

        payload = self._get(f"{api_root}?per_page=20").json()
        if not isinstance(payload, list):
            raise UpdateError("GitHub devolvió una lista de versiones no válida.")
        for release in payload:
            if isinstance(release, dict) and not release.get("draft"):
                return release
        raise UpdateError("El repositorio todavía no tiene versiones publicadas.")

    def check_for_updates(self) -> UpdateCheck:
        release_payload = self._release_payload()
        assets = {
            str(item.get("name")): str(item.get("browser_download_url"))
            for item in release_payload.get("assets", [])
            if isinstance(item, dict) and item.get("name") and item.get("browser_download_url")
        }
        manifest_url = assets.get(MANIFEST_ASSET)
        if not manifest_url:
            raise UpdateError(
                f"La versión {release_payload.get('tag_name', '')} no contiene {MANIFEST_ASSET}."
            )
        manifest = self._get(manifest_url).json()
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise UpdateError("El manifiesto de actualización no es compatible.")

        version = str(manifest.get("version", "")).strip().removeprefix("v")
        _version_key(version)
        tag = str(release_payload.get("tag_name", "")).strip()
        if tag.removeprefix("v") != version:
            raise UpdateError("La versión del manifiesto no coincide con la etiqueta de GitHub.")
        if manifest.get("platform") != "linux-aarch64":
            raise UpdateError("La actualización no está preparada para Raspberry Pi de 64 bits.")

        package_asset = str(manifest.get("package_asset") or DEFAULT_PACKAGE_ASSET)
        package_url = assets.get(package_asset)
        if not package_url:
            raise UpdateError(f"La versión no contiene el paquete {package_asset}.")
        sha256 = str(manifest.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise UpdateError("El manifiesto no contiene un SHA-256 válido.")
        try:
            size = int(manifest.get("size", 0))
        except (TypeError, ValueError) as exc:
            raise UpdateError("El tamaño indicado en el manifiesto no es válido.") from exc
        if size <= 0 or size > MAX_PACKAGE_BYTES:
            raise UpdateError("El tamaño del paquete está fuera del límite permitido.")

        release = ReleaseInfo(
            version=version,
            tag=tag,
            package_url=package_url,
            package_asset=package_asset,
            sha256=sha256,
            size=size,
            notes=str(manifest.get("notes") or release_payload.get("body") or "")[:4000],
            prerelease=bool(release_payload.get("prerelease")),
        )
        current = read_current_version(self.base_dir)
        return UpdateCheck(current, is_newer(version, current), release)

    def _download(self, release: ReleaseInfo, destination: Path) -> None:
        response = self._get(release.package_url, stream=True)
        total = 0
        digest = hashlib.sha256()
        with destination.open("wb") as handle:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if not block:
                    continue
                total += len(block)
                if total > MAX_PACKAGE_BYTES or total > release.size:
                    raise UpdateError("La descarga supera el tamaño declarado.")
                digest.update(block)
                handle.write(block)
        if total != release.size:
            raise UpdateError(
                f"Descarga incompleta: se esperaban {release.size} bytes y llegaron {total}."
            )
        if digest.hexdigest().lower() != release.sha256:
            raise UpdateError("El SHA-256 del paquete no coincide; no se instalará.")

    def _extract_safely(self, archive: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > 5000:
                raise UpdateError("El paquete contiene demasiados archivos.")
            expanded_size = sum(info.file_size for info in infos)
            if expanded_size > MAX_PACKAGE_BYTES * 2:
                raise UpdateError("El paquete expandido supera el límite permitido.")
            for info in infos:
                relative = _safe_relative_name(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise UpdateError("El paquete contiene enlaces simbólicos no permitidos.")
                target = destination / relative
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if mode:
                    target.chmod(mode & 0o777)

    @staticmethod
    def _payload_files(staging: Path) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for path in staging.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(staging).as_posix()
            top = relative.split("/", 1)[0]
            if top in PROTECTED_TOP_LEVEL or relative.endswith((".pyc", ".pyo")):
                continue
            result[relative] = path
        missing = REQUIRED_PACKAGE_FILES.difference(result)
        if missing:
            raise UpdateError(
                "El paquete no contiene los archivos obligatorios: " + ", ".join(sorted(missing))
            )
        return result

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)

    def _make_backup(self, files: dict[str, Path], previous_version: str) -> tuple[Path, dict]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.update_dir / "backups" / f"{previous_version}-{stamp}"
        overwritten: list[str] = []
        created: list[str] = []
        for relative in sorted(files):
            current = self.base_dir / relative
            if current.is_file():
                backup_file = backup / "files" / relative
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, backup_file)
                overwritten.append(relative)
            else:
                created.append(relative)
        metadata = {
            "schema_version": 1,
            "previous_version": previous_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "overwritten": overwritten,
            "created": created,
        }
        self._write_json(backup / "backup.json", metadata)
        return backup, metadata

    def _copy_payload(self, files: dict[str, Path]) -> None:
        for relative, source in sorted(files.items()):
            target = self.base_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".{target.name}.jarvis-update.tmp"
            shutil.copy2(source, temporary)
            os.replace(temporary, target)

    def _restore_backup(self, backup: Path, metadata: dict) -> None:
        for relative in reversed(metadata.get("created", [])):
            target = self.base_dir / relative
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        for relative in metadata.get("overwritten", []):
            source = backup / "files" / relative
            target = self.base_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".{target.name}.jarvis-rollback.tmp"
            shutil.copy2(source, temporary)
            os.replace(temporary, target)

    def _run(self, command: list[str], timeout: int) -> None:
        completed = subprocess.run(
            command,
            cwd=self.base_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "error desconocido").strip()
            raise UpdateError(detail[-1200:])

    def _validate_installation(self) -> None:
        self._run(
            [self.python_executable, "-m", "compileall", "-q", "omar_ai_core"],
            timeout=120,
        )
        self._run(
            [
                self.python_executable,
                "-c",
                (
                    "from omar_ai_core.updater import read_current_version; "
                    "from omar_ai_core.runtime import JarvisLive; "
                    "assert read_current_version() != '0.0.0'; print('ok')"
                ),
            ],
            timeout=60,
        )

    def _acquire_lock(self) -> int:
        self.update_dir.mkdir(parents=True, exist_ok=True)
        try:
            return os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            try:
                age = time.time() - self.lock_path.stat().st_mtime
            except OSError:
                age = 0
            if age > 3600:
                self.lock_path.unlink(missing_ok=True)
                return os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            raise UpdateError("Ya hay una actualización en curso.") from exc

    def install(
        self,
        release: ReleaseInfo | None = None,
        *,
        install_dependencies: bool = True,
    ) -> InstallResult:
        if release is None:
            check = self.check_for_updates()
            if not check.available or check.release is None:
                raise UpdateError("Jarvis ya está actualizado.")
            release = check.release

        current = read_current_version(self.base_dir)
        if not is_newer(release.version, current):
            raise UpdateError("La versión solicitada no es posterior a la instalada.")

        lock_fd = self._acquire_lock()
        backup: Path | None = None
        backup_metadata: dict | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="jarvis-update-", dir=self.update_dir) as temp:
                work = Path(temp)
                archive = work / release.package_asset
                staging = work / "staging"
                staging.mkdir()
                self._download(release, archive)
                self._extract_safely(archive, staging)
                files = self._payload_files(staging)
                staged_version = read_current_version(staging)
                if staged_version != release.version:
                    raise UpdateError("La versión interna del paquete no coincide con el manifiesto.")

                old_requirements = (
                    _sha256(self.base_dir / "requirements.txt")
                    if (self.base_dir / "requirements.txt").is_file()
                    else ""
                )
                new_requirements = _sha256(staging / "requirements.txt")
                backup, backup_metadata = self._make_backup(files, current)
                self._copy_payload(files)
                if install_dependencies and old_requirements != new_requirements:
                    self._run(
                        [
                            self.python_executable,
                            "-m",
                            "pip",
                            "install",
                            "-r",
                            str(self.base_dir / "requirements.txt"),
                        ],
                        timeout=1200,
                    )
                self._validate_installation()

            state = {
                "schema_version": 1,
                "status": "installed",
                "previous_version": current,
                "installed_version": release.version,
                "backup_path": str(backup),
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "restart_required": True,
            }
            self._write_json(self.state_path, state)
            return InstallResult(current, release.version, str(backup), True)
        except Exception as exc:
            if backup is not None and backup_metadata is not None:
                try:
                    self._restore_backup(backup, backup_metadata)
                except Exception as rollback_exc:
                    raise UpdateError(
                        f"La actualización falló ({exc}) y la restauración también ({rollback_exc})."
                    ) from rollback_exc
            if isinstance(exc, UpdateError):
                raise
            raise UpdateError(f"No se pudo instalar la actualización: {exc}") from exc
        finally:
            os.close(lock_fd)
            self.lock_path.unlink(missing_ok=True)

    def rollback_latest(self) -> str:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            backup = Path(state["backup_path"])
            metadata = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
        except Exception as exc:
            raise UpdateError("No existe una copia válida para restaurar.") from exc
        self._restore_backup(backup, metadata)
        restored = str(metadata.get("previous_version", "desconocida"))
        state.update(
            status="rolled_back",
            restored_version=restored,
            rolled_back_at=datetime.now(timezone.utc).isoformat(),
            restart_required=True,
        )
        self._write_json(self.state_path, state)
        return restored

    def status(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "schema_version": 1,
                "status": "idle",
                "installed_version": read_current_version(self.base_dir),
                "restart_required": False,
            }


def _print_check(check: UpdateCheck) -> None:
    if check.available and check.release:
        print(
            f"Actualización disponible: {check.current_version} -> {check.release.version}\n"
            f"{check.release.notes}".rstrip()
        )
    else:
        print(f"Jarvis está actualizado ({check.current_version}).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Actualizador seguro de Jarvis para Raspberry Pi")
    parser.add_argument("action", choices=("check", "install", "status", "rollback"))
    parser.add_argument("--yes", action="store_true", help="Confirma instalación o restauración")
    parser.add_argument("--json", action="store_true", help="Imprime resultado JSON")
    args = parser.parse_args(argv)
    manager = UpdateManager()
    try:
        if args.action == "check":
            result = manager.check_for_updates()
            if args.json:
                data = asdict(result)
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                _print_check(result)
        elif args.action == "install":
            if not args.yes:
                raise UpdateError("Repite el comando con --yes para confirmar la instalación.")
            result = manager.install()
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        elif args.action == "rollback":
            if not args.yes:
                raise UpdateError("Repite el comando con --yes para confirmar la restauración.")
            print(f"Versión restaurada: {manager.rollback_latest()}")
        else:
            print(json.dumps(manager.status(), ensure_ascii=False, indent=2))
    except UpdateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
