"""Font sources for project directories, Google Fonts clones, hosts, and Docker."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from fontTools.ttLib import TTCollection, TTFont, TTLibError  # type: ignore[import-untyped]

from .models import FontAsset, FontAxis, FontFace, FontStatus, ProviderError


class FontProvider(Protocol):
    """Provider boundary used by :class:`frameforge_fonts.FontCatalog`."""

    name: str

    def available(self) -> bool:
        """Return whether the provider can currently be queried."""

    def list_faces(self) -> tuple[FontFace, ...]:
        """Return every exact face the provider can materialize."""

    def fetch(self, face: FontFace) -> FontAsset:
        """Return the exact bytes represented by *face*."""


Runner = Callable[[list[str]], subprocess.CompletedProcess[bytes]]
_FONT_SUFFIXES = frozenset({".ttf", ".otf", ".ttc", ".otc"})
_WIDTH_PERCENT = {1: 50, 2: 62, 3: 75, 4: 87, 5: 100, 6: 112, 7: 125, 8: 150, 9: 200}


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, capture_output=True, check=False)  # noqa: S603


def _font_name(font: TTFont, identifiers: Sequence[int]) -> str | None:
    table = font.get("name")
    if table is None:
        return None
    for identifier in identifiers:
        records = [record for record in table.names if record.nameID == identifier]
        records.sort(key=lambda record: (record.platformID != 3, record.langID != 0x409))
        for record in records:
            try:
                value = cast(str, record.toUnicode()).strip()
            except (UnicodeDecodeError, AttributeError):
                continue
            if value:
                return value
    return None


def font_asset_family(asset: FontAsset, face_index: int = 0) -> str:
    """Read the authoritative family name from provider-returned bytes."""
    try:
        with TTFont(BytesIO(asset.data), lazy=True, fontNumber=face_index) as font:
            family = _font_name(font, (16, 1))
    except (TTLibError, OSError) as exc:
        raise ProviderError(f"provider returned an invalid font asset: {asset.filename}") from exc
    if family is None:
        raise ProviderError(f"provider font asset has no family name: {asset.filename}")
    return family


def _inspect_face(
    path: Path,
    *,
    provider: str,
    source: str,
    status: FontStatus,
    locator: str | None = None,
    face_index: int = 0,
) -> FontFace:
    with TTFont(path, lazy=True, fontNumber=face_index) as font:
        family = _font_name(font, (16, 1))
        if family is None:
            raise ProviderError(f"font has no family name: {path}")
        subfamily = (_font_name(font, (17, 2)) or "Regular").casefold()
        style = "italic" if "italic" in subfamily else "oblique" if "oblique" in subfamily else "normal"
        os2 = font.get("OS/2")
        weight = int(getattr(os2, "usWeightClass", 400))
        width_class = int(getattr(os2, "usWidthClass", 5))
        stretch = _WIDTH_PERCENT.get(width_class, 100)
        axes: tuple[FontAxis, ...] = ()
        if "fvar" in font:
            axes = tuple(
                FontAxis(
                    tag=str(axis.axisTag),
                    minimum=float(axis.minValue),
                    default=float(axis.defaultValue),
                    maximum=float(axis.maxValue),
                )
                for axis in font["fvar"].axes
            )
    return FontFace(
        family=family,
        style=style,
        weight=weight,
        stretch=stretch,
        provider=provider,
        source=source,
        locator=locator or str(path.resolve()),
        status=status,
        face_index=face_index,
        axes=axes,
    )


def _inspect_faces(
    path: Path,
    *,
    provider: str,
    source: str,
    status: FontStatus,
) -> tuple[FontFace, ...]:
    face_count = 1
    if path.suffix.lower() in {".ttc", ".otc"}:
        collection = TTCollection(path, lazy=True)
        try:
            face_count = len(collection.fonts)
        finally:
            collection.close()
    return tuple(
        _inspect_face(
            path,
            provider=provider,
            source=source,
            status=status,
            face_index=face_index,
        )
        for face_index in range(face_count)
    )


class DirectoryFontProvider:
    """Expose exact fonts recursively from a project or user directory."""

    def __init__(self, root: str | Path, *, name: str = "directory") -> None:
        self.root = Path(root).expanduser().resolve()
        self.name = name
        self._cache: tuple[FontFace, ...] | None = None

    def available(self) -> bool:
        return self.root.is_dir()

    def list_faces(self) -> tuple[FontFace, ...]:
        if self._cache is not None:
            return self._cache
        if not self.available():
            return ()
        faces: list[FontFace] = []
        paths = sorted(
            path for path in self.root.rglob("*") if path.suffix.lower() in _FONT_SUFFIXES
        )
        for path in paths:
            relative = path.relative_to(self.root).as_posix()
            try:
                faces.extend(
                    _inspect_faces(
                        path,
                        provider=self.name,
                        source=f"{self.name}:{relative}",
                        status=FontStatus.READY,
                    )
                )
            except (OSError, TTLibError, ProviderError):
                continue
        self._cache = tuple(faces)
        return self._cache

    def fetch(self, face: FontFace) -> FontAsset:
        self._assert_ours(face)
        path = Path(face.locator)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ProviderError(f"cannot read font from {self.name}: {path}") from exc
        return FontAsset(data=data, filename=path.name, source=face.source)

    def _assert_ours(self, face: FontFace) -> None:
        if face.provider != self.name:
            raise ProviderError(f"face belongs to provider {face.provider!r}, not {self.name!r}")


class GoogleFontsRepositoryProvider(DirectoryFontProvider):
    """Expose all TTF/OTF faces in a local shallow clone of ``google/fonts``."""

    name = "google-fonts"

    def __init__(self, root: str | Path) -> None:
        super().__init__(root, name=self.name)

    def available(self) -> bool:
        return self.root.is_dir() and any((self.root / license_dir).is_dir() for license_dir in ("ofl", "apache", "ufl"))

    def list_faces(self) -> tuple[FontFace, ...]:
        if self._cache is not None:
            return self._cache
        if not self.available():
            return ()
        faces: list[FontFace] = []
        for license_dir in ("apache", "ofl", "ufl"):
            base = self.root / license_dir
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.suffix.lower() not in _FONT_SUFFIXES:
                    continue
                slug = path.parent.name
                try:
                    faces.extend(
                        _inspect_faces(
                            path,
                            provider=self.name,
                            source=f"google-fonts:{slug}",
                            status=FontStatus.READY,
                        )
                    )
                except (OSError, TTLibError, ProviderError):
                    continue
        self._cache = tuple(faces)
        return self._cache

    def fetch(self, face: FontFace) -> FontAsset:
        self._assert_ours(face)
        path = Path(face.locator)
        license_path = next(
            (
                candidate
                for candidate in (
                    path.parent / "OFL.txt",
                    path.parent / "LICENSE.txt",
                    path.parent / "UFL.txt",
                )
                if candidate.is_file()
            ),
            None,
        )
        try:
            data = path.read_bytes()
            license_data = license_path.read_bytes() if license_path else None
        except OSError as exc:
            raise ProviderError(f"cannot read Google Fonts asset: {path}") from exc
        return FontAsset(
            data=data,
            filename=path.name,
            source=face.source,
            license_data=license_data,
            license_name=license_path.name if license_path else None,
        )


class HostFontProvider(DirectoryFontProvider):
    """Expose the current host's fontconfig files as one optional provider."""

    name = "host-fontconfig"

    def __init__(self, *, runner: Runner | None = None) -> None:
        self.root = Path("/")
        self._runner = runner or _default_runner
        self._custom_runner = runner is not None
        self._cache: tuple[FontFace, ...] | None = None

    def available(self) -> bool:
        return self._custom_runner or shutil.which("fc-list") is not None

    def list_faces(self) -> tuple[FontFace, ...]:
        if self._cache is not None:
            return self._cache
        if not self.available():
            return ()
        result = self._runner(["fc-list", "--format", "%{file}\\n"])
        if result.returncode != 0:
            return ()
        faces: list[FontFace] = []
        paths = sorted({Path(line) for line in result.stdout.decode(errors="replace").splitlines() if line})
        for path in paths:
            try:
                faces.extend(
                    _inspect_faces(
                        path,
                        provider=self.name,
                        source="host-fontconfig",
                        status=FontStatus.READY,
                    )
                )
            except (OSError, TTLibError, ProviderError):
                continue
        self._cache = tuple(faces)
        return self._cache


