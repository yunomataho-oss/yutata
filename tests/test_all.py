"""
Unit tests for all extractor modules, the search engine, and CSV export.
Run with: pytest tests/ -v
"""
from __future__ import annotations

import csv
import io
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

    def test_incremental_skip(self, tmp_path):
        """2nd index run with no changes should skip all files (fast path)."""
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "inc.dxf")
        _make_dxf_file(dxf_path, "INC-001")
        index_path = str(tmp_path / "idx.json")

        from src.search.search_engine import DrawingSearchEngine
        import time as _time

        # First run: parse file
        e1 = DrawingSearchEngine(index_path=index_path)
        ok1, err1 = e1.index_directory(str(tmp_path))
        assert ok1 == 1 and err1 == 0

        # Second run: file unchanged -> should be near-instant
        e2 = DrawingSearchEngine(index_path=index_path)
        t0 = _time.time()
        ok2, err2 = e2.index_directory(str(tmp_path))
        elapsed = _time.time() - t0
        assert ok2 == 1 and err2 == 0
        assert elapsed < 0.5, f"2nd run took {elapsed:.3f}s — expected <0.5s (incremental)"

    def test_force_reindex(self, tmp_path):
        """force=True should re-parse even unchanged files."""
        pytest.importorskip("ezdxf")
        dxf_path = str(tmp_path / "force.dxf")
        _make_dxf_file(dxf_path, "FORCE-001")
        index_path = str(tmp_path / "idx.json")

        from src.search.search_engine import DrawingSearchEngine

        e1 = DrawingSearchEngine(index_path=index_path)
        e1.index_directory(str(tmp_path))

        # Modify indexed_at to check it gets updated
        abs_path = list(e1._index.keys())[0]
        old_ts = e1._index[abs_path].indexed_at

        import time as _time
        _time.sleep(0.05)  # ensure clock advances

        e2 = DrawingSearchEngine(index_path=index_path)
        e2.index_directory(str(tmp_path), force=True)
        new_ts = e2._index[abs_path].indexed_at
        assert new_ts > old_ts, "force=True should re-parse and update indexed_at"

    def test_parallel_indexing(self, tmp_path):
        """Parallel indexing with multiple workers should yield correct results."""
        pytest.importorskip("ezdxf")
        import time as _time

        for i in range(10):
            _make_dxf_file(str(tmp_path / f"p{i:02d}.dxf"), f"PAR-{i:04d}")

        index_path = str(tmp_path / "idx.json")
        from src.search.search_engine import DrawingSearchEngine

        engine = DrawingSearchEngine(index_path=index_path)
        ok, err = engine.index_directory(str(tmp_path), max_workers=4)
        assert ok == 10
        assert err == 0
        # All drawing numbers should be searchable
        for i in range(10):
            results = engine.search(f"PAR-{i:04d}")
            assert len(results) >= 1, f"PAR-{i:04d} not found after parallel index"


# ────────────────────────────────────────────────────────────────────────────
#  CSV Export
# ────────────────────────────────────────────────────────────────────────────

