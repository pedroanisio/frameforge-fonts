# FrameForge Fonts

`frameforge-fonts` makes fonts a composition-time dependency instead of an
accidental property of the render host.

The hard rule is simple: document geometry may be created only from a verified
`FontHandle`. A family name is catalog input; it is never accepted by the
shaper. That removes silent fallback from the measurement path.

This package is the composition-time font companion to
[FrameForge](https://github.com/pedroanisio/frameforge), which owns the document
model, authoring SDK, validation, and rendering pipeline. The repositories
currently meet through FrameForge's `fp_version: 1` closure contract; FrameForge
does not yet import or delegate SDK measurement to this package.

## The workflow

1. Ask the catalog which exact faces are available.
2. Choose only a listed family, style, weight, and width.
3. Call `ensure()` to materialize the exact bytes in a user-space,
   content-addressed store.
4. Shape and measure with the returned handle while composing the document.
5. Export the handles actually used as the document's small, portable `.fp`
   font closure.

Catalog states have operational meaning:

| Status | Meaning |
|---|---|
| `ready` | Exact bytes are locally readable and can be materialized immediately. |
| `fetchable` | A configured provider can retrieve the bytes without rendering. |
| `external` | An optional external runtime, such as Docker, can export the bytes. |
| `unavailable` | The exact requested face cannot be used for fidelity composition. |

There is no fuzzy family substitution and no estimate mode. HarfBuzz shapes the
exact SHA-256-pinned bytes, including script shaping, kerning, OpenType features,
and variable-font coordinates.

## Install

```bash
uv tool install .
ff-fonts doctor
```

The store defaults to `$XDG_DATA_HOME/frameforge-fonts` or
`~/.local/share/frameforge-fonts`. Set `FRAMEFORGE_FONTS_HOME` or pass
`--store` to scope it elsewhere. This requires neither root nor Docker.

## Google Fonts companion mode

Use a shallow clone as the large shared catalog. The clone is not copied into
each document; only selected faces enter the store and only used faces enter a
document closure.

```bash
git clone --depth 1 --single-branch https://github.com/google/fonts.git

ff-fonts \
  --google-fonts-root ./fonts \
  --no-host \
  list

ff-fonts \
  --google-fonts-root ./fonts \
  --no-host \
  ensure "Source Serif 4" --weight 400
```

`FRAMEFORGE_GOOGLE_FONTS_ROOT=./fonts` avoids repeating the option. The provider
reads `ofl/`, `apache/`, and `ufl/`, derives face metadata from the actual font
tables, and carries the adjacent license file into closures.

Project-specific fonts can be added without installation:

```bash
ff-fonts --font-dir ./assets/fonts --no-host list
```

Host fonts are one provider, enabled by default when `fc-list` exists. They are
not privileged over project fonts, the user-space store, or a Google Fonts
clone. Use `--no-host` for a deliberately host-independent composition catalog.

## Python composition API

```python
from pathlib import Path

from frameforge_fonts import FontCatalog, FontQuery, FontStore, shape_text
from frameforge_fonts.providers import GoogleFontsRepositoryProvider

store = FontStore()
catalog = FontCatalog(
    store,
    [GoogleFontsRepositoryProvider(Path("fonts"))],
)

# Do this before authoring positioned text.
handle = catalog.ensure(FontQuery("Source Serif 4", weight=400))
metrics = shape_text(handle, "Measured before geometry", size_px=18)
print(metrics.advance_x)
```

`catalog.resolve()` answers whether an exact face is selectable.
`catalog.ensure()` is allowed to materialize it. `catalog.require_ready()` is the
strict boundary for code that must never perform provisioning during a
composition transaction.

## Portable document closure

```python
from frameforge_fonts import export_closure, import_closure

export_closure([handle], "book.fp", generated_from="book.fg.yaml")
import_closure("book.fp", FontStore("./isolated-store"))
```

The archive is deterministic and uses FrameForge's existing `fp_version: 1`
shape: `manifest.json`, exact `fonts/*` bytes, and SHA-256 hashes. It adds style,
weight, stretch, variable coordinates, and license members while retaining the
legacy `family`, `bold`, `resolved`, `file`, `sha256`, and `source` fields.
FrameForge can therefore consume the closure with its current `fg-font`
installer.

CLI equivalent:

```bash
ff-fonts --google-fonts-root ./fonts --no-host \
  closure "Source Serif 4" "Inter" --out book.fp --generated-from book.fg.yaml

ff-fonts --store ./render-fonts import book.fp
```

## Optional Docker provider

Docker remains a provider, not the architecture. If it is installed and the
caller has permission, the companion can enumerate a font-rich image and export
the selected bytes into the same user-space store:

```bash
ff-fonts --docker-image frameforge:latest --no-host list
```

The implementation invokes `docker` directly without a shell. A host without
Docker simply omits this provider and can use a project directory or Google
Fonts clone instead.

## Development

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest --cov=frameforge_fonts --cov-branch --cov-report=term-missing
uv build
```

The package is MIT licensed. Font files retain their own licenses; a closure
copies provider-supplied license text but does not change its terms.
