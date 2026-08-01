"""Content-addressed user-space storage for composition-ready font handles."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import FontAsset, FontAxis, FontFace, FontHandle, FontQuery, IntegrityError


def default_store_root() -> Path:
    """Return the user-space store root without requiring elevated privileges."""
    explicit = os.environ.get("FRAMEFORGE_FONTS_HOME")
    if explicit:
        return Path(explicit).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home).expanduser() / "frameforge-fonts"
    return Path.home() / ".local" / "share" / "frameforge-fonts"


class FontStore:
    """Persist exact bytes and face metadata under SHA-256-addressed paths."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_store_root()
        self.blobs = self.root / "blobs" / "sha256"
        self.licenses = self.root / "licenses"
        self.index_path = self.root / "index.json"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.licenses.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        face: FontFace,
        asset: FontAsset,
        *,
        variations: dict[str, float] | None = None,
    ) -> FontHandle:
        """Materialize *asset* atomically and return its verified handle."""
        digest = hashlib.sha256(asset.data).hexdigest()
        suffix = Path(asset.filename).suffix.lower()
        if suffix not in {".ttf", ".otf", ".ttc", ".otc"}:
            suffix = ".font"
        blob_path = self.blobs / digest[:2] / f"{digest}{suffix}"
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if blob_path.exists():
            existing = hashlib.sha256(blob_path.read_bytes()).hexdigest()
            if existing != digest:
                raise IntegrityError(f"content-addressed font path is corrupt: {blob_path}")
        else:
            self._atomic_write(blob_path, asset.data)

        license_path: Path | None = None
        if asset.license_data is not None:
            license_digest = hashlib.sha256(asset.license_data).hexdigest()
            license_path = self.licenses / f"{digest}-{license_digest[:12]}.txt"
            if not license_path.exists():
                self._atomic_write(license_path, asset.license_data)

        handle = FontHandle(
            family=face.family,
            style=face.style,
            weight=face.weight,
            stretch=face.stretch,
            sha256=digest,
            path=blob_path,
            source=asset.source,
            provider=face.provider,
            face_index=face.face_index,
            axes=face.axes,
            variations=dict(variations or {}),
            license_path=license_path,
        )
        self._record(handle)
        handle.verify()
        return handle

    def list_handles(self) -> tuple[FontHandle, ...]:
        """List indexed handles whose blobs are still present."""
        handles: list[FontHandle] = []
        for entry in self._read_index():
            handle = self._decode(entry)
            if handle.path.is_file():
                handles.append(handle)
        return tuple(handles)

    def find(self, query: FontQuery) -> FontHandle | None:
        """Return a verified exact handle for *query*, if already materialized."""
        candidates = [handle for handle in self.list_handles() if handle.as_face().supports(query)]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.sha256, item.face_index))
        handle = candidates[0]
        handle.verify()
        return handle

    def _record(self, handle: FontHandle) -> None:
        entries = self._read_index()
        encoded = self._encode(handle)
        identity = self._identity(encoded)
        retained = [entry for entry in entries if self._identity(entry) != identity]
        retained.append(encoded)
        retained.sort(key=self._identity)
        payload = (json.dumps(retained, indent=2, sort_keys=True) + "\n").encode()
        self._atomic_write(self.index_path, payload)

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"font store index must be a list: {self.index_path}")
        return [entry for entry in data if isinstance(entry, dict)]

    @staticmethod
    def _identity(entry: dict[str, Any]) -> tuple[str, str, int, int, str, int]:
        return (
            str(entry["family"]).casefold(),
            str(entry["style"]),
            int(entry["weight"]),
            int(entry["stretch"]),
            str(entry["sha256"]),
            int(entry.get("face_index", 0)),
        )

    @staticmethod
    def _encode(handle: FontHandle) -> dict[str, Any]:
        return {
            "axes": [
                {
                    "tag": axis.tag,
                    "minimum": axis.minimum,
                    "default": axis.default,
                    "maximum": axis.maximum,
                }
                for axis in handle.axes
            ],
            "face_index": handle.face_index,
            "family": handle.family,
            "license_path": str(handle.license_path) if handle.license_path else None,
            "path": str(handle.path),
            "provider": handle.provider,
            "sha256": handle.sha256,
            "source": handle.source,
            "stretch": handle.stretch,
            "style": handle.style,
            "variations": dict(sorted(handle.variations.items())),
            "weight": handle.weight,
        }

    @staticmethod
    def _decode(entry: dict[str, Any]) -> FontHandle:
        return FontHandle(
            family=str(entry["family"]),
            style=str(entry["style"]),
            weight=int(entry["weight"]),
            stretch=int(entry["stretch"]),
            sha256=str(entry["sha256"]),
            path=Path(str(entry["path"])),
            source=str(entry["source"]),
            provider=str(entry["provider"]),
            face_index=int(entry.get("face_index", 0)),
            axes=tuple(FontAxis(**axis) for axis in entry.get("axes", [])),
            variations={
                str(tag): float(value) for tag, value in entry.get("variations", {}).items()
            },
            license_path=(
                Path(str(entry["license_path"])) if entry.get("license_path") else None
            ),
        )

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
