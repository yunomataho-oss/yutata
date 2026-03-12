"""
PDF text extractor using PyMuPDF (fitz).
Extracts all text from every page including annotations and form fields.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .base_extractor import BaseExtractor, ExtractionResult


# Drawing-number pattern heuristics (common in JIS / ISO title blocks)
# e.g.  DRW-12345,  12345-A,  A3-0012,  図番 12345
_DRW_PATTERNS = [
    re.compile(r"\b(?:DRW|DWG|DRAW|図番|図面番号|DRAWING\s*NO\.?)[:\s\-]*([A-Z0-9\-_/]+)", re.IGNORECASE),
    re.compile(r"\b([A-Z]{1,4}[-_]?\d{3,10}(?:[-_][A-Z0-9]+)?)\b"),
    re.compile(r"\b(\d{4,10}[-_][A-Z0-9]{1,6})\b"),
]


def _extract_drawing_numbers(texts: List[str]) -> List[str]:
    found: set[str] = set()
    for text in texts:
        for pat in _DRW_PATTERNS:
            for m in pat.finditer(text):
                candidate = m.group(1) if pat.groups else m.group(0)
                candidate = candidate.strip()
                if len(candidate) >= 3:
                    found.add(candidate.upper())
    return sorted(found)


class PdfExtractor(BaseExtractor):
    """Extract text from PDF files using PyMuPDF."""

    def extract(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="pdf")

        if not self._safe_read(file_path):
            result.error = f"File not found or not readable: {file_path}"
            return result

        try:
            import fitz  # PyMuPDF
        except ImportError:
            result.error = "PyMuPDF is not installed. Run: pip install PyMuPDF"
            return result

        try:
            doc = fitz.open(file_path)
            texts: List[str] = []

            for page_num in range(len(doc)):
                page = doc[page_num]

                # 1) Standard text extraction (preserves layout)
                page_text = page.get_text("text")
                if page_text.strip():
                    texts.append(page_text)

                # 2) Extract from annotations (sticky notes, comments, etc.)
                for annot in page.annots():
                    info = annot.info
                    if info.get("content"):
                        texts.append(info["content"])
                    if info.get("title"):
                        texts.append(info["title"])

                # 3) Try to extract text blocks individually for better coverage
                blocks = page.get_text("blocks")
                for block in blocks:
                    if len(block) >= 5 and isinstance(block[4], str):
                        blk_text = block[4].strip()
                        if blk_text and blk_text not in texts:
                            texts.append(blk_text)

            # PDF metadata
            meta = doc.metadata or {}
            result.raw_metadata = meta
            if meta.get("title"):
                result.title = meta["title"]

            doc.close()

            result.texts = [t for t in texts if t.strip()]
            result.drawing_numbers = _extract_drawing_numbers(result.texts)

        except Exception as exc:
            result.error = f"PDF extraction failed: {exc}"

        return result
