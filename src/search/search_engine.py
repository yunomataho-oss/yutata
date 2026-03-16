"""
Search engine: index drawing files and perform full-text + drawing-number search.
Persists index as JSON for fast re-search without re-parsing.

Performance features:
  - Incremental indexing (skip files whose mtime/size haven't changed)
  - Parallel extraction with ThreadPoolExecutor
  - Batch JSON save (every SAVE_BATCH_SIZE files + final)
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# How many files to process before writing index to disk (mid-run checkpoint)
SAVE_BATCH_SIZE = 50

# Default number of parallel worker threads
DEFAULT_MAX_WORKERS = min(8, (os.cpu_count() or 2) * 2)


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
    # --- incremental indexing metadata ---
    file_mtime: float = 0.0  # os.path.getmtime at index time
    file_size: int = 0       # os.path.getsize  at index time


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
        force: bool = False,
        max_workers: Optional[int] = None,
    ) -> Tuple[int, int]:
        """
        Scan *directory* and index all supported drawing files.

        Incremental: files whose mtime and size haven't changed since the last
        index are skipped automatically (unless *force=True*).

        Parallel: files are extracted in parallel using a thread pool.
        I/O-bound work (file reading) benefits well from threading.

        progress_callback(file_path, current, total) is called for each file
        as it completes (from the main thread via a result queue).

        Returns (indexed_count, error_count).
        """
        files = list(self._discover_files(directory, recursive))
        total = len(files)

        # --- Partition: skip unchanged, process changed/new ---
        to_process: List[str] = []
        skipped = 0
        for fpath in files:
            if not force and self._is_up_to_date(fpath):
                skipped += 1
            else:
                to_process.append(fpath)

        workers = max_workers if max_workers is not None else DEFAULT_MAX_WORKERS
        indexed = 0
        errors = 0
        done_count = skipped  # already-skipped count toward progress

        if to_process:
            indexed, errors = self._index_parallel(
                to_process,
                total=total,
                already_done=skipped,
                progress_callback=progress_callback,
                max_workers=workers,
            )
        else:
            # All files up-to-date: fire progress callbacks for display
            if progress_callback:
                for i, fpath in enumerate(files, 1):
                    progress_callback(fpath, i, total)

        self._save_index()
        return indexed + skipped, errors

    def _is_up_to_date(self, file_path: str) -> bool:
        """Return True if the file is already indexed with the same mtime+size."""
        abs_path = os.path.abspath(file_path)
        entry = self._index.get(abs_path)
        if entry is None:
            return False
        try:
            stat = os.stat(abs_path)
            return (
                abs(stat.st_mtime - entry.file_mtime) < 1.0
                and stat.st_size == entry.file_size
            )
        except OSError:
            return False

    def _index_parallel(
        self,
        files: List[str],
        total: int,
        already_done: int,
        progress_callback: Optional[Callable[[str, int, int], None]],
        max_workers: int,
    ) -> Tuple[int, int]:
        """Extract files in parallel; return (indexed, errors)."""
        indexed = 0
        errors = 0
        completed = already_done

        # Use a lock to protect shared _index writes from multiple threads
        import threading
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_path = {
                pool.submit(self._extract_single, fpath): fpath
                for fpath in files
            }

            batch_dirty = 0  # count since last mid-run save

            for future in as_completed(future_to_path):
                fpath = future_to_path[future]
                completed += 1

                try:
                    entry = future.result()
                    with lock:
                        self._index[entry.file_path] = entry
                    if entry.error:
                        errors += 1
                    else:
                        indexed += 1
                except Exception:
                    errors += 1

                if progress_callback:
                    progress_callback(fpath, completed, total)

                batch_dirty += 1
                if batch_dirty >= SAVE_BATCH_SIZE:
                    with lock:
                        self._save_index()
                    batch_dirty = 0

        return indexed, errors

    def _extract_single(self, file_path: str) -> IndexEntry:
        """Extract one file and return an IndexEntry. Thread-safe (no shared state)."""
        abs_path = os.path.abspath(file_path)
        ext = Path(abs_path).suffix.lower()
        extractor_cls = _EXTRACTOR_MAP.get(ext)
        if extractor_cls is None:
            raise ValueError(f"Unsupported file type: {ext}")

        try:
            stat = os.stat(abs_path)
            file_mtime = stat.st_mtime
            file_size = stat.st_size
        except OSError:
            file_mtime = 0.0
            file_size = 0

        extractor = extractor_cls(self.config)
        result: ExtractionResult = extractor.extract(abs_path)

        return IndexEntry(
            file_path=abs_path,
            file_type=result.file_type,
            filename=result.filename,
            drawing_numbers=result.drawing_numbers,
            # DXF/DWG はセクション形式 blob を優先（レイアウト判定に使用）
            # その他はフラットなスペース結合
            texts_blob=(result.texts_blob_sections
                        if result.texts_blob_sections
                        else " ".join(result.texts)),
            title=result.title,
            indexed_at=time.time(),
            error=result.error,
            file_mtime=file_mtime,
            file_size=file_size,
        )

    def index_file(self, file_path: str, force: bool = False) -> IndexEntry:
        """Index a single file. Raises on fatal error.

        If *force=False* and the file is already up-to-date in the index,
        the cached entry is returned without re-parsing.
        """
        if not force and self._is_up_to_date(file_path):
            return self._index[os.path.abspath(file_path)]

        entry = self._extract_single(file_path)
        self._index[entry.file_path] = entry
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
                    # Back-compat: add mtime/size fields if missing
                    v.setdefault("file_mtime", 0.0)
                    v.setdefault("file_size", 0)
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
