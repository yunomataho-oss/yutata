"""
preview_engine.py — 各ファイル形式をサムネイル/プレビュー画像(PIL Image)に変換する。

対応形式:
  PDF     → PyMuPDF (fitz) でページをラスタライズ
  DXF     → ezdxf drawing addon + matplotlib でレンダリング
  DWG     → ODA変換後に DXF と同じパイプライン
  SLDDRW  → ODA / COM 変換後 DXF or PDF パイプライン

戻り値は常に list[PIL.Image.Image] (ページ/レイアウトごとのリスト)。
エラー時はエラー文字列を描画したプレースホルダー画像を返す。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# ── サイズ定数 ────────────────────────────────────────────────────────────
DEFAULT_DPI     = 150          # PDF ラスタライズ解像度
MAX_PIXELS      = 4000 * 4000  # 安全上限
PREVIEW_BG      = "#ffffff"
ERROR_BG        = "#fff3e0"
ERROR_FG        = "#b71c1c"
PLACEHOLDER_SIZE = (800, 600)


# ── プレースホルダー生成 ──────────────────────────────────────────────────
def _make_placeholder(message: str, size: Tuple[int, int] = PLACEHOLDER_SIZE) -> Image.Image:
    img  = Image.new("RGB", size, PREVIEW_BG)
    draw = ImageDraw.Draw(img)
    margin = 40
    # テキスト折り返し描画
    lines = []
    for raw in message.split("\n"):
        # 80 文字ごとに折り返す
        while len(raw) > 78:
            lines.append(raw[:78])
            raw = raw[78:]
        lines.append(raw)
    y = margin
    for line in lines:
        draw.text((margin, y), line, fill=ERROR_FG)
        y += 20
    return img


def _make_error_image(title: str, detail: str) -> Image.Image:
    img  = Image.new("RGB", PLACEHOLDER_SIZE, ERROR_BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, PLACEHOLDER_SIZE[0], 40], fill="#ef9a9a")
    draw.text((10, 10), f"⚠ {title}", fill=ERROR_FG)
    y = 60
    for line in detail.split("\n"):
        draw.text((16, y), line, fill="#5d4037")
        y += 18
        if y > PLACEHOLDER_SIZE[1] - 20:
            break
    return img


# ── PDF プレビュー ────────────────────────────────────────────────────────
def preview_pdf(
    file_path: str,
    page_numbers: Optional[List[int]] = None,
    dpi: int = DEFAULT_DPI,
) -> List[Image.Image]:
    """
    PDF の各ページを PIL Image に変換する。
    page_numbers=None の場合は全ページ。
    """
    try:
        import fitz
    except ImportError:
        return [_make_error_image("PyMuPDF 未インストール",
                                   "pip install PyMuPDF")]

    try:
        doc    = fitz.open(file_path)
        pages  = page_numbers if page_numbers else list(range(len(doc)))
        images = []
        mat    = fitz.Matrix(dpi / 72, dpi / 72)

        for pg_idx in pages:
            if pg_idx >= len(doc):
                break
            page = doc[pg_idx]
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            img  = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)

        doc.close()
        return images if images else [_make_placeholder("PDF にページが見つかりません")]

    except Exception as exc:
        return [_make_error_image("PDF プレビューエラー", str(exc))]


# ── DXF プレビュー ────────────────────────────────────────────────────────
def _render_dxf_doc(doc, layout_name: Optional[str] = None) -> Image.Image:
    """ezdxf ドキュメントオブジェクトを PIL Image に変換する。"""
    import matplotlib
    matplotlib.use("Agg")          # GUI バックエンドを使わない
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.properties import LayoutProperties

    # レイアウト選択
    if layout_name and layout_name in doc.layouts.names():
        layout = doc.layouts.get(layout_name)
    else:
        # ペーパースペース優先、なければモデルスペース
        names = [n for n in doc.layouts.names() if n != "Model"]
        layout = doc.layouts.get(names[0]) if names else doc.modelspace()

    fig = plt.figure(figsize=(12, 9), dpi=110)
    ax  = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    ctx     = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    props   = LayoutProperties.from_layout(layout)

    Frontend(ctx, backend).draw_layout(layout, finalize=True,
                                        layout_properties=props)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor="white",
                bbox_inches="tight", pad_inches=0.05)
    buf.seek(0)
    img = Image.open(buf).copy()
    plt.close(fig)
    buf.close()
    return img


def preview_dxf(
    file_path: str,
    layout_names: Optional[List[str]] = None,
) -> List[Image.Image]:
    """
    DXF ファイルの各レイアウトを PIL Image に変換する。
    layout_names=None の場合は全レイアウト。
    """
    try:
        import ezdxf
        import ezdxf.recover as recover
    except ImportError:
        return [_make_error_image("ezdxf 未インストール", "pip install ezdxf")]

    try:
        # recover モードで読み込み (破損 DXF に耐性)
        try:
            doc, _ = recover.readfile(file_path)
        except Exception:
            doc = ezdxf.readfile(file_path)

        names   = layout_names if layout_names else list(doc.layouts.names())
        images  = []

        for name in names:
            try:
                img = _render_dxf_doc(doc, name)
                img.info["layout_name"] = name
                images.append(img)
            except Exception as exc:
                images.append(_make_error_image(f"レイアウト '{name}' のレンダリング失敗",
                                                 str(exc)))

        return images if images else [_make_placeholder("DXF に描画可能なレイアウトがありません")]

    except Exception as exc:
        return [_make_error_image("DXF プレビューエラー", str(exc))]


# ── DWG プレビュー ────────────────────────────────────────────────────────
def preview_dwg(file_path: str, config: Optional[dict] = None) -> List[Image.Image]:
    """
    DWG ファイルのプレビュー。
    ODA が利用可能なら DXF 変換後レンダリング、なければエラー画像を返す。
    """
    config = config or {}

    # ezdxf.addons.odafc で直接読み込み試みる
    try:
        import ezdxf
        from ezdxf.addons import odafc

        custom = config.get("oda_path", "")
        if custom and os.path.isfile(custom):
            if sys.platform == "win32":
                odafc.win_exec_path  = custom
            else:
                odafc.unix_exec_path = custom

        if odafc.is_installed():
            doc = odafc.readfile(file_path)
            names  = list(doc.layouts.names())
            images = []
            for name in names:
                try:
                    img = _render_dxf_doc(doc, name)
                    img.info["layout_name"] = name
                    images.append(img)
                except Exception as exc:
                    images.append(_make_error_image(
                        f"レイアウト '{name}' レンダリング失敗", str(exc)))
            return images if images else [_make_placeholder("DWG: 描画可能なレイアウトなし")]

    except Exception as exc:
        pass   # フォールバックへ

    # ODA CLI で DXF に変換してからレンダリング
    try:
        from src.extractors.dwg_extractor import find_oda_executable
        oda_exe = find_oda_executable(config.get("oda_path", ""))

        if oda_exe:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_in  = os.path.join(tmpdir, "in")
                tmp_out = os.path.join(tmpdir, "out")
                os.makedirs(tmp_in);  os.makedirs(tmp_out)
                shutil.copy2(file_path, tmp_in)

                cmd = [oda_exe, tmp_in, tmp_out, "ACAD2018", "DXF", "0", "1"]
                if sys.platform != "win32" and shutil.which("xvfb-run"):
                    cmd = ["xvfb-run", "-a"] + cmd

                subprocess.run(cmd, timeout=90, capture_output=True)

                dxf_files = [f for f in os.listdir(tmp_out) if f.lower().endswith(".dxf")]
                if dxf_files:
                    return preview_dxf(os.path.join(tmp_out, dxf_files[0]))
    except Exception:
        pass

    return [_make_error_image(
        "DWG プレビュー不可",
        "ODA File Converter が見つかりません。\n"
        "インストール後、「設定 > ODA File Converter 設定」で\n"
        "パスを登録してください。\n\n"
        "ダウンロード:\n"
        "https://www.opendesign.com/guestfiles/oda_file_converter",
    )]


# ── SLDDRW プレビュー ─────────────────────────────────────────────────────
def preview_slddrw(file_path: str, config: Optional[dict] = None) -> List[Image.Image]:
    """
    SLDDRW のプレビュー。
    Windows + SolidWorks → PDF エクスポートしてラスタライズ。
    それ以外 → プレースホルダー画像。
    """
    config = config or {}

    # Windows + SolidWorks COM API
    if sys.platform == "win32":
        try:
            images = _slddrw_via_com(file_path)
            if images:
                return images
        except Exception:
            pass

    return [_make_error_image(
        "SLDDRW プレビュー制限",
        "SolidWorks プレビューには\n"
        "Windows + SolidWorks のインストールが必要です。\n\n"
        "テキスト検索結果は「詳細情報」パネルで確認できます。",
    )]


def _slddrw_via_com(file_path: str) -> List[Image.Image]:
    """SolidWorks COM API で PDF エクスポート → ラスタライズ。"""
    import win32com.client as win32  # type: ignore
    import tempfile

    sw = win32.Dispatch("SldWorks.Application")
    sw.Visible = False

    abs_path = os.path.abspath(file_path)
    errors   = win32.VARIANT(win32.VT_BYREF | win32.VT_I4, 0)
    warnings = win32.VARIANT(win32.VT_BYREF | win32.VT_I4, 0)
    doc = sw.OpenDoc6(abs_path, 3, 1, "", errors, warnings)

    if doc is None:
        raise RuntimeError("SolidWorks がファイルを開けませんでした")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = f.name

    try:
        doc.SaveAs3(pdf_path, 0, 0)
        sw.CloseDoc(abs_path)
        return preview_pdf(pdf_path)
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


# ── 統合エントリポイント ──────────────────────────────────────────────────
def get_preview(
    file_path: str,
    config: Optional[dict] = None,
    max_pages: int = 20,
) -> List[Image.Image]:
    """
    ファイル形式を自動判別してプレビュー画像リストを返す。

    戻り値: list[PIL.Image.Image]  (常に 1 枚以上)
    """
    config = config or {}
    ext    = os.path.splitext(file_path)[1].lower()

    if not os.path.isfile(file_path):
        return [_make_error_image("ファイルが見つかりません", file_path)]

    if ext == ".pdf":
        pages = preview_pdf(file_path)
        return pages[:max_pages]

    elif ext == ".dxf":
        return preview_dxf(file_path)[:max_pages]

    elif ext == ".dwg":
        return preview_dwg(file_path, config)[:max_pages]

    elif ext == ".slddrw":
        return preview_slddrw(file_path, config)[:max_pages]

    else:
        return [_make_error_image("未対応形式", f"拡張子 '{ext}' には対応していません。")]
