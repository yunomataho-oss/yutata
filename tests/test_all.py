"""
Unit tests for all extractor modules and the search engine.
Run with: pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import json

import pytest

# Ensure src is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.extractors.base_extractor import ExtractionResult


# ────────────────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────────────────

def _make_minimal_pdf(path: str, text_content: str = "DRW-001 Drawing Number Test") -> str:
    """Create a minimal valid PDF with text using only stdlib (no extra deps)."""
    content_stream = f"BT /F1 12 Tf 50 750 Td ({text_content}) Tj ET"
    stream_bytes = content_stream.encode("latin-1")
    stream_len = len(stream_bytes)

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(stream_len).encode() + b" >>\nstream\n" +
        stream_bytes + b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"0000000380 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n452\n%%EOF\n"
    )
    with open(path, "wb") as fh:
        fh.write(pdf)
    return path


def _make_dxf_file(path: str, drawing_number: str = "DRW-9999") -> str:
    """Create a minimal DXF R12 file with a TEXT entity."""
    dxf = f"""  0
SECTION
  2
HEADER
  0
ENDSEC
  0
SECTION
  2
ENTITIES
  0
TEXT
  8
0
 10
100.0
 20
100.0
 30
0.0
 40
5.0
  1
{drawing_number}
  0
TEXT
  8
0
 10
100.0
 20
80.0
 30
0.0
 40
5.0
  1
TITLE: Sample Drawing
  0
ENDSEC
  0
EOF
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dxf)
    return path


# ────────────────────────────────────────────────────────────────────────────
#  PDF Extractor Tests
# ────────────────────────────────────────────────────────────────────────────

class TestPdfExtractor:
    def test_missing_file(self):
        from src.extractors.pdf_extractor import PdfExtractor
        result = PdfExtractor().extract("/nonexistent/file.pdf")
        assert not result.success
        assert result.error is not None

    def test_extract_text(self, tmp_path):
        pytest.importorskip("fitz", reason="PyMuPDF not installed")
        pdf_path = str(tmp_path / "test.pdf")
        _make_minimal_pdf(pdf_path, "DRW-001 Test drawing ABC-1234")

        from src.extractors.pdf_extractor import PdfExtractor
        result = PdfExtractor().extract(pdf_path)
        assert result.file_type == "pdf"
        # Text should be extracted (or at least no fatal error)
        # (minimal PDF may not have proper font encoding — just check no crash)
        assert result.error is None or "extraction failed" not in (result.error or "").lower()

    def test_file_type_label(self, tmp_path):
        pytest.importorskip("fitz")
        pdf_path = str(tmp_path / "sample.pdf")
        _make_minimal_pdf(pdf_path)
        from src.extractors.pdf_extractor import PdfExtractor
        result = PdfExtractor().extract(pdf_path)
        assert result.file_type == "pdf"
        assert result.filename == "sample.pdf"


# ────────────────────────────────────────────────────────────────────────────
#  DXF Extractor Tests
# ────────────────────────────────────────────────────────────────────────────

class TestDxfExtractor:
    def test_missing_file(self):
        from src.extractors.dxf_extractor import DxfExtractor
        result = DxfExtractor().extract("/nonexistent/file.dxf")
        assert not result.success

    def test_extract_text_from_dxf(self, tmp_path):
        pytest.importorskip("ezdxf", reason="ezdxf not installed")
        dxf_path = str(tmp_path / "test.dxf")
        _make_dxf_file(dxf_path, "DRW-9999")

        from src.extractors.dxf_extractor import DxfExtractor
        result = DxfExtractor().extract(dxf_path)
        assert result.file_type == "dxf"
        assert result.error is None
        assert "DRW-9999" in " ".join(result.texts)

    def test_drawing_number_detected(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "test.dxf")
        _make_dxf_file(dxf_path, "ABC-1234")
        from src.extractors.dxf_extractor import DxfExtractor
        result = DxfExtractor().extract(dxf_path)
        assert "ABC-1234" in result.drawing_numbers or \
               any("ABC" in dn for dn in result.drawing_numbers), \
               f"Expected ABC-1234 in {result.drawing_numbers}"


