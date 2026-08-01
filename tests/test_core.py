"""Core composition-time font contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from frameforge_fonts import (
    FontAsset,
    FontAxis,
    FontCatalog,
    FontFace,
    FontQuery,
    FontStatus,
    FontStore,
    IntegrityError,
    ProviderError,
    UnavailableFontError,
    shape_text,
)
from frameforge_fonts.store import default_store_root


def make_test_font(path: Path, family: str = "FrameForge Test") -> bytes:
    """Create a deterministic tiny TrueType font suitable for shaping tests."""
    builder = FontBuilder(1000, isTTF=True)
    glyph_order = [".notdef", "space", "A", "B"]
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({32: "space", 65: "A", 66: "B"})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in glyph_order})
    builder.setupHorizontalMetrics(
        {".notdef": (500, 0), "space": (300, 0), "A": (600, 0), "B": (620, 0)}
    )
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": f"{family} Regular test",
            "fullName": f"{family} Regular",
            "psName": family.replace(" ", "") + "-Regular",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        usWeightClass=400,
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)
    return path.read_bytes()


class MemoryProvider:
    """Small provider double proving materialization and exact resolution."""

    name = "memory"

    def __init__(self, face: FontFace, asset: FontAsset) -> None:
        self.face = face
        self.asset = asset

    def available(self) -> bool:
        return True

    def list_faces(self) -> tuple[FontFace, ...]:
        return (self.face,)

    def fetch(self, face: FontFace) -> FontAsset:
        assert face == self.face
        return self.asset


def test_font_query_rejects_invalid_composition_requests() -> None:
    with pytest.raises(ValueError, match="family"):
        FontQuery("  ")
    with pytest.raises(ValueError, match="weight"):
        FontQuery("Inter", weight=0)
    with pytest.raises(ValueError, match="stretch"):
        FontQuery("Inter", stretch=0)
    with pytest.raises(ValueError, match="style"):
        FontQuery("Inter", style="roman")
    with pytest.raises(ValueError, match="four characters"):
        FontQuery("Inter", axes={"weight": 400})
    with pytest.raises(ValueError, match="finite"):
        FontQuery("Inter", axes={"wght": float("inf")})


def test_value_objects_reject_unsafe_or_impossible_faces(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="family"):
        FontFace("", "normal", 400, 100, "test", "test", "font.ttf", FontStatus.READY)
    with pytest.raises(ValueError, match="style"):
        FontFace("Test", "roman", 400, 100, "test", "test", "font.ttf", FontStatus.READY)
    with pytest.raises(ValueError, match="index"):
        FontFace(
            "Test", "normal", 400, 100, "test", "test", "font.ttf", FontStatus.READY, -1
        )
    with pytest.raises(ValueError, match="data"):
        FontAsset(data=b"", filename="font.ttf", source="test")
    with pytest.raises(ValueError, match="basename"):
        FontAsset(data=b"font", filename="nested/font.ttf", source="test")

    missing = replace(
        FontStore(tmp_path / "store").put(
            FontFace(
                "Test", "normal", 400, 100, "test", "test", "font.ttf", FontStatus.READY
            ),
            FontAsset(data=b"font", filename="font.ttf", source="test"),
        ),
        path=tmp_path / "absent.ttf",
    )
    with pytest.raises(IntegrityError, match="not readable"):
        missing.verify()


def test_variable_face_support_is_exact_across_axes() -> None:
    face = FontFace(
        family="Variable Sans",
        style="normal",
        weight=400,
        stretch=100,
        provider="test",
        source="test",
        locator="variable.ttf",
        status=FontStatus.FETCHABLE,
        axes=(
            FontAxis("wght", 100, 400, 900),
            FontAxis("wdth", 75, 100, 125),
            FontAxis("opsz", 8, 14, 72),
        ),
    )

    assert face.supports(FontQuery("variable sans", weight=650, stretch=110, axes={"opsz": 24}))
    assert not face.supports(FontQuery("Variable Sans", style="italic"))
    assert not face.supports(FontQuery("Variable Sans", weight=950))
    assert not face.supports(FontQuery("Variable Sans", axes={"slnt": -5}))


def test_catalog_is_exact_and_never_fuzzy_substitutes(tmp_path: Path) -> None:
    font_path = tmp_path / "font.ttf"
    data = make_test_font(font_path)
    face = FontFace(
        family="FrameForge Test",
        style="normal",
        weight=400,
        stretch=100,
        provider="memory",
        source="memory:test",
        locator="font.ttf",
        status=FontStatus.FETCHABLE,
    )
    catalog = FontCatalog(
        FontStore(tmp_path / "store"),
        [MemoryProvider(face, FontAsset(data=data, filename="font.ttf", source=face.source))],
    )

    assert catalog.resolve(FontQuery("frameforge test")) == face
    with pytest.raises(UnavailableFontError, match="FrameForge Tests"):
        catalog.resolve(FontQuery("FrameForge Tests"))


def test_ensure_returns_verified_content_addressed_handle(tmp_path: Path) -> None:
    font_path = tmp_path / "font.ttf"
    data = make_test_font(font_path)
    face = FontFace(
        family="FrameForge Test",
        style="normal",
        weight=400,
        stretch=100,
        provider="memory",
        source="memory:test",
        locator="font.ttf",
        status=FontStatus.FETCHABLE,
    )
    catalog = FontCatalog(
        FontStore(tmp_path / "store"),
        [MemoryProvider(face, FontAsset(data=data, filename="font.ttf", source=face.source))],
    )

    first = catalog.ensure(FontQuery("FrameForge Test"))
    second = catalog.ensure(FontQuery("FrameForge Test"))

    assert first == second
    assert first.path.name == first.sha256 + ".ttf"
    assert first.verify()
    assert catalog.status(FontQuery("FrameForge Test")) is FontStatus.READY


def test_shaping_uses_only_verified_handle_and_exact_advances(tmp_path: Path) -> None:
    data = make_test_font(tmp_path / "font.ttf")
    store = FontStore(tmp_path / "store")
    handle = store.put(
        FontFace(
            family="FrameForge Test",
            style="normal",
            weight=400,
            stretch=100,
            provider="test",
            source="test:generated",
            locator="font.ttf",
            status=FontStatus.READY,
        ),
        FontAsset(data=data, filename="font.ttf", source="test:generated"),
    )

    run = shape_text(handle, "AB", size_px=10)

    assert run.advance_x == pytest.approx(12.2)
    assert run.ascent == pytest.approx(8.0)
    assert run.descent == pytest.approx(2.0)
    assert run.missing_glyphs == 0
    assert len(run.glyphs) == 2
    assert run.font_sha256 == handle.sha256

    handle.path.write_bytes(b"tampered")
    with pytest.raises(IntegrityError, match="sha256"):
        shape_text(replace(handle), "AB", size_px=10)


def test_missing_glyph_is_reported_not_silently_accepted(tmp_path: Path) -> None:
    data = make_test_font(tmp_path / "font.ttf")
    store = FontStore(tmp_path / "store")
    face = FontFace(
        family="FrameForge Test",
        style="normal",
        weight=400,
        stretch=100,
        provider="test",
        source="test:generated",
        locator="font.ttf",
        status=FontStatus.READY,
    )
    handle = store.put(face, FontAsset(data=data, filename="font.ttf", source=face.source))

    assert shape_text(handle, "AΩ", size_px=10).missing_glyphs == 1


def test_shape_options_and_invalid_inputs_are_explicit(tmp_path: Path) -> None:
    data = make_test_font(tmp_path / "font.ttf")
    store = FontStore(tmp_path / "store")
    face = FontFace(
        "FrameForge Test", "normal", 400, 100, "test", "test", "font.ttf", FontStatus.READY
    )
    handle = store.put(face, FontAsset(data=data, filename="font.ttf", source="test"))

    run = shape_text(
        handle,
        "AB",
        size_px=12,
        direction="ltr",
        language="en",
        script="Latn",
        features={"kern": True},
    )
    assert run.advance_x > 0
    with pytest.raises(TypeError, match="FontHandle"):
        shape_text("FrameForge Test", "AB", size_px=12)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        shape_text(handle, "AB", size_px=0)


def test_store_locations_index_validation_and_corruption(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FRAMEFORGE_FONTS_HOME", str(tmp_path / "explicit"))
    assert default_store_root() == tmp_path / "explicit"
    monkeypatch.delenv("FRAMEFORGE_FONTS_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_store_root() == tmp_path / "xdg" / "frameforge-fonts"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert default_store_root() == tmp_path / "home" / ".local/share/frameforge-fonts"

    store = FontStore(tmp_path / "store")
    store.index_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        store.list_handles()


def test_catalog_ready_gate_families_and_variable_materialization(tmp_path: Path) -> None:
    data = make_test_font(tmp_path / "font.ttf", "Variable Sans")
    face = FontFace(
        family="Variable Sans",
        style="normal",
        weight=400,
        stretch=100,
        provider="memory",
        source="memory:variable",
        locator="font.ttf",
        status=FontStatus.FETCHABLE,
        axes=(FontAxis("wght", 100, 400, 900),),
    )
    provider = MemoryProvider(face, FontAsset(data=data, filename="font.ttf", source=face.source))
    catalog = FontCatalog(FontStore(tmp_path / "store"), [provider])
    query = FontQuery("Variable Sans", weight=650)

    assert catalog.families() == ("Variable Sans",)
    assert catalog.status(FontQuery("Missing")) is FontStatus.UNAVAILABLE
    with pytest.raises(UnavailableFontError, match="not ready"):
        catalog.require_ready(query)
    handle = catalog.ensure(query)
    assert handle.weight == 650
    assert handle.variations == {"wght": 650.0}
    assert catalog.require_ready(query) == handle

    with pytest.raises(ValueError, match="unique"):
        FontCatalog(FontStore(tmp_path / "other"), [provider, provider])


def test_catalog_rejects_provider_family_mismatch(tmp_path: Path) -> None:
    advertised_path = tmp_path / "advertised.ttf"
    returned_path = tmp_path / "returned.ttf"
    make_test_font(advertised_path, "Advertised Sans")
    returned = make_test_font(returned_path, "Different Sans")
    face = FontFace(
        "Advertised Sans",
        "normal",
        400,
        100,
        "memory",
        "memory:mismatch",
        "font.ttf",
        FontStatus.FETCHABLE,
    )
    catalog = FontCatalog(
        FontStore(tmp_path / "store"),
        [MemoryProvider(face, FontAsset(returned, "font.ttf", face.source))],
    )

    with pytest.raises(ProviderError, match="Different Sans"):
        catalog.ensure(FontQuery("Advertised Sans"))
