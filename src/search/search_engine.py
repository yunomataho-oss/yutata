"""
Search engine: index drawing files and perform full-text + drawing-number search.
Persists index as JSON for fast re-search without re-parsing.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from ..extractors.base_extractor import ExtractionResult
from ..extractors.pdf_extractor import PdfExtractor
from ..extractors.dxf_extractor import DxfExtractor
from ..extractors.dwg_extractor import DwgExtractor
from ..extractors.slddrw_extractor import SlddrwExtractor

# Supported extensions → extractor class
_EXTRACTOR_MAP = {
    ".pdf":     PdfExtractor,
    ".dxf":     DxfExtractor,
    ".dwg":     DwgExtractor,
    ".slddrw":  SlddrwExtractor,
}

DEFAULT_INDEX_PATH = os.path.join(
    os.path.expanduser("~"), ".drawing_search", "index.json"
)


@dataclass
class IndexEntry:
    file_path: str
    file_type: str
    filename: str
    drawing_numbers: List[str]
    texts_blob: str          # all texts joined — for full-text search
    title: Optional[str]
    indexed_at: float        # Unix timestamp
    error: Optional[str]


@dataclass
class SearchResult:
    entry: IndexEntry
    match_type: str          # 'drawing_number' | 'full_text' | 'filename'
    matched_value: str       # the exact string that matched
    score: float             # relevance score 0–1


class DrawingSearchEngine:
    """
    Manages an index of drawing files and provides search functionality.
    """

    def __init__(self, index_path: str = DEFAULT_INDEX_PATH, config: Optional[dict] = None):
        self.index_path = index_path
        self.config = config or {}
        self._index: Dict[str, IndexEntry] = {}   # key = absolute file_path
        self._load_index()

    # ------------------------------------------------------------------ #
    #  Indexing                                                            #
    # ------------------------------------------------------------------ #

    def index_directory(
        self,
        directory: str,
        recursive: bool = True,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[int, int]:
        """
        Scan *directory* and index all supported drawing files.

        progress_callback(file_path, current, total) is called for each file.
        Returns (indexed_count, error_count).
        """
        files = list(self._discover_files(directory, recursive))
        total = len(files)
        indexed = 0
        errors = 0

        for i, fpath in enumerate(files):
            if progress_callback:
                progress_callback(fpath, i + 1, total)
            try:
                self.index_file(fpath)
                indexed += 1
            except Exception:
                errors += 1

        self._save_index()
        return indexed, errors

    def index_file(self, file_path: str) -> IndexEntry:
        """Index a single file. Raises on fatal error."""
        abs_path = os.path.abspath(file_path)
        ext = Path(abs_path).suffix.lower()

        extractor_cls = _EXTRACTOR_MAP.get(ext)
        if extractor_cls is None:
            raise ValueError(f"Unsupported file type: {ext}")

        extractor = extractor_cls(self.config)
        result: ExtractionResult = extractor.extract(abs_path)

        entry = IndexEntry(
            file_path=abs_path,
            file_type=result.file_type,
            filename=result.filename,
            drawing_numbers=result.drawing_numbers,
            texts_blob=" ".join(result.texts),
            title=result.title,
            indexed_at=time.time(),
            error=result.error,
        )
        self._index[abs_path] = entry
        return entry

    def remove_file(self, file_path: str) -> bool:
        abs_path = os.path.abspath(file_path)
        if abs_path in self._index:
            del self._index[abs_path]
            self._save_index()
            return True
        return False

    def clear_index(self) -> None:
        self._index.clear()
        self._save_index()

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        match_type: str = "all",   # 'all' | 'drawing_number' | 'full_text' | 'filename'
        case_sensitive: bool = False,
        use_regex: bool = False,
    ) -> List[SearchResult]:
        """
        Search the index for *query*.

        Returns a list of SearchResult sorted by score descending.
        """
        if not query.strip():
            return []

        results: List[SearchResult] = []
        q = query.strip() if case_sensitive else query.strip().lower()

        for entry in self._index.values():
            hit = self._match_entry(entry, q, query, match_type, case_sensitive, use_regex)
            if hit:
                results.append(hit)

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _match_entry(
        self,
        entry: IndexEntry,
        q_lower: str,
        q_orig: str,
        match_type: str,
        case_sensitive: bool,
        use_regex: bool,
    ) -> Optional[SearchResult]:
        best: Optional[SearchResult] = None

        def _text(s: str) -> str:
            return s if case_sensitive else s.lower()

        def _match(s: str) -> bool:
            if use_regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                return bool(re.search(q_orig, s, flags))
            return q_lower in _text(s)

        # --- drawing number match (highest priority) ---
        if match_type in ("all", "drawing_number"):
            for dn in entry.drawing_numbers:
                if _match(dn):
                    score = 1.0 if _text(dn) == q_lower else 0.9
                    if best is None or score > best.score:
                        best = SearchResult(entry, "drawing_number", dn, score)

        # --- filename match ---
        if match_type in ("all", "filename"):
            if _match(entry.filename):
                score = 0.8
                if best is None or score > best.score:
                    best = SearchResult(entry, "filename", entry.filename, score)

        # --- full text match ---
        if match_type in ("all", "full_text"):
            if _match(entry.texts_blob):
                # Score based on frequency
                count = len(re.findall(re.escape(q_lower), _text(entry.texts_blob)))
                score = min(0.7, 0.4 + count * 0.05)
                if best is None or score > best.score:
                    # Extract snippet around match
                    snippet = self._get_snippet(entry.texts_blob, q_orig, q_lower, case_sensitive)
                    best = SearchResult(entry, "full_text", snippet, score)

        return best

    @staticmethod
    def _get_snippet(text: str, q_orig: str, q_lower: str, case_sensitive: bool, context: int = 60) -> str:
        """Return a ≤120-char snippet around the first match."""
        t = text if case_sensitive else text.lower()
        idx = t.find(q_lower)
        if idx < 0:
            return text[:120]
        start = max(0, idx - context)
        end = min(len(text), idx + len(q_lower) + context)
        snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
        return snippet

    # ------------------------------------------------------------------ #
    #  Index persistence                                                   #
    # ------------------------------------------------------------------ #

    def _load_index(self) -> None:
        if os.path.isfile(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for k, v in raw.items():
                    self._index[k] = IndexEntry(**v)
            except Exception:
                self._index = {}

    @staticmethod
    def _sanitize(obj):
        """Recursively remove surrogate / non-encodable characters from a structure."""
        if isinstance(obj, str):
            return obj.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        if isinstance(obj, list):
            return [DrawingSearchEngine._sanitize(i) for i in obj]
        if isinstance(obj, dict):
            return {DrawingSearchEngine._sanitize(k): DrawingSearchEngine._sanitize(v) for k, v in obj.items()}
        return obj

    def _save_index(self) -> None:
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        data = self._sanitize({k: asdict(v) for k, v in self._index.items()})
        with open(self.index_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    #  Utilities                                                           #
    # ------------------------------------------------------------------ #

    @property
    def index_size(self) -> int:
        return len(self._index)

    @property
    def indexed_files(self) -> List[IndexEntry]:
        return list(self._index.values())

    @staticmethod
    def _discover_files(directory: str, recursive: bool) -> Iterator[str]:
        for root, dirs, files in os.walk(directory):
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext in _EXTRACTOR_MAP:
                    yield os.path.join(root, fname)
            if not recursive:
                break
