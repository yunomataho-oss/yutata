"""
DXF text extractor using ezdxf.
Extracts TEXT, MTEXT, ATTRIB, ATTDEF entities from all layouts and blocks.
"""
from __future__ import annotations

import re
from typing import List

from .base_extractor import BaseExtractor, ExtractionResult

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


def _collect_texts_from_layout(layout) -> List[str]:
    """Collect all text entities from a layout (modelspace / paperspace / block)."""
    texts: List[str] = []

    for entity in layout:
        etype = entity.dxftype()

        try:
            if etype in ("TEXT", "MTEXT"):
                val = entity.dxf.text if etype == "TEXT" else entity.text
                if val:
                    # Strip MTEXT formatting codes {\\P \\S \\f ...}
                    clean = re.sub(r'\\[A-Za-z;]|[{}]|\\[0-9;]+', '', str(val))
                    texts.append(clean.strip())

            elif etype == "ATTRIB":
                val = entity.dxf.get("text", "")
                if val:
                    texts.append(str(val).strip())

            elif etype == "ATTDEF":
                val = entity.dxf.get("text", "")
                tag = entity.dxf.get("tag", "")
                if val:
                    texts.append(str(val).strip())
                if tag:
                    texts.append(str(tag).strip())

            elif etype == "INSERT":
                # Block reference — collect attribs attached to it
                try:
                    for attrib in entity.attribs:
                        val = attrib.dxf.get("text", "")
                        if val:
                            texts.append(str(val).strip())
                except Exception:
                    pass

            elif etype == "DIMENSION":
                try:
                    val = entity.dxf.get("text", "")
                    if val:
                        texts.append(str(val).strip())
                except Exception:
                    pass

        except Exception:
            pass

    return [t for t in texts if t]


class DxfExtractor(BaseExtractor):
    """Extract text from DXF files using ezdxf."""

    def extract(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="dxf")

        if not self._safe_read(file_path):
            result.error = f"File not found or not readable: {file_path}"
            return result

        try:
            import ezdxf
        except ImportError:
            result.error = "ezdxf is not installed. Run: pip install ezdxf"
            return result

        try:
            doc = ezdxf.readfile(file_path)
            texts: List[str] = []

            # Modelspace
            texts.extend(_collect_texts_from_layout(doc.modelspace()))

            # All paper space layouts
            for layout in doc.layouts:
                if layout.name != "Model":
                    texts.extend(_collect_texts_from_layout(layout))

            # All block definitions (title block, standard parts)
            for block in doc.blocks:
                texts.extend(_collect_texts_from_layout(block))

            # Custom properties / summary info
            summary = doc.header.get("$LASTSAVEDBY", "")
            if summary:
                texts.append(str(summary))

            result.texts = list(dict.fromkeys(t for t in texts if t.strip()))
            result.drawing_numbers = _extract_drawing_numbers(result.texts)

            # Try to detect title from filename
            result.raw_metadata = {
                "dxf_version": doc.dxfversion,
                "filename": file_path,
            }

        except Exception as exc:
            result.error = f"DXF extraction failed: {exc}"

        return result