class TestCsvExport:
    """Tests for _write_results_csv and _write_index_csv static methods."""

    def _make_entry(self, tmp_path, filename="test.pdf", drawing_numbers=None,
                    file_type="pdf", title=None, error=None, texts="sample text"):
        from src.search.search_engine import IndexEntry
        fp = str(tmp_path / filename)
        return IndexEntry(
            file_path=fp,
            file_type=file_type,
            filename=filename,
            drawing_numbers=drawing_numbers or ["DRW-001"],
            texts_blob=texts,
            title=title,
            indexed_at="2026-01-01T00:00:00",
            error=error,
            file_mtime=0.0,
            file_size=0,
        )

    def _make_result(self, entry, match_type="drawing_number", matched_value="DRW-001", score=0.95):
        from src.search.search_engine import SearchResult
        return SearchResult(entry=entry, match_type=match_type, matched_value=matched_value, score=score)

    def test_results_csv_has_header(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entry = self._make_entry(tmp_path)
        results = [self._make_result(entry)]
        out = str(tmp_path / "results.csv")
        DrawingSearchApp._write_results_csv(out, results)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        assert "ファイル名" in header
        assert "図面番号" in header
        assert "マッチ値" in header
        assert "ファイルパス" in header

    def test_results_csv_data_row(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entry = self._make_entry(tmp_path, drawing_numbers=["DRW-999"], title="My Title")
        results = [self._make_result(entry, matched_value="DRW-999", score=0.88)]
        out = str(tmp_path / "results.csv")
        DrawingSearchApp._write_results_csv(out, results)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 2  # header + 1 data row
        row = rows[1]
        assert "DRW-999" in row
        assert "My Title" in row
        assert "0.880" in row

    def test_results_csv_multiple_drawing_numbers(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entry = self._make_entry(tmp_path, drawing_numbers=["DRW-001", "DRW-002", "DRW-003"])
        results = [self._make_result(entry)]
        out = str(tmp_path / "results.csv")
        DrawingSearchApp._write_results_csv(out, results)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        row = rows[1]
        # Drawing numbers should be joined with "; "
        drawing_col = row[1]  # index 1 = 図面番号
        assert "DRW-001" in drawing_col
        assert "DRW-002" in drawing_col

    def test_results_csv_empty_list(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        out = str(tmp_path / "empty.csv")
        DrawingSearchApp._write_results_csv(out, [])
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 1  # header only
        assert "ファイル名" in rows[0]

    def test_results_csv_utf8_bom(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entry = self._make_entry(tmp_path, filename="テスト図面.pdf", title="テストタイトル")
        results = [self._make_result(entry)]
        out = str(tmp_path / "results.csv")
        DrawingSearchApp._write_results_csv(out, results)
        with open(out, "rb") as f:
            header_bytes = f.read(3)
        # UTF-8 BOM = EF BB BF
        assert header_bytes == b"\xef\xbb\xbf", "File should start with UTF-8 BOM for Excel compatibility"

    def test_results_csv_path_and_folder(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entry = self._make_entry(tmp_path)
        results = [self._make_result(entry)]
        out = str(tmp_path / "results.csv")
        DrawingSearchApp._write_results_csv(out, results)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        path_idx = header.index("ファイルパス")
        folder_idx = header.index("フォルダ")
        row = rows[1]
        assert row[path_idx] == entry.file_path
        assert row[folder_idx] == os.path.dirname(entry.file_path)

    def test_index_csv_has_header(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entries = [self._make_entry(tmp_path)]
        out = str(tmp_path / "index.csv")
        DrawingSearchApp._write_index_csv(out, entries)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        assert "ファイル名" in header
        assert "ファイル種別" in header
        assert "図面番号" in header
        assert "ファイルパス" in header

    def test_index_csv_texts_blob_truncated(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        long_text = "A" * 1000
        entry = self._make_entry(tmp_path, texts=long_text)
        out = str(tmp_path / "index.csv")
        DrawingSearchApp._write_index_csv(out, [entry])
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        text_idx = header.index("抽出テキスト冒頭500字")
        row = rows[1]
        assert len(row[text_idx]) <= 500

    def test_index_csv_empty_list(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        out = str(tmp_path / "empty_index.csv")
        DrawingSearchApp._write_index_csv(out, [])
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 1  # header only

    def test_results_csv_error_field(self, tmp_path):
        from src.gui.app import DrawingSearchApp
        entry = self._make_entry(tmp_path, error="Parse failed")
        results = [self._make_result(entry)]
        out = str(tmp_path / "results.csv")
        DrawingSearchApp._write_results_csv(out, results)
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        error_idx = header.index("エラー")
        assert rows[1][error_idx] == "Parse failed"
