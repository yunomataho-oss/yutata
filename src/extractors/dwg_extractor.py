"""
DWG extractor.
Strategy:
  1. Try ODA File Converter (free CLI) to convert DWG → DXF, then use DxfExtractor.
  2. Fallback: try LibreCAD / teighafileconverter if available.
  3. Fallback: binary scan for ASCII strings (best-effort, no proper parsing).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional

from .base_extractor import BaseExtractor, ExtractionResult
from .dxf_extractor import DxfExtractor

# ODA File Converter executable names on various platforms
_ODA_CANDIDATES = [
    "ODAFileConverter",
    "ODAFileConverter.exe",
    "/usr/bin/ODAFileConverter",
    "/opt/ODAFileConverter/ODAFileConverter",
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
    r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
]


def _find_oda() -> Optional[str]:
    for cand in _ODA_CANDIDATES:
        if shutil.which(cand):
            return cand
        if os.path.isfile(cand):
            return cand
    return None


def _binary_text_scan(file_path: str) -> List[str]:
    """Last-resort: extract printable ASCII runs ≥4 chars from binary."""
    texts: List[str] = []
    try:
        with open(file_path, "rb") as fh:
            data = fh.read()
        # Find runs of printable ASCII (32-126) at least 4 bytes
        pattern = re.compile(rb'[ -~]{4,}')
        for m in pattern.finditer(data):
            try:
                decoded = m.group().decode("ascii", errors="ignore").strip()
                if decoded:
                    texts.append(decoded)
            except Exception:
                pass
    except Exception:
        pass
    return texts


class DwgExtractor(BaseExtractor):
    """
    Extract text from DWG files.
    Requires ODA File Converter for proper parsing.
    """

    def extract(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="dwg")

        if not self._safe_read(file_path):
            result.error = f"File not found or not readable: {file_path}"
            return result

        oda_path = _find_oda()

        if oda_path:
            result = self._extract_via_oda(file_path, oda_path)
        else:
            result = self._extract_binary_fallback(file_path)

        return result

    def _extract_via_oda(self, file_path: str, oda_path: str) -> ExtractionResult:
        """Convert DWG → DXF using ODA, then parse with ezdxf."""
        result = ExtractionResult(file_path=file_path, file_type="dwg")

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                # ODA syntax: ODAFileConverter <in_dir> <out_dir> <version> <type> <recurse> <audit>
                in_dir = os.path.dirname(os.path.abspath(file_path))
                filename = os.path.basename(file_path)

                # Copy single file to isolated temp input dir
                tmp_in = os.path.join(tmpdir, "input")
                tmp_out = os.path.join(tmpdir, "output")
                os.makedirs(tmp_in, exist_ok=True)
                os.makedirs(tmp_out, exist_ok=True)

                import shutil as sh
                sh.copy2(file_path, tmp_in)

                cmd = [
                    oda_path,
                    tmp_in, tmp_out,
                    "ACAD2018", "DXF", "0", "1"
                ]
                subprocess.run(cmd, timeout=60, check=False,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)

                # Find output DXF
                dxf_files = [f for f in os.listdir(tmp_out) if f.lower().endswith(".dxf")]
                if dxf_files:
                    dxf_path = os.path.join(tmp_out, dxf_files[0])
                    dxf_result = DxfExtractor(self.config).extract(dxf_path)
                    # Copy results back, keeping original file_path
                    result.texts = dxf_result.texts
                    result.drawing_numbers = dxf_result.drawing_numbers
                    result.title = dxf_result.title
                    result.raw_metadata = dxf_result.raw_metadata
                    result.raw_metadata["converted_via"] = "ODA File Converter"
                    if dxf_result.error:
                        result.error = dxf_result.error
                else:
                    # Fallback to binary scan
                    fallback = self._extract_binary_fallback(file_path)
                    result.texts = fallback.texts
                    result.drawing_numbers = fallback.drawing_numbers
                    result.raw_metadata["converted_via"] = "binary_fallback (ODA produced no output)"

            except Exception as exc:
                result.error = f"ODA conversion failed: {exc}"
                fallback = self._extract_binary_fallback(file_path)
                result.texts = fallback.texts
                result.drawing_numbers = fallback.drawing_numbers

        return result

    def _extract_binary_fallback(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="dwg")
        texts = _binary_text_scan(file_path)
        result.texts = texts
        result.raw_metadata = {"converted_via": "binary_scan_fallback"}

        from .dxf_extractor import _extract_drawing_numbers
        result.drawing_numbers = _extract_drawing_numbers(texts)
        result.error = (
            "ODA File Converter not found. Used binary text scan (limited accuracy). "
            "Install ODA File Converter for full DWG support: https://www.opendesign.com/guestfiles/oda_file_converter"
        )
        return result
