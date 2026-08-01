"""Composition-time catalog and exact materialization workflow."""

from __future__ import annotations

from dataclasses import replace

from .models import (
    FontFace,
    FontHandle,
    FontQuery,
    FontStatus,
    ProviderError,
    UnavailableFontError,
)
from .providers import FontProvider, font_asset_family
from .store import FontStore

_STATUS_ORDER = {
    FontStatus.READY: 0,
    FontStatus.FETCHABLE: 1,
    FontStatus.EXTERNAL: 2,
    FontStatus.UNAVAILABLE: 3,
}


class FontCatalog:
    """Answer what can be composed and turn exact choices into ready handles.

    Family strings never cross the composition boundary. Call :meth:`ensure`
    first and pass the resulting :class:`FontHandle` to shaping or rendering.
    """

    def __init__(self, store: FontStore, providers: list[FontProvider] | tuple[FontProvider, ...]) -> None:
        self.store = store
        self.providers = tuple(providers)
        names = [provider.name for provider in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("font provider names must be unique")

    def list_faces(self) -> tuple[FontFace, ...]:
        """Return ready and provisionable faces in stable catalog order."""
        faces = [handle.as_face() for handle in self.store.list_handles()]
        for provider in self.providers:
            if provider.available():
                faces.extend(provider.list_faces())
        unique: dict[tuple[str, str, int, int, str, str], FontFace] = {}
        for face in faces:
            key = (
                face.family.casefold(),
                face.style,
                face.weight,
                face.stretch,
                face.provider,
                face.locator,
            )
            unique[key] = face
        return tuple(
            sorted(
                unique.values(),
                key=lambda face: (
                    face.family.casefold(),
                    face.style,
                    face.weight,
                    face.stretch,
                    _STATUS_ORDER[face.status],
                    face.provider,
                    face.locator,
                ),
            )
        )

    def families(self) -> tuple[str, ...]:
        """Return exact selectable family names."""
        by_folded: dict[str, str] = {}
        for face in self.list_faces():
            by_folded.setdefault(face.family.casefold(), face.family)
        return tuple(by_folded[key] for key in sorted(by_folded))

    def resolve(self, query: FontQuery) -> FontFace:
        """Resolve *query* exactly; fuzzy family and synthetic faces are forbidden."""
        ready = self.store.find(query)
        if ready is not None:
            return ready.as_face()
        candidates = [face for face in self.list_faces() if face.supports(query)]
        candidates = [face for face in candidates if face.provider != "store"]
        if not candidates:
            raise UnavailableFontError(
                f"font unavailable: {query.family!r} style={query.style} "
                f"weight={query.weight} stretch={query.stretch}"
            )
        provider_order = {provider.name: index for index, provider in enumerate(self.providers)}
        candidates.sort(
            key=lambda face: (
                _STATUS_ORDER[face.status],
                provider_order.get(face.provider, len(provider_order)),
                face.locator,
            )
        )
        return candidates[0]

    def status(self, query: FontQuery) -> FontStatus:
        """Return the current composition availability of *query*."""
        if self.store.find(query) is not None:
            return FontStatus.READY
        try:
            return self.resolve(query).status
        except UnavailableFontError:
            return FontStatus.UNAVAILABLE

    def ensure(self, query: FontQuery) -> FontHandle:
        """Materialize an exact face in the local store before composition."""
        ready = self.store.find(query)
        if ready is not None:
            return ready
        face = self.resolve(query)
        provider = next(
            (candidate for candidate in self.providers if candidate.name == face.provider),
            None,
        )
        if provider is None:
            raise UnavailableFontError(
                f"font unavailable: provider {face.provider!r} is not configured"
            )
        asset = provider.fetch(face)
        actual_family = font_asset_family(asset, face.face_index)
        if actual_family.casefold() != face.family.casefold():
            raise ProviderError(
                f"provider {provider.name!r} advertised {face.family!r} "
                f"but returned {actual_family!r}"
            )
        if asset.source != face.source:
            raise ProviderError(
                f"provider {provider.name!r} changed source identity for {face.family!r}"
            )
        selected_face = replace(
            face,
            weight=query.weight,
            stretch=query.stretch,
            status=FontStatus.READY,
        )
        variations = dict(query.axes)
        axes = {axis.tag: axis for axis in face.axes}
        if face.weight != query.weight and "wght" in axes:
            variations["wght"] = float(query.weight)
        if face.stretch != query.stretch and "wdth" in axes:
            variations["wdth"] = float(query.stretch)
        return self.store.put(selected_face, asset, variations=variations)

    def require_ready(self, query: FontQuery) -> FontHandle:
        """Return an existing ready handle without provisioning anything."""
        handle = self.store.find(query)
        if handle is None:
            status = self.status(query)
            raise UnavailableFontError(
                f"font is {status.value}, not ready: {query.family!r}; call ensure() first"
            )
        return handle
