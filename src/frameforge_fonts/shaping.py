"""Exact HarfBuzz shaping and measurement over verified font handles."""

from __future__ import annotations

from collections.abc import Mapping

from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from .models import (
    FontHandle,
    ShapedGlyph,
    ShapedRun,
    ShapingUnavailableError,
)


def shape_text(
    handle: FontHandle,
    text: str,
    *,
    size_px: float,
    direction: str | None = None,
    language: str | None = None,
    script: str | None = None,
    features: Mapping[str, int | bool] | None = None,
    variations: Mapping[str, float] | None = None,
) -> ShapedRun:
    """Shape *text* with exact pinned bytes and return pixel-unit metrics.

    No family-name or host fallback is accepted here. A caller must provide a
    verified :class:`FontHandle` created by :meth:`FontCatalog.ensure`.
    """
    if not isinstance(handle, FontHandle):
        raise TypeError("shape_text requires a ready FontHandle, not a family name")
    if not size_px > 0:
        raise ValueError("size_px must be positive")
    handle.verify()
    try:
        import uharfbuzz as hb  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency/package failure
        raise ShapingUnavailableError(
            "uharfbuzz is required for exact composition-time shaping"
        ) from exc

    data = handle.path.read_bytes()
    face = hb.Face(data, handle.face_index)
    units_per_em = int(face.upem)
    if units_per_em <= 0:
        raise ValueError(f"font has invalid units-per-em: {handle.path}")
    font = hb.Font(face)
    font.scale = (units_per_em, units_per_em)
    hb.ot_font_set_funcs(font)
    selected_variations = dict(handle.variations)
    selected_variations.update(variations or {})
    if selected_variations:
        font.set_variations(selected_variations)

    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    if direction is not None:
        buffer.direction = direction
    if language is not None:
        buffer.language = language
    if script is not None:
        buffer.script = script
    hb.shape(font, buffer, dict(features or {}))

    scale = float(size_px) / units_per_em
    glyphs = tuple(
        ShapedGlyph(
            glyph_id=int(info.codepoint),
            cluster=int(info.cluster),
            advance_x=float(position.x_advance) * scale,
            advance_y=float(position.y_advance) * scale,
            offset_x=float(position.x_offset) * scale,
            offset_y=float(position.y_offset) * scale,
        )
        for info, position in zip(buffer.glyph_infos, buffer.glyph_positions, strict=True)
    )
    with TTFont(handle.path, fontNumber=handle.face_index, lazy=True) as ttfont:
        hhea = ttfont["hhea"]
        ascent = float(hhea.ascent) * scale
        descent = abs(float(hhea.descent) * scale)
        line_gap = float(hhea.lineGap) * scale
    return ShapedRun(
        text=text,
        font_sha256=handle.sha256,
        size_px=float(size_px),
        advance_x=sum(glyph.advance_x for glyph in glyphs),
        advance_y=sum(glyph.advance_y for glyph in glyphs),
        ascent=ascent,
        descent=descent,
        line_gap=line_gap,
        missing_glyphs=sum(glyph.glyph_id == 0 for glyph in glyphs),
        glyphs=glyphs,
    )
