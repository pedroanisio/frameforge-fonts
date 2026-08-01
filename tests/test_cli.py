"""Public command-line behavior."""

from __future__ import annotations

import json
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from frameforge_fonts.cli import main


def make_test_font(path: Path) -> None:
    builder = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "space", "A", "B"]
    builder.setupGlyphOrder(glyphs)
    builder.setupCharacterMap({32: "space", 65: "A", 66: "B"})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in glyphs})
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "CLI Sans",
            "styleName": "Regular",
            "uniqueFontIdentifier": "CLI Sans Regular test",
            "fullName": "CLI Sans Regular",
            "psName": "CLISans-Regular",
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


def test_list_empty_catalog_as_json(tmp_path: Path, capsys) -> None:
    code = main(["--store", str(tmp_path / "store"), "--no-host", "list", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"faces": [], "families": 0}


def test_ensure_unavailable_font_fails_before_composition(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--store",
            str(tmp_path / "store"),
            "--no-host",
            "ensure",
            "Definitely Missing",
        ]
    )

    assert code == 2
    assert "unavailable" in capsys.readouterr().err.lower()


def test_doctor_reports_store_and_optional_provider_state(tmp_path: Path, capsys) -> None:
    code = main(["--store", str(tmp_path / "store"), "--no-host", "doctor", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["store"]["writable"] is True
    assert report["providers"] == []


def test_cli_composition_and_closure_round_trip(tmp_path: Path, capsys) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    make_test_font(font_dir / "CLISans-Regular.ttf")
    store = tmp_path / "store"
    base = ["--store", str(store), "--font-dir", str(font_dir), "--no-host"]

    assert main([*base, "list", "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["families"] == 1
    assert listing["faces"][0]["family"] == "CLI Sans"

    assert main([*base, "ensure", "CLI Sans"]) == 0
    ensured = json.loads(capsys.readouterr().out)
    assert ensured["status"] == "ready"
    assert len(ensured["sha256"]) == 64

    assert main([*base, "measure", "CLI Sans", "AB", "--size", "10"]) == 0
    measured = json.loads(capsys.readouterr().out)
    assert measured["advance_x"] == 12.0
    assert len(measured["glyphs"]) == 2

    pack = tmp_path / "document.fp"
    assert main(
        [*base, "closure", "CLI Sans", "--out", str(pack), "--generated-from", "doc.fg.yaml"]
    ) == 0
    assert capsys.readouterr().out.strip() == str(pack)

    imported_store = tmp_path / "imported"
    assert main(["--store", str(imported_store), "--no-host", "import", str(pack)]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["imported"] == 1
    assert imported["faces"][0]["family"] == "CLI Sans"


def test_cli_human_output_and_environment_providers(tmp_path: Path, capsys, monkeypatch) -> None:
    google_root = tmp_path / "google-fonts"
    (google_root / "ofl").mkdir(parents=True)
    monkeypatch.setenv("FRAMEFORGE_GOOGLE_FONTS_ROOT", str(google_root))
    monkeypatch.setenv("FRAMEFORGE_FONTS_DOCKER_IMAGE", "missing:image")

    code = main(["--store", str(tmp_path / "store"), "--no-host", "doctor"])

    assert code == 0
    output = capsys.readouterr().out
    assert "store:" in output
    assert "google-fonts: available=True" in output
    assert "docker: available=False" in output


def test_cli_human_list_output(tmp_path: Path, capsys) -> None:
    assert main(["--store", str(tmp_path / "store"), "--no-host", "list"]) == 0
    assert capsys.readouterr().out.startswith("0 families, 0 faces")
