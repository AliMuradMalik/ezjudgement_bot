"""Map OpenAI citation filenames to the original PDFs on disk.

Corpus layout (confirmed):

    <SOURCES_DIR>/
      CLC/<YEAR>/headnote/CLC<YEAR>K<NN>.pdf
      CLC/<YEAR>/judgment/CLC<YEAR>K<NN>.pdf
      SCMR/<YEAR>/headnote/SCMR<YEAR>S<NNNN>.pdf
      SCMR/<YEAR>/judgment/SCMR<YEAR>S<NNNN>.pdf

Each case has two PDFs sharing the SAME filename: a ``judgment/`` (full text)
and a ``headnote/`` (summary). A citation carries only the bare filename, so we
derive the folder from the filename (series + year) and offer both documents.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from app.config import get_settings

# app/sources.py -> app/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Public URL prefix under which PDFs are served (see app/routers/sources.py).
URL_PREFIX = "/sources"

# The two document variants every case has.
KINDS = ("judgment", "headnote")

# CLC2013K219 / SCMR2013S140 -> capture series (CLC|SCMR) and the 4-digit year.
_NAME_RE = re.compile(r"^(CLC|SCMR)(\d{4})", re.IGNORECASE)


def sources_root() -> Path:
    """Absolute path to the configured PDF folder (may not exist yet)."""
    configured = Path(get_settings().sources_dir)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured


def _stem_and_name(filename: str) -> tuple[str, str]:
    """('CLC2013K219', 'CLC2013K219.pdf') from any spelling of the filename."""
    base = Path(filename).name  # drop any directory part the model may include
    if base.lower().endswith(".pdf"):
        return base[:-4], base
    return base, f"{base}.pdf"


def _derived_path(root: Path, filename: str, kind: str) -> Path | None:
    """Build <root>/<SERIES>/<YEAR>/<kind>/<name>.pdf straight from the name."""
    stem, name = _stem_and_name(filename)
    match = _NAME_RE.match(stem)
    if not match:
        return None
    series, year = match.group(1).upper(), match.group(2)
    return root / series / year / kind / name


# --- recursive fallback index: basename(lower) -> {kind: path} ----------------
# Only built (lazily) if the derived path misses, so a well-formed corpus never
# pays for a full-tree walk at request time.
_INDEX: dict[str, dict[str, Path]] | None = None


def _build_index(root: Path) -> dict[str, dict[str, Path]]:
    index: dict[str, dict[str, Path]] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            kind = path.parent.name.lower()
            if kind not in KINDS:
                kind = "other"
            index.setdefault(path.name.lower(), {}).setdefault(kind, path.resolve())
    return index


def refresh_index() -> dict[str, dict[str, Path]]:
    """Rebuild and cache the filename index. Returns it (basename -> {kind: path})."""
    global _INDEX
    root = sources_root()
    _INDEX = _build_index(root.resolve()) if root.is_dir() else {}
    return _INDEX


def _index() -> dict[str, dict[str, Path]]:
    global _INDEX
    if _INDEX is None:
        refresh_index()
    return _INDEX or {}


def resolve_source(filename: str | None, kind: str) -> Path | None:
    """Return the on-disk PDF for a citation filename + variant, or None.

    ``kind`` is ``"judgment"`` or ``"headnote"``. Tries the derived path first,
    then a recursive lookup by bare filename. Guards against path traversal.
    """
    if not filename or kind not in KINDS:
        return None

    root = sources_root()
    if not root.is_dir():
        return None
    root = root.resolve()

    # 1) fast path: derive the exact location from the filename.
    derived = _derived_path(root, filename, kind)
    if derived is not None:
        derived = derived.resolve()
        if derived.is_relative_to(root) and derived.is_file():
            return derived

    # 2) fallback: recursive lookup by bare filename within the right variant.
    _, name = _stem_and_name(filename)
    entry = _index().get(name.lower())
    if entry:
        hit = entry.get(kind)
        if hit is not None and hit.is_relative_to(root):
            return hit
    return None


def document_urls(filename: str | None) -> dict[str, str | None]:
    """Public URLs for a citation's PDFs.

    Returns ``{"judgment": url|None, "headnote": url|None}`` — a value is set
    only when that PDF actually exists on disk.
    """
    _, name = _stem_and_name(filename) if filename else ("", "")
    urls: dict[str, str | None] = {}
    for kind in KINDS:
        if filename and resolve_source(filename, kind) is not None:
            urls[kind] = f"{URL_PREFIX}/{kind}/{quote(name)}"
        else:
            urls[kind] = None
    return urls
