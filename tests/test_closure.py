"""Portable document-font closure compatibility and integrity tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from frameforge_fonts import (
    FontAsset,
    FontFace,
    FontStatus,
    FontStore,
    IntegrityError,
    export_closure,
    import_closure,
)


def stored_handle(tmp_path: Path):
    store = FontStore(tmp_path / "store")
    face = FontFace(
        family="Closure Sans",
        style="normal",
        weight=400,
        stretch=100,
        provider="fixture",
        source="google-fonts:closuresans",
        locator="ClosureSans-Regular.ttf",
        status=FontStatus.READY,
    )
    handle = store.put(
        face,
        FontAsset(
            data=b"deterministic-font-payload",
            filename="ClosureSans-Regular.ttf",
            source=face.source,
            license_data=b"Open font license fixture",
            license_name="OFL.txt",
        ),
    )
    return store, handle


def test_export_is_deterministic_and_frameforge_v1_compatible(tmp_path: Path) -> None:
    _, handle = stored_handle(tmp_path)
    first = export_closure([handle], tmp_path / "first.fp", generated_from="doc.fg.yaml")
    second = export_closure([handle], tmp_path / "second.fp", generated_from="doc.fg.yaml")

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entry = manifest["fonts"][0]
        assert manifest["fp_version"] == 1
        assert manifest["generated_from"] == "doc.fg.yaml"
        assert entry["family"] == "Closure Sans"
        assert entry["bold"] is False
        assert entry["resolved"] == "Closure Sans"
        assert entry["sha256"] == handle.sha256
        assert archive.read(entry["file"]) == b"deterministic-font-payload"
        assert archive.read(entry["license_file"]) == b"Open font license fixture"


def test_import_verifies_then_materializes_ready_handles(tmp_path: Path) -> None:
    _, handle = stored_handle(tmp_path)
    pack = export_closure([handle], tmp_path / "doc.fp")
    imported = import_closure(pack, FontStore(tmp_path / "other-store"))

    assert len(imported) == 1
    assert imported[0].sha256 == handle.sha256
    assert imported[0].verify()


def test_import_rejects_tampered_font_bytes(tmp_path: Path) -> None:
    _, handle = stored_handle(tmp_path)
    source = export_closure([handle], tmp_path / "doc.fp")
    tampered = tmp_path / "tampered.fp"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as changed:
        for name in original.namelist():
            data = original.read(name)
            changed.writestr(name, b"tampered" if name.startswith("fonts/") else data)

    with pytest.raises(IntegrityError, match="sha256"):
        import_closure(tampered, FontStore(tmp_path / "target"))


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ({"fp_version": 2, "fonts": []}, "version"),
        ({"fp_version": 1, "fonts": {}}, "bounded list"),
        ({"fp_version": 1, "fonts": ["bad"]}, "must be an object"),
        (
            {
                "fp_version": 1,
                "fonts": [{"file": "../font.ttf", "sha256": "0" * 64}],
            },
            "unsafe",
        ),
        (
            {
                "fp_version": 1,
                "fonts": [{"file": "fonts/font.ttf", "sha256": "bad"}],
            },
            "invalid sha256",
        ),
    ],
)
def test_import_rejects_invalid_manifests(
    tmp_path: Path, manifest: dict[str, object], message: str
) -> None:
    pack = tmp_path / "bad.fp"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("fonts/font.ttf", b"font")
    with pytest.raises(IntegrityError, match=message):
        import_closure(pack, FontStore(tmp_path / "store"))


def test_import_rejects_bad_archive_json_and_license_hash(tmp_path: Path) -> None:
    invalid_json = tmp_path / "json.fp"
    with zipfile.ZipFile(invalid_json, "w") as archive:
        archive.writestr("manifest.json", b"{")
    with pytest.raises(IntegrityError, match="valid JSON"):
        import_closure(invalid_json, FontStore(tmp_path / "json-store"))

    _, handle = stored_handle(tmp_path)
    valid = export_closure([handle], tmp_path / "valid.fp")
    bad_license = tmp_path / "license.fp"
    with zipfile.ZipFile(valid) as source, zipfile.ZipFile(bad_license, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, b"changed license" if name.startswith("licenses/") else data)
    with pytest.raises(IntegrityError, match="license sha256"):
        import_closure(bad_license, FontStore(tmp_path / "license-store"))


def test_import_rejects_non_zip_and_missing_members(tmp_path: Path) -> None:
    non_zip = tmp_path / "not.fp"
    non_zip.write_bytes(b"not a zip")
    with pytest.raises(IntegrityError, match="invalid font closure"):
        import_closure(non_zip, FontStore(tmp_path / "nonzip-store"))

    missing = tmp_path / "missing.fp"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"fp_version": 1, "fonts": [
            {"family": "Missing", "file": "fonts/missing.ttf", "sha256": "0" * 64}
        ]}))
    with pytest.raises(IntegrityError, match="missing"):
        import_closure(missing, FontStore(tmp_path / "missing-store"))
