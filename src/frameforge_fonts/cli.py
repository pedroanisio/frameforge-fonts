"""The ``ff-fonts`` composition-time font workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .closure import export_closure, import_closure
from .models import FontError, FontFace, FontHandle, FontQuery, ShapedRun
from .providers import (
    DirectoryFontProvider,
    DockerFontProvider,
    FontProvider,
    GoogleFontsRepositoryProvider,
    HostFontProvider,
)
from .registry import FontCatalog
from .shaping import shape_text
from .store import FontStore


def _query(args: argparse.Namespace) -> FontQuery:
    return FontQuery(
        family=args.family,
        weight=args.weight,
        style=args.style,
        stretch=args.stretch,
    )


def _providers(args: argparse.Namespace) -> list[FontProvider]:
    providers: list[FontProvider] = []
    for index, directory in enumerate(args.font_dir):
        providers.append(DirectoryFontProvider(directory, name=f"directory-{index + 1}"))
    google_root = args.google_fonts_root or os.environ.get("FRAMEFORGE_GOOGLE_FONTS_ROOT")
    if google_root:
        providers.append(GoogleFontsRepositoryProvider(google_root))
    docker_image = args.docker_image or os.environ.get("FRAMEFORGE_FONTS_DOCKER_IMAGE")
    if docker_image:
        providers.append(DockerFontProvider(docker_image))
    if not args.no_host:
        providers.append(HostFontProvider())
    return providers


def _catalog(args: argparse.Namespace) -> FontCatalog:
    return FontCatalog(FontStore(args.store), _providers(args))


def _face_json(face: FontFace) -> dict[str, Any]:
    return {
        "axes": [
            {
                "default": axis.default,
                "maximum": axis.maximum,
                "minimum": axis.minimum,
                "tag": axis.tag,
            }
            for axis in face.axes
        ],
        "family": face.family,
        "locator": face.locator,
        "provider": face.provider,
        "source": face.source,
        "status": face.status.value,
        "stretch": face.stretch,
        "style": face.style,
        "weight": face.weight,
    }


def _handle_json(handle: FontHandle) -> dict[str, Any]:
    return {
        "family": handle.family,
        "path": str(handle.path),
        "provider": handle.provider,
        "sha256": handle.sha256,
        "source": handle.source,
        "status": "ready",
        "stretch": handle.stretch,
        "style": handle.style,
        "variations": dict(handle.variations),
        "weight": handle.weight,
    }


def _run_json(run: ShapedRun) -> dict[str, Any]:
    return {
        "advance_x": run.advance_x,
        "advance_y": run.advance_y,
        "ascent": run.ascent,
        "descent": run.descent,
        "font_sha256": run.font_sha256,
        "glyphs": [
            {
                "advance_x": glyph.advance_x,
                "advance_y": glyph.advance_y,
                "cluster": glyph.cluster,
                "glyph_id": glyph.glyph_id,
                "offset_x": glyph.offset_x,
                "offset_y": glyph.offset_y,
            }
            for glyph in run.glyphs
        ],
        "line_gap": run.line_gap,
        "missing_glyphs": run.missing_glyphs,
        "size_px": run.size_px,
        "text": run.text,
    }


def _cmd_list(args: argparse.Namespace) -> int:
    faces = _catalog(args).list_faces()
    if args.json:
        print(
            json.dumps(
                {
                    "faces": [_face_json(face) for face in faces],
                    "families": len({face.family.casefold() for face in faces}),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(f"{len({face.family.casefold() for face in faces})} families, {len(faces)} faces")
    for face in faces:
        print(
            f"{face.family}\t{face.style}\t{face.weight}\t{face.status.value}\t{face.source}"
        )
    return 0


def _cmd_ensure(args: argparse.Namespace) -> int:
    handle = _catalog(args).ensure(_query(args))
    print(json.dumps(_handle_json(handle), indent=2, sort_keys=True))
    return 0


def _cmd_measure(args: argparse.Namespace) -> int:
    handle = _catalog(args).ensure(_query(args))
    run = shape_text(handle, args.text, size_px=args.size)
    print(json.dumps(_run_json(run), indent=2, sort_keys=True))
    return 0


def _cmd_closure(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    handles = [
        catalog.ensure(
            FontQuery(
                family=family,
                weight=args.weight,
                style=args.style,
                stretch=args.stretch,
            )
        )
        for family in args.families
    ]
    output = export_closure(handles, args.out, generated_from=args.generated_from)
    print(output)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    handles = import_closure(args.pack, FontStore(args.store))
    print(
        json.dumps(
            {"faces": [_handle_json(handle) for handle in handles], "imported": len(handles)},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    store = catalog.store
    report: dict[str, Any] = {
        "providers": [
            {"available": provider.available(), "name": provider.name}
            for provider in catalog.providers
        ],
        "store": {
            "path": str(store.root),
            "writable": os.access(store.root, os.W_OK),
        },
        "version": __version__,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"store: {report['store']['path']} (writable={report['store']['writable']})")
        for provider in report["providers"]:
            print(f"{provider['name']}: available={provider['available']}")
    return 0


def _add_face_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("family")
    parser.add_argument("--weight", type=int, default=400)
    parser.add_argument("--style", choices=("normal", "italic", "oblique"), default="normal")
    parser.add_argument("--stretch", type=int, default=100)


def build_parser() -> argparse.ArgumentParser:
    """Build the public argument parser."""
    parser = argparse.ArgumentParser(
        prog="ff-fonts",
        description="Know, provision, and pin fonts before FrameForge document composition.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--store", type=Path, help="user-space content-addressed store")
    parser.add_argument(
        "--font-dir",
        action="append",
        default=[],
        type=Path,
        help="project/user font directory (repeatable)",
    )
    parser.add_argument("--google-fonts-root", type=Path, help="local google/fonts clone")
    parser.add_argument("--docker-image", help="optional font-rich Docker image")
    parser.add_argument("--no-host", action="store_true", help="do not catalog host fonts")
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list selectable exact faces")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=_cmd_list)

    ensure_parser = commands.add_parser("ensure", help="materialize a face before composition")
    _add_face_options(ensure_parser)
    ensure_parser.set_defaults(handler=_cmd_ensure)

    measure_parser = commands.add_parser("measure", help="shape and measure with exact bytes")
    _add_face_options(measure_parser)
    measure_parser.add_argument("text")
    measure_parser.add_argument("--size", type=float, default=16.0)
    measure_parser.set_defaults(handler=_cmd_measure)

    closure_parser = commands.add_parser("closure", help="export a portable .fp font closure")
    closure_parser.add_argument("families", nargs="+")
    closure_parser.add_argument("--out", type=Path, required=True)
    closure_parser.add_argument("--generated-from")
    closure_parser.add_argument("--weight", type=int, default=400)
    closure_parser.add_argument(
        "--style", choices=("normal", "italic", "oblique"), default="normal"
    )
    closure_parser.add_argument("--stretch", type=int, default=100)
    closure_parser.set_defaults(handler=_cmd_closure)

    import_parser = commands.add_parser("import", help="verify and install a .fp closure")
    import_parser.add_argument("pack", type=Path)
    import_parser.set_defaults(handler=_cmd_import)

    doctor_parser = commands.add_parser("doctor", help="report provider availability")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=_cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run ``ff-fonts`` and return a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (FontError, OSError, ValueError) as exc:
        print(f"ff-fonts: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
