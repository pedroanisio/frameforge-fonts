"""Composition-time font discovery, provisioning, shaping, and closure."""

from .closure import export_closure, import_closure
from .models import (
    FontAsset,
    FontAxis,
    FontError,
    FontFace,
    FontHandle,
    FontQuery,
    FontStatus,
    IntegrityError,
    ProviderError,
    ShapedGlyph,
    ShapedRun,
    ShapingUnavailableError,
    UnavailableFontError,
)
from .registry import FontCatalog
from .shaping import shape_text
from .store import FontStore, default_store_root

__all__ = [
    "FontAsset",
    "FontAxis",
    "FontCatalog",
    "FontError",
    "FontFace",
    "FontHandle",
    "FontQuery",
    "FontStatus",
    "FontStore",
    "IntegrityError",
    "ProviderError",
    "ShapedGlyph",
    "ShapedRun",
    "ShapingUnavailableError",
    "UnavailableFontError",
    "default_store_root",
    "export_closure",
    "import_closure",
    "shape_text",
]

__version__ = "0.1.0"
