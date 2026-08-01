"""Value objects shared by catalogs, stores, providers, and shapers."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType


class FontError(Exception):
    """Base class for expected FrameForge Fonts failures."""


class UnavailableFontError(FontError):
    """Raised when an exact requested face cannot be materialized."""


class IntegrityError(FontError):
    """Raised when pinned font bytes do not match their digest."""


class ProviderError(FontError):
    """Raised when a configured font provider cannot fulfill its contract."""


class ShapingUnavailableError(FontError):
    """Raised when the exact shaping engine is unavailable."""


class FontStatus(str, Enum):
    """Composition availability, ordered from usable now to unavailable."""

    READY = "ready"
    FETCHABLE = "fetchable"
    EXTERNAL = "external"
    UNAVAILABLE = "unavailable"


_STYLES = frozenset({"normal", "italic", "oblique"})


@dataclass(frozen=True, slots=True)
class FontQuery:
    """An exact face request made before document composition."""

    family: str
    weight: int = 400
    style: str = "normal"
    stretch: int = 100
    axes: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        family = self.family.strip()
        style = self.style.strip().lower()
        if not family:
            raise ValueError("font family must not be empty")
        if not 1 <= self.weight <= 1000:
            raise ValueError("font weight must be between 1 and 1000")
        if style not in _STYLES:
            raise ValueError(f"font style must be one of {sorted(_STYLES)}")
        if not 1 <= self.stretch <= 1000:
            raise ValueError("font stretch must be between 1 and 1000")
        clean_axes: dict[str, float] = {}
        for tag, value in self.axes.items():
            clean_tag = tag.strip()
            number = float(value)
            if len(clean_tag) != 4:
                raise ValueError(f"variation axis tag must have four characters: {tag!r}")
            if not math.isfinite(number):
                raise ValueError(f"variation axis value must be finite: {tag!r}")
            clean_axes[clean_tag] = number
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "style", style)
        object.__setattr__(self, "axes", MappingProxyType(clean_axes))


@dataclass(frozen=True, slots=True)
class FontAxis:
    """One variable-font axis range in design coordinates."""

    tag: str
    minimum: float
    default: float
    maximum: float

    def contains(self, value: float) -> bool:
        """Return whether *value* is representable by this axis."""
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True, slots=True)
class FontFace:
    """A provider's advertised face; it is not a composition handle."""

    family: str
    style: str
    weight: int
    stretch: int
    provider: str
    source: str
    locator: str
    status: FontStatus
    face_index: int = 0
    axes: tuple[FontAxis, ...] = ()

    def __post_init__(self) -> None:
        if not self.family.strip():
            raise ValueError("font face family must not be empty")
        if self.style not in _STYLES:
            raise ValueError(f"invalid font face style: {self.style!r}")
        if self.face_index < 0:
            raise ValueError("font face index cannot be negative")

    def supports(self, query: FontQuery) -> bool:
        """Return whether the face can represent *query* without synthesis."""
        if self.family.casefold() != query.family.casefold() or self.style != query.style:
            return False
        axes = {axis.tag: axis for axis in self.axes}
        weight_ok = self.weight == query.weight or (
            "wght" in axes and axes["wght"].contains(float(query.weight))
        )
        stretch_ok = self.stretch == query.stretch or (
            "wdth" in axes and axes["wdth"].contains(float(query.stretch))
        )
        return weight_ok and stretch_ok and all(
            tag in axes and axes[tag].contains(float(value))
            for tag, value in query.axes.items()
        )


@dataclass(frozen=True, slots=True)
class FontAsset:
    """Exact font and optional license bytes returned by a provider."""

    data: bytes
    filename: str
    source: str
    license_data: bytes | None = None
    license_name: str | None = None

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("font asset data must not be empty")
        if Path(self.filename).name != self.filename:
            raise ValueError("font asset filename must be a basename")


@dataclass(frozen=True, slots=True)
class FontHandle:
    """A content-addressed, locally readable face allowed in composition."""

    family: str
    style: str
    weight: int
    stretch: int
    sha256: str
    path: Path
    source: str
    provider: str
    face_index: int = 0
    axes: tuple[FontAxis, ...] = ()
    variations: Mapping[str, float] = field(default_factory=dict)
    license_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "variations",
            MappingProxyType(
                {str(tag): float(value) for tag, value in self.variations.items()}
            ),
        )

    def verify(self) -> bool:
        """Verify that the local bytes still match the pinned digest."""
        try:
            data = self.path.read_bytes()
        except OSError as exc:
            raise IntegrityError(f"font bytes are not readable: {self.path}") from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != self.sha256:
            raise IntegrityError(
                f"font sha256 mismatch for {self.path}: expected {self.sha256}, got {actual}"
            )
        return True

    def as_face(self) -> FontFace:
        """Return the catalog view of this ready handle."""
        return FontFace(
            family=self.family,
            style=self.style,
            weight=self.weight,
            stretch=self.stretch,
            provider="store",
            source=self.source,
            locator=str(self.path),
            status=FontStatus.READY,
            face_index=self.face_index,
            axes=self.axes,
        )


@dataclass(frozen=True, slots=True)
class ShapedGlyph:
    """One positioned glyph in CSS-pixel units."""

    glyph_id: int
    cluster: int
    advance_x: float
    advance_y: float
    offset_x: float
    offset_y: float


@dataclass(frozen=True, slots=True)
class ShapedRun:
    """Exact HarfBuzz shaping output for a pinned font handle."""

    text: str
    font_sha256: str
    size_px: float
    advance_x: float
    advance_y: float
    ascent: float
    descent: float
    line_gap: float
    missing_glyphs: int
    glyphs: tuple[ShapedGlyph, ...]