class DockerFontProvider:
    """Catalog and export exact font bytes from an optional Docker image."""

    name = "docker"

    def __init__(self, image: str, *, runner: Runner | None = None) -> None:
        if not image.strip():
            raise ValueError("Docker image must not be empty")
        self.image = image
        self._runner = runner or _default_runner
        self._custom_runner = runner is not None
        self._cache: tuple[FontFace, ...] | None = None

    def available(self) -> bool:
        if not self._custom_runner and shutil.which("docker") is None:
            return False
        result = self._runner(["docker", "info", "--format", "{{json .ServerVersion}}"])
        return result.returncode == 0

    def list_faces(self) -> tuple[FontFace, ...]:
        if self._cache is not None:
            return self._cache
        if not self.available():
            return ()
        result = self._runner(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "fc-list",
                self.image,
                "--format",
                "%{file}\\t%{family}\\t%{style}\\t%{index}\\n",
            ]
        )
        if result.returncode != 0:
            raise ProviderError(result.stderr.decode(errors="replace").strip() or "Docker fc-list failed")
        faces: list[FontFace] = []
        seen: set[tuple[str, str, str, int]] = set()
        for row in result.stdout.decode(errors="replace").splitlines():
            parts = row.split("\t")
            if len(parts) != 4:
                continue
            locator, families, style_name, raw_index = (part.strip() for part in parts)
            family = families.split(",", 1)[0].strip()
            try:
                face_index = int(raw_index or 0)
            except ValueError:
                continue
            key = (locator, family, style_name, face_index)
            if not locator or not family or key in seen:
                continue
            seen.add(key)
            style_folded = style_name.casefold()
            style = "italic" if "italic" in style_folded else "oblique" if "oblique" in style_folded else "normal"
            weight = self._weight_from_style(style_folded)
            faces.append(
                FontFace(
                    family=family,
                    style=style,
                    weight=weight,
                    stretch=100,
                    provider=self.name,
                    source=f"docker:{self.image}",
                    locator=locator,
                    status=FontStatus.EXTERNAL,
                    face_index=face_index,
                )
            )
        self._cache = tuple(sorted(faces, key=lambda face: (face.family.casefold(), face.weight, face.style, face.locator)))
        return self._cache

    def fetch(self, face: FontFace) -> FontAsset:
        if face.provider != self.name:
            raise ProviderError(f"face belongs to provider {face.provider!r}, not {self.name!r}")
        path = PurePosixPath(face.locator)
        if not path.is_absolute() or ".." in path.parts:
            raise ProviderError(f"Docker font locator must be an absolute safe path: {face.locator}")
        result = self._runner(
            ["docker", "run", "--rm", "--entrypoint", "cat", self.image, face.locator]
        )
        if result.returncode != 0 or not result.stdout:
            raise ProviderError(
                result.stderr.decode(errors="replace").strip()
                or f"Docker could not export {face.locator}"
            )
        return FontAsset(
            data=result.stdout,
            filename=path.name,
            source=face.source,
        )

    @staticmethod
    def _weight_from_style(style: str) -> int:
        weights = (
            ("thin", 100),
            ("extralight", 200),
            ("extra light", 200),
            ("light", 300),
            ("medium", 500),
            ("semibold", 600),
            ("semi bold", 600),
            ("extrabold", 800),
            ("extra bold", 800),
            ("black", 900),
            ("bold", 700),
        )
        return next((value for name, value in weights if name in style), 400)
