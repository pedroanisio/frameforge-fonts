"""Deterministic FrameForge ``.fp`` document-font closure import and export."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .models import (
    FontAsset,
    FontAxis,
    FontFace,
    FontHandle,
    FontStatus,
    IntegrityError,
)
from .store import FontStore

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_MAX_FACES = 10_000
_MAX_ASSET_BYTES = 256 * 1024 * 1024


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def _safe_member(name: object, prefix: str) -> str:
    if not isinstance(name, str):
        raise IntegrityError(f"closure {prefix} member must be a string")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 2 or path.parts[0] != prefix:
        raise IntegrityError(f"unsafe closure member path: {name!r}")
    return name


def _read_bounded(archive: zipfile.ZipFile, member: str) -> bytes:
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise IntegrityError(f"closure is missing {member!r}") from exc
    if info.file_size > _MAX_ASSET_BYTES:
        raise IntegrityError(f"closure member is too large: {member!r}")
    return archive.read(info)


def export_closure(
    handles: list[FontHandle] | tuple[FontHandle, ...],
    path: str | Path,
    *,
    generated_from: str | None = None,
) -> Path:
    """Write a deterministic, FrameForge-v1-compatible font closure."""
    ordered = sorted(
        handles,
        key=lambda handle: (
            handle.family.casefold(),
            handle.style,
            handle.weight,
            handle.stretch,
            handle.sha256,
            handle.face_index,
        ),
    )
    entries: list[dict[str, Any]] = []
    members: dict[str, bytes] = {}
    for handle in ordered:
        handle.verify()
        suffix = handle.path.suffix.lower()
        if suffix not in {".ttf", ".otf", ".ttc", ".otc"}:
            suffix = ".font"
        font_member = f"fonts/{handle.sha256}{suffix}"
        members.setdefault(font_member, handle.path.read_bytes())
        license_member: str | None = None
        license_digest: str | None = None
        if handle.license_path is not None:
            license_data = handle.license_path.read_bytes()
            license_digest = hashlib.sha256(license_data).hexdigest()
            license_member = f"licenses/{handle.sha256}-{license_digest[:12]}.txt"
            members.setdefault(license_member, license_data)
        entries.append(
            {
                "axes": [
                    {
                        "default": axis.default,
                        "maximum": axis.maximum,
                        "minimum": axis.minimum,
                        "tag": axis.tag,
                    }
                    for axis in handle.axes
                ],
                "bold": handle.weight >= 600,
                "face_index": handle.face_index,
                "family": handle.family,
                "file": font_member,
                "license_file": license_member,
                "license_sha256": license_digest,
                "resolved": handle.family,
                "sha256": handle.sha256,
                "source": handle.source,
                "stretch": handle.stretch,
                "style": handle.style,
                "variations": dict(sorted(handle.variations.items())),
                "weight": handle.weight,
            }
        )
    manifest: dict[str, Any] = {
        "closure_format": "frameforge-fonts/1",
        "fonts": entries,
        "fp_version": 1,
        "generated_from": generated_from,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr(_zip_info("manifest.json"), manifest_data)
        for name in sorted(members):
            archive.writestr(_zip_info(name), members[name])
    return destination


def import_closure(path: str | Path, store: FontStore) -> tuple[FontHandle, ...]:
    """Verify and materialize all faces from a portable ``.fp`` closure."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise IntegrityError(f"invalid font closure: {path}") from exc
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise IntegrityError("closure contains duplicate archive members")
        try:
            manifest = json.loads(_read_bounded(archive, "manifest.json"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrityError("closure manifest is not valid JSON") from exc
        if not isinstance(manifest, dict) or manifest.get("fp_version") != 1:
            raise IntegrityError("unsupported closure manifest version")
        entries = manifest.get("fonts")
        if not isinstance(entries, list) or len(entries) > _MAX_FACES:
            raise IntegrityError("closure fonts must be a bounded list")
        handles: list[FontHandle] = []
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise IntegrityError("closure font entry must be an object")
            font_member = _safe_member(raw_entry.get("file"), "fonts")
            expected = raw_entry.get("sha256")
            if not isinstance(expected, str) or _DIGEST.fullmatch(expected) is None:
                raise IntegrityError(f"invalid sha256 for {font_member}")
            data = _read_bounded(archive, font_member)
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise IntegrityError(
                    f"font sha256 mismatch for {font_member}: expected {expected}, got {actual}"
                )
            license_data: bytes | None = None
            license_name: str | None = None
            if raw_entry.get("license_file") is not None:
                license_member = _safe_member(raw_entry["license_file"], "licenses")
                license_data = _read_bounded(archive, license_member)
                license_name = PurePosixPath(license_member).name
                expected_license = raw_entry.get("license_sha256")
                actual_license = hashlib.sha256(license_data).hexdigest()
                if expected_license is not None and actual_license != expected_license:
                    raise IntegrityError(f"license sha256 mismatch for {license_member}")
            try:
                axes = tuple(FontAxis(**axis) for axis in raw_entry.get("axes", []))
                face = FontFace(
                    family=str(raw_entry["family"]),
                    style=str(raw_entry.get("style", "normal")),
                    weight=int(raw_entry.get("weight", 700 if raw_entry.get("bold") else 400)),
                    stretch=int(raw_entry.get("stretch", 100)),
                    provider="closure",
                    source=str(raw_entry.get("source", f"closure:{Path(path).name}")),
                    locator=font_member,
                    status=FontStatus.READY,
                    face_index=int(raw_entry.get("face_index", 0)),
                    axes=axes,
                )
                variations = {
                    str(tag): float(value)
                    for tag, value in raw_entry.get("variations", {}).items()
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise IntegrityError(f"invalid closure metadata for {font_member}") from exc
            handle = store.put(
                face,
                FontAsset(
                    data=data,
                    filename=PurePosixPath(font_member).name,
                    source=face.source,
                    license_data=license_data,
                    license_name=license_name,
                ),
                variations=variations,
            )
            if handle.sha256 != expected:
                raise IntegrityError(f"store changed font digest for {font_member}")
            handles.append(handle)
        return tuple(handles)
