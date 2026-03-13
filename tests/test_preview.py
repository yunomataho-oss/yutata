"""
Preview engine tests.
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image


def _make_minimal_pdf(path: str, text: str = "DRW-001 Test") -> str:
    content = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode("latin-1")
    pdf = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(len(content)).encode() + b" >>\nstream\n" +
        content + b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n" + b"0000000000 65535 f \n" * 6 +
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n1\n%%EOF\n"
    )
    with open(path, "wb") as f:
        f.write(pdf)
    return path


def _make_dxf(path: str, drawing_number: str = "DRW-001") -> str:
    content = f"""  0
SECTION
  2
ENTITIES
  0
LINE
  8
0
 10
0.0
 20
0.0
 30
0.0
 11
100.0
 21
100.0
 31
0.0
  0
CIRCLE
  8
0
 10
50.0
 20
50.0
 30
0.0
 40
30.0
  0
TEXT
  8
0
 10
10.0
 20
10.0
 30
0.0
 40
5.0
  1
{drawing_number}
  0
ENDSEC
  0
EOF
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestPreviewEngine:
    def test_import(self):
        from src.preview.preview_engine import (
            get_preview, preview_pdf, preview_dxf,
            _make_placeholder, _make_error_image
        )
        assert callable(get_preview)

    def test_placeholder_image(self):
        from src.preview.preview_engine import _make_placeholder
        img = _make_placeholder("テストメッセージ")
        assert isinstance(img, Image.Image)
        assert img.width > 0 and img.height > 0

    def test_error_image(self):
        from src.preview.preview_engine import _make_error_image
        img = _make_error_image("エラータイトル", "詳細メッセージ")
        assert isinstance(img, Image.Image)

    def test_missing_file(self):
        from src.preview.preview_engine import get_preview
        pages = get_preview("/nonexistent/file.pdf")
        assert len(pages) == 1
        assert isinstance(pages[0], Image.Image)

    def test_unsupported_ext(self, tmp_path):
        from src.preview.preview_engine import get_preview
        p = str(tmp_path / "file.xyz")
        open(p, "w").close()
        pages = get_preview(p)
        assert len(pages) >= 1

    def test_pdf_preview(self, tmp_path):
        pytest.importorskip("fitz")
        path  = str(tmp_path / "test.pdf")
        _make_minimal_pdf(path, "DRW-001")
        from src.preview.preview_engine import preview_pdf
        pages = preview_pdf(path)
        assert len(pages) >= 1
        assert isinstance(pages[0], Image.Image)
        assert pages[0].width > 0

    def test_dxf_preview(self, tmp_path):
        pytest.importorskip("ezdxf")
        path = str(tmp_path / "test.dxf")
        _make_dxf(path, "ABC-1234")
        from src.preview.preview_engine import preview_dxf
        pages = preview_dxf(path)
        assert len(pages) >= 1
        assert isinstance(pages[0], Image.Image)

    def test_dxf_multiple_layouts(self, tmp_path):
        pytest.importorskip("ezdxf")
        import ezdxf
        path = str(tmp_path / "multi.dxf")
        doc  = ezdxf.new()
        doc.layouts.new("Sheet1")
        doc.saveas(path)
        from src.preview.preview_engine import preview_dxf
        pages = preview_dxf(path)
        # Should have at least Model space
        assert len(pages) >= 1

    def test_dwg_no_oda(self, tmp_path):
        """DWG without ODA returns an error image (not a crash)."""
        path = str(tmp_path / "test.dwg")
        # Write fake DWG (just binary header)
        with open(path, "wb") as f:
            f.write(b"AC1032" + b"\x00" * 100)
        from src.preview.preview_engine import preview_dwg
        pages = preview_dwg(path, config={})
        assert len(pages) >= 1
        assert isinstance(pages[0], Image.Image)

    def test_get_preview_pdf(self, tmp_path):
        pytest.importorskip("fitz")
        path = str(tmp_path / "t.pdf")
        _make_minimal_pdf(path)
        from src.preview.preview_engine import get_preview
        pages = get_preview(path)
        assert len(pages) >= 1

    def test_get_preview_dxf(self, tmp_path):
        pytest.importorskip("ezdxf")
        path = str(tmp_path / "t.dxf")
        _make_dxf(path)
        from src.preview.preview_engine import get_preview
        pages = get_preview(path)
        assert len(pages) >= 1

    def test_max_pages_limit(self, tmp_path):
        pytest.importorskip("ezdxf")
        import ezdxf
        path = str(tmp_path / "many.dxf")
        doc  = ezdxf.new()
        for i in range(5):
            doc.layouts.new(f"Sheet{i}")
        doc.saveas(path)
        from src.preview.preview_engine import get_preview
        pages = get_preview(path, max_pages=3)
        assert len(pages) <= 3

    # ── バグ修正検証テスト ──────────────────────────────────────────────

    def test_dxf_white_background(self, tmp_path):
        """DXF プレビューは白背景 (Bright% > 50%) で返される。
        旧コードではデフォルト黒背景 (#212830) のまま描画され
        ほぼ真っ黒な画像が出力されていた。"""
        pytest.importorskip("ezdxf")
        import numpy as np
        from tests.create_samples import make_dxf as _make_dxf_full
        path = str(tmp_path / "bg_test.dxf")
        _make_dxf_full(path, "BG-001", "Background Test")
        from src.preview.preview_engine import get_preview
        pages = get_preview(path)
        assert len(pages) >= 1
        img = pages[0]
        assert img.mode == "RGB", f"Expected RGB, got {img.mode}"
        arr = np.array(img)
        bright_pct = (arr.mean(axis=2) > 200).mean() * 100
        dark_pct   = (arr.mean(axis=2) < 50).mean()  * 100
        assert bright_pct > 50, (
            f"DXF preview is too dark (bright={bright_pct:.1f}%, dark={dark_pct:.1f}%). "
            "White background fix may have regressed."
        )
        assert dark_pct < 30, (
            f"DXF preview has too many dark pixels (dark={dark_pct:.1f}%). "
            "White background fix may have regressed."
        )

    def test_dxf_output_is_rgb(self, tmp_path):
        """DXF プレビュー出力は RGB モードでなければならない (RGBA 不可)。
        旧コードでは matplotlib が RGBA で出力し、アルファチャンネルが
        Tkinter PhotoImage に渡されて表示がバグる問題があった。"""
        pytest.importorskip("ezdxf")
        path = str(tmp_path / "mode_test.dxf")
        _make_dxf(path, "MODE-001")
        from src.preview.preview_engine import get_preview
        pages = get_preview(path)
        for i, p in enumerate(pages):
            assert p.mode == "RGB", (
                f"Page {i} has mode '{p.mode}', expected 'RGB'. "
                "RGBA to RGB conversion fix may have regressed."
            )

    def test_dxf_empty_layout_skipped(self, tmp_path):
        """エンティティが 0 件のレイアウトはプレビューに含まれない。
        旧コードでは空白ページが出力されていた。"""
        pytest.importorskip("ezdxf")
        import ezdxf as _ezdxf
        path = str(tmp_path / "empty_layout.dxf")
        doc = _ezdxf.new()
        # Model には LINE を追加
        msp = doc.modelspace()
        msp.add_line((0, 0), (100, 100))
        # Sheet1 は空のまま
        doc.layouts.new("Sheet1")
        doc.saveas(path)
        from src.preview.preview_engine import preview_dxf
        pages = preview_dxf(path)
        # Sheet1 (空) はスキップされ Model だけ
        assert len(pages) == 1, (
            f"Expected 1 page (empty layout skipped), got {len(pages)}."
        )
        assert pages[0].info.get("layout_name") == "Model"

    def test_pdf_output_is_rgb(self, tmp_path):
        """PDF プレビュー出力も RGB モードであること。"""
        pytest.importorskip("fitz")
        path = str(tmp_path / "pdf_mode.pdf")
        _make_minimal_pdf(path)
        from src.preview.preview_engine import get_preview
        pages = get_preview(path)
        for i, p in enumerate(pages):
            assert p.mode == "RGB", f"PDF page {i} mode={p.mode}, expected RGB"
