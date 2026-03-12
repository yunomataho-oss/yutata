"""
SLDDRW (SolidWorks Drawing) extractor.
Strategy:
  1. If running on Windows with SolidWorks installed → use Win32COM API (most reliable).
  2. Fallback: treat SLDDRW as ZIP (modern SW files) and parse embedded XML for text.
  3. Fallback: binary string scan.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from typing import List, Optional
from xml.etree import ElementTree as ET

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


def _binary_text_scan(file_path: str) -> List[str]:
    texts: List[str] = []
    try:
        with open(file_path, "rb") as fh:
            data = fh.read()
        pattern = re.compile(rb'[ -~]{4,}')
        for m in pattern.finditer(data):
            try:
                decoded = m.group().decode("ascii", errors="ignore").strip()
                if decoded:
                    texts.append(decoded)
            except Exception:
                pass
        # Also try UTF-16 LE (common in SW files)
        try:
            text_utf16 = data.decode("utf-16-le", errors="ignore")
            # Extract printable runs
            for run in re.findall(r'[\u0020-\u007E\u3000-\u9FFF\uFF00-\uFFEF]{3,}', text_utf16):
                texts.append(run.strip())
        except Exception:
            pass
    except Exception:
        pass
    return texts


def _extract_from_zip(file_path: str) -> List[str]:
    """
    Modern SolidWorks files (.slddrw 2015+) are OLE / CFB containers,
    but some versions embed XML that can be parsed.
    Try treating as ZIP first.
    """
    texts: List[str] = []
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            for name in zf.namelist():
                if any(name.lower().endswith(ext) for ext in (".xml", ".txt", ".html")):
                    try:
                        content = zf.read(name).decode("utf-8", errors="ignore")
                        # Strip XML tags
                        clean = re.sub(r'<[^>]+>', ' ', content)
                        texts.append(clean)
                    except Exception:
                        pass
    except zipfile.BadZipFile:
        pass
    return texts


def _extract_via_com(file_path: str) -> Optional[List[str]]:
    """Use SolidWorks COM API (Windows only, requires SW installation)."""
    if sys.platform != "win32":
        return None
    try:
        import win32com.client as win32  # type: ignore
        sw = win32.Dispatch("SldWorks.Application")
        sw.Visible = False

        abs_path = os.path.abspath(file_path)
        errors = win32.VARIANT(win32.VT_BYREF | win32.VT_I4, 0)
        warnings = win32.VARIANT(win32.VT_BYREF | win32.VT_I4, 0)
        doc = sw.OpenDoc6(
            abs_path,
            3,   # swDocDRAWING
            1,   # swOpenDocOptions_Silent
            "",
            errors,
            warnings,
        )
        if doc is None:
            return None

        texts: List[str] = []

        # Iterate all drawing views for notes / annotations
        drawing = doc.GetFirstSheet()
        while drawing:
            ann = drawing.GetFirstAnnotation2()
            while ann:
                try:
                    note = ann.GetSpecificAnnotation()
                    if hasattr(note, "GetText"):
                        t = note.GetText()
                        if t:
                            texts.append(t)
                except Exception:
                    pass
                ann = ann.GetNext2()
            drawing = drawing.GetNext()

        # Custom properties
        try:
            cfg_mgr = doc.ConfigurationManager
            active_cfg = cfg_mgr.ActiveConfiguration
            cust_prop = active_cfg.CustomPropertyManager
            names = cust_prop.GetNames()
            if names:
                for prop_name in names:
                    val_in = ""
                    val_out = ""
                    _, val_in, val_out, _ = cust_prop.Get5(prop_name, False)
                    if val_out:
                        texts.append(f"{prop_name}: {val_out}")
        except Exception:
            pass

        sw.CloseDoc(abs_path)
        return texts

    except Exception:
        return None


class SlddrwExtractor(BaseExtractor):
    """
    Extract text from SolidWorks Drawing files (.SLDDRW).
    """

    def extract(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="slddrw")

        if not self._safe_read(file_path):
            result.error = f"File not found or not readable: {file_path}"
            return result

        texts: List[str] = []
        method_used = []

        # Strategy 1: COM API (Windows + SW installed)
        com_texts = _extract_via_com(file_path)
        if com_texts is not None:
            texts.extend(com_texts)
            method_used.append("SolidWorks COM API")

        # Strategy 2: ZIP / XML embedded content
        zip_texts = _extract_from_zip(file_path)
        if zip_texts:
            texts.extend(zip_texts)
            method_used.append("embedded XML")

        # Strategy 3: Binary scan (always run as supplement)
        bin_texts = _binary_text_scan(file_path)
        texts.extend(bin_texts)
        method_used.append("binary scan")

        result.texts = list(dict.fromkeys(t.strip() for t in texts if t.strip()))
        result.drawing_numbers = _extract_drawing_numbers(result.texts)
        result.raw_metadata = {
            "extraction_methods": method_used,
        }

        if not com_texts and sys.platform == "win32":
            result.error = (
                "SolidWorks COM API unavailable. Used binary scan (limited accuracy). "
                "For best results, run this application on a machine with SolidWorks installed."
            )
        elif sys.platform != "win32":
            result.error = (
                "SolidWorks COM API requires Windows + SolidWorks. "
                "Used binary/XML scan (limited accuracy)."
            )

        return result
