"""Catalog provider tests; all external boundaries are deterministic fakes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from frameforge_fonts import FontStatus
from frameforge_fonts.models import FontFace, ProviderError
from frameforge_fonts.providers import (
    DirectoryFontProvider,
    DockerFontProvider,
    GoogleFontsRepositoryProvider,
    HostFontProvider,
)


def make_test_font(path: Path, family: str) -> bytes:
    builder = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "space", "A"]
    builder.setupGlyphOrder(glyphs)
    builder.setupCharacterMap({32: "space", 65: "A"})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in glyphs})
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyphs})
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


def test_directory_provider_discovers_exact_local_faces(tmp_path: Path) -> None:
    data = make_test_font(tmp_path / "LocalSans-Regular.ttf", "Local Sans")
    provider = DirectoryFontProvider(tmp_path, name="project")

    faces = provider.list_faces()

    assert len(faces) == 1
    assert faces[0].family == "Local Sans"
    assert faces[0].status is FontStatus.READY
    assert provider.fetch(faces[0]).data == data


def test_directory_provider_skips_bad_files_and_guards_ownership(tmp_path: Path) -> None:
    (tmp_path / "bad.ttf").write_bytes(b"not a font")
    provider = DirectoryFontProvider(tmp_path, name="project")
    assert provider.available()
    assert provider.list_faces() == ()
    alien = FontFace(
        "Alien", "normal", 400, 100, "other", "other", "font.ttf", FontStatus.READY
    )
    with pytest.raises(ProviderError, match="belongs"):
        provider.fetch(alien)

    absent = DirectoryFontProvider(tmp_path / "absent")
    assert not absent.available()
    assert absent.list_faces() == ()


def test_google_repository_provider_preserves_source_and_license(tmp_path: Path) -> None:
    family_dir = tmp_path / "ofl" / "localsans"
    family_dir.mkdir(parents=True)
    data = make_test_font(family_dir / "LocalSans-Regular.ttf", "Local Sans")
    license_text = "SIL Open Font License test fixture"
    (family_dir / "OFL.txt").write_text(license_text, encoding="utf-8")
    provider = GoogleFontsRepositoryProvider(tmp_path)

    (face,) = provider.list_faces()
    asset = provider.fetch(face)

    assert face.source == "google-fonts:localsans"
    assert face.status is FontStatus.READY
    assert asset.data == data
    assert asset.license_data == license_text.encode()
    assert asset.license_name == "OFL.txt"


def test_host_provider_uses_fontconfig_paths_without_substitution(tmp_path: Path) -> None:
    font_path = tmp_path / "HostSans.ttf"
    data = make_test_font(font_path, "Host Sans")

    def runner(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, f"{font_path}\n".encode(), b"")

    provider = HostFontProvider(runner=runner)
    assert provider.available()
    (face,) = provider.list_faces()
    assert face.family == "Host Sans"
    assert face.source == "host-fontconfig"
    assert provider.fetch(face).data == data


def test_docker_provider_reports_external_then_exports_exact_bytes() -> None:
    font_data = b"docker-font-bytes"
    calls: list[tuple[str, ...]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(command))
        if "info" in command:
            return subprocess.CompletedProcess(command, 0, b'"27.0"\n', b"")
        if "fc-list" in command:
            row = b"/usr/share/fonts/DockerSans.ttf\tDocker Sans\tRegular\t0\n"
            return subprocess.CompletedProcess(command, 0, row, b"")
        return subprocess.CompletedProcess(command, 0, font_data, b"")

    provider = DockerFontProvider("frameforge:fonts", runner=runner)

    assert provider.available()
    (face,) = provider.list_faces()
    assert face.family == "Docker Sans"
    assert face.status is FontStatus.EXTERNAL
    assert provider.fetch(face).data == font_data
    assert all("sh" not in command and "bash" not in command for command in calls)


def test_docker_provider_failure_and_locator_guards() -> None:
    def unavailable(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, b"", b"permission denied")

    provider = DockerFontProvider("frameforge:fonts", runner=unavailable)
    assert not provider.available()
    assert provider.list_faces() == ()

    bad = FontFace(
        "Docker Sans",
        "normal",
        400,
        100,
        "docker",
        "docker:frameforge:fonts",
        "../escape.ttf",
        FontStatus.EXTERNAL,
    )
    with pytest.raises(ProviderError, match="absolute safe"):
        provider.fetch(bad)
    with pytest.raises(ValueError, match="must not be empty"):
        DockerFontProvider(" ")


@pytest.mark.parametrize(
    ("style", "weight"),
    [("Thin", 100), ("Extra Light", 200), ("Light", 300), ("Medium", 500),
     ("Semi Bold", 600), ("Bold", 700), ("Extra Bold", 800), ("Black", 900)],
)
def test_docker_style_weight_mapping(style: str, weight: int) -> None:
    assert DockerFontProvider._weight_from_style(style.casefold()) == weight
