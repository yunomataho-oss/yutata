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
        from src.preview.preview_engine import get_preview, PageResult
        pages = get_preview("/nonexistent/file.pdf")
        assert len(pages) == 1
        # get_preview always returns PageResult objects
        assert isinstance(pages[0], PageResult)
        assert isinstance(pages[0].image, Image.Image)

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
        from src.preview.preview_engine import preview_pdf, PageResult
        pages = preview_pdf(path)
        assert len(pages) >= 1
        assert isinstance(pages[0], PageResult)
        assert pages[0].image.width > 0

    def test_dxf_preview(self, tmp_path):
        pytest.importorskip("ezdxf")
        path = str(tmp_path / "test.dxf")
        _make_dxf(path, "ABC-1234")
        from src.preview.preview_engine import preview_dxf, PageResult
        pages = preview_dxf(path)
        assert len(pages) >= 1
        assert isinstance(pages[0], PageResult)

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
        from src.preview.preview_engine import preview_dwg, PageResult
        pages = preview_dwg(path, config={})
        assert len(pages) >= 1
        assert isinstance(pages[0], PageResult)

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
        pr = pages[0]
        # pages[0] is PageResult; access .image for the PIL Image
        img = pr.image if hasattr(pr, 'image') else pr
        assert img.mode == "RGB", f"Expected RGB, got {img.mode}"
        arr = np.array(img)
        bright_pct = (arr.mean(axis=2) > 200).mean() * 100
        dark_pct   = (arr.mean(axis=2) < 50).mean()  * 100
        assert bright_pct > 50, (
            f"DXF preview is too dark (bright={bright_pct:.1f}%, dark={dark_pct:.1f}%). "
            "White background fix may have regressed."
        )
        assert dark_pct < 50, (
            f"DXF preview has too many dark pixels (dark={dark_pct:.1f}%). "
            "White background fix may have regressed. "
            "(Note: TextPolicy.REPLACE_RECT renders text as filled rectangles which adds dark pixels)"
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

    # ── ハイライト機能テスト (ハイライト機能削除済み → hit_boxes は常に空) ──

    def test_pdf_highlight_returns_pageresult(self, tmp_path):
        """highlight_text 指定時も PageResult を返す（ハイライト機能削除済みのため hit_boxes は空）。"""
        pytest.importorskip("fitz")
        from tests.create_samples import make_pdf as _make_pdf_full
        path = str(tmp_path / "hl.pdf")
        _make_pdf_full(path, "DRW-HL-001", "Highlight Test")
        from src.preview.preview_engine import get_preview, PageResult
        pages = get_preview(path, highlight_text="DRW-HL-001")
        assert len(pages) >= 1
        pr = pages[0]
        assert isinstance(pr, PageResult)
        # ハイライト機能削除済み: hit_boxes は常に空リスト
        assert pr.hit_boxes == []
        assert pr.mode == "RGB"

    def test_pdf_no_highlight_empty_boxes(self, tmp_path):
        """highlight_text='' のとき hit_boxes は空。"""
        pytest.importorskip("fitz")
        path = str(tmp_path / "no_hl.pdf")
        _make_minimal_pdf(path)
        from src.preview.preview_engine import get_preview
        pages = get_preview(path, highlight_text="")
        assert pages[0].hit_boxes == []

    def test_dxf_highlight_returns_pageresult(self, tmp_path):
        """DXF highlight_text 指定時も PageResult を返す（ハイライト機能削除済みのため hit_boxes は空）。"""
        pytest.importorskip("ezdxf")
        from tests.create_samples import make_dxf as _make_dxf_full
        path = str(tmp_path / "hl.dxf")
        _make_dxf_full(path, "DRW-HL-002", "DXF Highlight Test")
        from src.preview.preview_engine import get_preview, PageResult
        pages = get_preview(path, highlight_text="DRW-HL-002")
        assert len(pages) >= 1
        pr = pages[0]
        assert isinstance(pr, PageResult)
        # ハイライト機能削除済み: hit_boxes は常に空リスト
        assert pr.hit_boxes == []
        assert pr.mode == "RGB"

    def test_dxf_highlight_boxes_within_image(self, tmp_path):
        """ハイライトボックスの座標が画像サイズ内に収まっている。"""
        pytest.importorskip("ezdxf")
        from tests.create_samples import make_dxf as _make_dxf_full
        path = str(tmp_path / "bounds.dxf")
        _make_dxf_full(path, "BOUND-001", "Bounds Test")
        from src.preview.preview_engine import get_preview
        pages = get_preview(path, highlight_text="BOUND-001")
        pr = pages[0]
        W, H = pr.size
        for (x1, y1, x2, y2) in pr.hit_boxes:
            assert 0 <= x1 < W, f"x1={x1} out of range [0,{W})"
            assert 0 <= y1 < H, f"y1={y1} out of range [0,{H})"
            assert x1 < x2,     f"x1={x1} >= x2={x2}"
            assert y1 < y2,     f"y1={y1} >= y2={y2}"

    def test_highlight_applied_to_image_pixels(self, tmp_path):
        """ハイライト機能削除済み: highlight_text 有無に関わらず RGB 画像が返る。"""
        pytest.importorskip("fitz")
        from tests.create_samples import make_pdf as _make_pdf_full
        path = str(tmp_path / "pixel.pdf")
        _make_pdf_full(path, "YEL-001", "Yellow Highlight Test")
        from src.preview.preview_engine import get_preview
        pages_hl = get_preview(path, highlight_text="YEL-001")
        pages_no = get_preview(path, highlight_text="")
        # ハイライト機能削除済み: 両方とも RGB 画像が返り hit_boxes は空
        assert pages_hl[0].mode == "RGB"
        assert pages_no[0].mode == "RGB"
        assert pages_hl[0].hit_boxes == []
        assert pages_no[0].hit_boxes == []


# ────────────────────────────────────────────────────────────────────────────
#  DWG disk-cache helpers
# ────────────────────────────────────────────────────────────────────────────

class TestDwgDiskCache:
    """Tests for DWG disk-cache infrastructure in preview_engine."""

    def test_cache_key_is_deterministic(self):
        from src.preview.preview_engine import _dwg_cache_key
        k1 = _dwg_cache_key("/some/path/file.dwg", 1234567890.0)
        k2 = _dwg_cache_key("/some/path/file.dwg", 1234567890.0)
        assert k1 == k2

    def test_cache_key_differs_for_different_mtime(self):
        from src.preview.preview_engine import _dwg_cache_key
        k1 = _dwg_cache_key("/some/path/file.dwg", 1.0)
        k2 = _dwg_cache_key("/some/path/file.dwg", 2.0)
        assert k1 != k2

    def test_cache_key_differs_for_different_path(self):
        from src.preview.preview_engine import _dwg_cache_key
        k1 = _dwg_cache_key("/a/file.dwg", 1.0)
        k2 = _dwg_cache_key("/b/file.dwg", 1.0)
        assert k1 != k2

    def test_cache_key_length(self):
        from src.preview.preview_engine import _dwg_cache_key
        k = _dwg_cache_key("/some/file.dwg", 0.0)
        assert len(k) == 16

    def test_dxf_cache_path_ends_with_dxf(self):
        from src.preview.preview_engine import _dwg_dxf_cache_path
        p = _dwg_dxf_cache_path("abcdef1234567890")
        assert p.endswith(".dxf")

    def test_png_cache_path_ends_with_png(self):
        from src.preview.preview_engine import _dwg_png_cache_path
        p = _dwg_png_cache_path("abcdef1234567890", "Sheet1")
        assert p.endswith(".png")
        assert "Sheet1" in os.path.basename(p)

    def test_png_cache_path_safe_chars(self):
        """Slashes and spaces in layout name must be sanitized."""
        from src.preview.preview_engine import _dwg_png_cache_path
        p = _dwg_png_cache_path("key", "Sheet/1 Test")
        basename = os.path.basename(p)
        assert "/" not in basename
        assert " " not in basename

    def test_dwg_cache_dir_created(self, tmp_path, monkeypatch):
        """_dwg_cache_dir() must create the directory if it does not exist."""
        import src.preview.preview_engine as pe
        fake_cache = str(tmp_path / "fake_cache" / "drawing_search" / "dwg_preview")
        monkeypatch.setattr(pe, "_dwg_cache_dir", lambda: (
            os.makedirs(fake_cache, exist_ok=True) or fake_cache
        ))
        d = pe._dwg_cache_dir()
        assert os.path.isdir(d)

    def test_dwg_no_oda_returns_error_pageresult(self, tmp_path):
        """preview_dwg without ODA returns an error PageResult (not an exception)."""
        from src.preview.preview_engine import preview_dwg, PageResult
        fake_dwg = str(tmp_path / "test.dwg")
        # Minimal DWG-like file (not a real DWG, just non-empty)
        with open(fake_dwg, "wb") as f:
            f.write(b"AC1015" + b"\x00" * 100)
        result = preview_dwg(fake_dwg, config={"oda_path": ""})
        assert isinstance(result, list)
        assert len(result) >= 1
        assert isinstance(result[0], PageResult)

    def test_dwg_cache_purge_does_not_raise(self, tmp_path, monkeypatch):
        """_dwg_cache_purge must not raise even with many files."""
        import src.preview.preview_engine as pe
        monkeypatch.setattr(pe, "_dwg_cache_dir", lambda: str(tmp_path))
        # Create 10 dummy files
        for i in range(10):
            (tmp_path / f"dummy_{i}.png").write_bytes(b"x")
        # Purge with max_entries=5 should remove 5 files silently
        pe._dwg_cache_purge(max_entries=5)
        remaining = list(tmp_path.iterdir())
        assert len(remaining) == 5
