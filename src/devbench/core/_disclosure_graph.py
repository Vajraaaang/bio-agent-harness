"""Traverse bounded disclosure ancestry without conferring access rights."""

from collections.abc import Callable, Iterable, Iterator
from typing import Any

MAX_SOURCE_ANCESTRY = 512


def walk_source_ancestry(
    roots: Iterable[str],
    load_source: Callable[[str], dict[str, Any]],
    *,
    limit_error: Exception,
    peer_parents: Callable[[str], Iterable[str]] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield source nodes in existing stack order, deduplicated by identity.

    The source loader owns custody enforcement. Current-use callers can follow
    byte-equivalent peers while replay callers keep the original ancestry.
    """
    pending = list(roots)
    seen: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= MAX_SOURCE_ANCESTRY:
            raise limit_error
        source = load_source(identity)
        seen.add(identity)
        yield identity, source
        pending.extend(source["parents"])
        if peer_parents is not None:
            pending.extend(peer_parents(source["content_sha256"]))