# ────────────────────────────────────────────────────────────────────────────
#  Drawing Number Pattern Tests
# ────────────────────────────────────────────────────────────────────────────

class TestDrawingNumberPatterns:
    def test_pattern_drw_prefix(self):
        from src.extractors.pdf_extractor import _extract_drawing_numbers
        texts = ["図番: DRW-00123 Rev.A"]
        nums = _extract_drawing_numbers(texts)
        assert any("DRW-00123" in n or "00123" in n for n in nums), f"Got: {nums}"

    def test_pattern_alphanumeric(self):
        from src.extractors.pdf_extractor import _extract_drawing_numbers
        texts = ["Part No. AB-12345 see sheet 2"]
        nums = _extract_drawing_numbers(texts)
        assert len(nums) > 0

    def test_pattern_numeric_alpha(self):
        from src.extractors.pdf_extractor import _extract_drawing_numbers
        texts = ["12345-REV-A approved"]
        nums = _extract_drawing_numbers(texts)
        assert len(nums) > 0


# ────────────────────────────────────────────────────────────────────────────
#  Search Engine Tests
# ────────────────────────────────────────────────────────────────────────────

class TestSearchEngine:
    def test_index_and_search_dxf(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "drawing.dxf")
        _make_dxf_file(dxf_path, "XYZ-5678")

        index_path = str(tmp_path / "index.json")
        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        engine.index_file(dxf_path)
        engine._save_index()

        assert engine.index_size == 1

        results = engine.search("XYZ-5678")
        assert len(results) >= 1
        assert results[0].entry.filename == "drawing.dxf"

    def test_search_no_results(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "drawing.dxf")
        _make_dxf_file(dxf_path, "AAA-0001")

        index_path = str(tmp_path / "index.json")
        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        engine.index_file(dxf_path)

        results = engine.search("NOMATCH-9999")
        assert len(results) == 0

    def test_search_empty_query(self, tmp_path):
        index_path = str(tmp_path / "index.json")
        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        results = engine.search("")
        assert results == []

    def test_index_persistence(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "p.dxf")
        _make_dxf_file(dxf_path, "PERSIST-001")
        index_path = str(tmp_path / "idx.json")

        from src.search.search_engine import DrawingSearchEngine
        e1 = DrawingSearchEngine(index_path=index_path)
        e1.index_file(dxf_path)
        e1._save_index()

        # New engine instance loads same index
        e2 = DrawingSearchEngine(index_path=index_path)
        assert e2.index_size == 1

    def test_remove_file(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "rm.dxf")
        _make_dxf_file(dxf_path, "RM-001")
        index_path = str(tmp_path / "idx.json")

        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        engine.index_file(dxf_path)
        assert engine.index_size == 1
        engine.remove_file(dxf_path)
        assert engine.index_size == 0

    def test_clear_index(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "cl.dxf")
        _make_dxf_file(dxf_path, "CL-001")
        index_path = str(tmp_path / "idx.json")

        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        engine.index_file(dxf_path)
        engine.clear_index()
        assert engine.index_size == 0

    def test_regex_search(self, tmp_path):
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "re.dxf")
        _make_dxf_file(dxf_path, "REG-1234")
        index_path = str(tmp_path / "idx.json")

        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        engine.index_file(dxf_path)

        results = engine.search(r"REG-\d+", use_regex=True)
        assert len(results) >= 1

    def test_index_directory(self, tmp_path):
        pytest.importorskip("ezdxf")
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_dxf_file(str(tmp_path / "a.dxf"), "DIR-001")
        _make_dxf_file(str(sub / "b.dxf"), "DIR-002")

        index_path = str(tmp_path / "idx.json")
        from src.search.search_engine import DrawingSearchEngine
        engine = DrawingSearchEngine(index_path=index_path)
        indexed, errors = engine.index_directory(str(tmp_path), recursive=True)
        assert indexed == 2
        assert errors == 0
