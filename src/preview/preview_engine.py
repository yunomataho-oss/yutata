"""
preview_engine.py — 各ファイル形式をサムネイル/プレビュー画像(PIL Image)に変換する。

対応形式:
  PDF     → PyMuPDF (fitz) でページをラスタライズ
  DXF     → ezdxf drawing addon + matplotlib でレンダリング
  DWG     → ODA変換後に DXF と同じパイプライン
  SLDDRW  → ODA / COM 変換後 DXF or PDF パイプライン

戻り値は常に list[PIL.Image.Image] (ページ/レイアウトごとのリスト)。
エラー時はエラー文字列を描画したプレースホルダー画像を返す。

DXF/DWG レンダリング修正ポイント:
  1. LayoutProperties.set_colors('#ffffff') で白背景を強制
     → デフォルトの #212830(黒)背景のまま白線を描くと不可視になるバグを修正
  2. RGBA → RGB 変換 (白背景と合成)
     → matplotlib が RGBA 出力したとき alpha=0 部分が透明のままになるバグを修正
  3. 空レイアウトのスキップ
     → VIEWPORT のみ・エンティティ 0 件のレイアウトを除外
  4. ax.set_aspect('equal') + ax.margins() でアスペクト比を維持
     → bbox_inches='tight' による図形の歪みを防止
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
DEFAULT_DPI      = 150          # PDF ラスタライズ解像度
DXF_FIG_WIDTH    = 14           # DXF レンダリング figsize 横インチ
DXF_FIG_HEIGHT   = 10           # DXF レンダリング figsize 縦インチ
DXF_DPI          = 96           # DXF レンダリング DPI
DXF_BG_COLOR     = "#ffffff"    # 強制白背景
DXF_PAD          = 0.08         # bbox_inches tight 時の余白インチ
DXF_MARGIN_RATIO = 0.05         # ax.margins() — 図形周囲の余白率
# 空レイアウトとみなすエンティティタイプ (これだけの場合はスキップ)
_SKIP_ENTITY_TYPES = frozenset({"VIEWPORT"})

MAX_PIXELS       = 4000 * 4000  # 安全上限
PREVIEW_BG       = "#ffffff"
ERROR_BG         = "#fff3e0"
ERROR_FG         = "#b71c1c"
PLACEHOLDER_SIZE = (800, 600)


# ── プレースホルダー生成 ──────────────────────────────────────────────────
def _make_placeholder(message: str, size: Tuple[int, int] = PLACEHOLDER_SIZE) -> Image.Image:
    img  = Image.new("RGB", size, PREVIEW_BG)
    draw = ImageDraw.Draw(img)
    margin = 40
    lines: List[str] = []
    for raw in message.split("\n"):
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
    draw.text((10, 10), f"[!] {title}", fill=ERROR_FG)
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
    """PDF の各ページを PIL Image(RGB) に変換する。"""
    try:
        import fitz
    except ImportError:
        return [_make_error_image("PyMuPDF 未インストール",
                                   "pip install PyMuPDF")]

    try:
        doc    = fitz.open(file_path)
        pages  = page_numbers if page_numbers else list(range(len(doc)))
        images: List[Image.Image] = []
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


# ── DXF レンダリングコア ──────────────────────────────────────────────────

def _count_visible_entities(layout) -> int:
    """VIEWPORT などを除いた実質エンティティ数を返す。"""
    return sum(1 for e in layout if e.dxftype() not in _SKIP_ENTITY_TYPES)


def _rgba_to_rgb(img: Image.Image) -> Image.Image:
    """RGBA / P / L を白背景の RGB に変換する。"""
    if img.mode == "RGB":
        return img
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB")


def _render_dxf_layout(doc, layout) -> Image.Image:
    """
    ezdxf ドキュメントの 1 レイアウトを PIL Image(RGB) に変換する。

    修正点:
      - LayoutProperties.set_colors('#ffffff') で白背景を強制
      - ax.set_aspect('equal') でアスペクト比を維持
      - ax.margins() で余白を確保
      - RGBA → RGB 白背景合成
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.properties import LayoutProperties

    fig = plt.figure(figsize=(DXF_FIG_WIDTH, DXF_FIG_HEIGHT), dpi=DXF_DPI)
    ax  = fig.add_axes([0, 0, 1, 1])

    # ── 白背景を強制（最重要: デフォルト #212830 の黒背景で白線が見えなくなるバグを防ぐ）
    props = LayoutProperties.from_layout(layout)
    props.set_colors(DXF_BG_COLOR)          # background → white, fg → black

    ax.set_facecolor(DXF_BG_COLOR)
    fig.patch.set_facecolor(DXF_BG_COLOR)

    # アスペクト比保持・余白確保
    ax.set_aspect("equal", adjustable="datalim")
    ax.margins(DXF_MARGIN_RATIO)
    ax.axis("off")

    ctx     = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    Frontend(ctx, backend).draw_layout(layout, finalize=True,
                                        layout_properties=props)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=DXF_DPI,
        facecolor=DXF_BG_COLOR,
        bbox_inches="tight",
        pad_inches=DXF_PAD,
    )
    buf.seek(0)
    raw = Image.open(buf).copy()
    plt.close(fig)
    buf.close()

    # RGBA → RGB（アルファ透明部を白で合成）
    return _rgba_to_rgb(raw)


def preview_dxf(
    file_path: str,
    layout_names: Optional[List[str]] = None,
) -> List[Image.Image]:
    """
    DXF ファイルの各レイアウトを PIL Image に変換する。

    - 空レイアウト（VIEWPORT のみ、エンティティ 0 件）は自動スキップ
    - layout_names=None の場合: 全レイアウトから空を除いたものを描画
      (ペーパースペース優先 → モデルスペース)
    """
    try:
        import ezdxf
        import ezdxf.recover as recover
    except ImportError:
        return [_make_error_image("ezdxf 未インストール", "pip install ezdxf")]

    try:
        try:
            doc, _ = recover.readfile(file_path)
        except Exception:
            doc = ezdxf.readfile(file_path)

        all_names = list(doc.layouts.names())

        if layout_names:
            target_names = [n for n in layout_names if n in all_names]
        else:
            # ペーパースペース優先
            paper  = [n for n in all_names if n != "Model"]
            model  = [n for n in all_names if n == "Model"]
            target_names = paper + model

        images: List[Image.Image] = []
        rendered_any = False

        for name in target_names:
            layout = doc.layouts.get(name)
            if _count_visible_entities(layout) == 0:
                # エンティティなし → スキップ（空白ページを出さない）
                continue
            try:
                img = _render_dxf_layout(doc, layout)
                img.info["layout_name"] = name
                images.append(img)
                rendered_any = True
            except Exception as exc:
                images.append(_make_error_image(
                    f"レイアウト '{name}' のレンダリング失敗",
                    str(exc),
                ))

        if not rendered_any:
            return [_make_placeholder(
                "DXF に描画可能なエンティティが見つかりませんでした。\n"
                "図面が空か、対応していない形式の可能性があります。"
            )]

        return images

    except Exception as exc:
        return [_make_error_image("DXF プレビューエラー", str(exc))]


# ── DWG プレビュー ────────────────────────────────────────────────────────
def preview_dwg(file_path: str, config: Optional[dict] = None) -> List[Image.Image]:
    """
    DWG ファイルのプレビュー。
    ODA が利用可能なら DXF 変換後レンダリング、なければエラー画像を返す。
    """
    config = config or {}

    # ── 方法 1: ezdxf.addons.odafc で直接読み込み
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
            all_names = list(doc.layouts.names())
            paper     = [n for n in all_names if n != "Model"]
            model     = [n for n in all_names if n == "Model"]
            images: List[Image.Image] = []

            for name in paper + model:
                layout = doc.layouts.get(name)
                if _count_visible_entities(layout) == 0:
                    continue
                try:
                    img = _render_dxf_layout(doc, layout)
                    img.info["layout_name"] = name
                    images.append(img)
                except Exception as exc:
                    images.append(_make_error_image(
                        f"レイアウト '{name}' レンダリング失敗", str(exc)))

            return images if images else [_make_placeholder("DWG: 描画可能なレイアウトなし")]

    except Exception:
        pass

    # ── 方法 2: ODA CLI で DXF に変換してからレンダリング
    try:
        from src.extractors.dwg_extractor import find_oda_executable
        oda_exe = find_oda_executable(config.get("oda_path", ""))

        if oda_exe:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_in  = os.path.join(tmpdir, "in")
                tmp_out = os.path.join(tmpdir, "out")
                os.makedirs(tmp_in)
                os.makedirs(tmp_out)
                shutil.copy2(file_path, tmp_in)

                cmd = [oda_exe, tmp_in, tmp_out, "ACAD2018", "DXF", "0", "1"]
                if sys.platform != "win32" and shutil.which("xvfb-run"):
                    cmd = ["xvfb-run", "-a"] + cmd

                subprocess.run(cmd, timeout=90, capture_output=True)

                dxf_files = [
                    f for f in os.listdir(tmp_out) if f.lower().endswith(".dxf")
                ]
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

    sw       = win32.Dispatch("SldWorks.Application")
    sw.Visible = False

    abs_path = os.path.abspath(file_path)
    errors   = win32.VARIANT(win32.VT_BYREF | win32.VT_I4, 0)
    warnings = win32.VARIANT(win32.VT_BYREF | win32.VT_I4, 0)
    doc      = sw.OpenDoc6(abs_path, 3, 1, "", errors, warnings)

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

    戻り値: list[PIL.Image.Image]  (常に 1 枚以上、モードは RGB)
    """
    config = config or {}
    ext    = os.path.splitext(file_path)[1].lower()

    if not os.path.isfile(file_path):
        return [_make_error_image("ファイルが見つかりません", file_path)]

    if ext == ".pdf":
        pages = preview_pdf(file_path)
    elif ext == ".dxf":
        pages = preview_dxf(file_path)
    elif ext == ".dwg":
        pages = preview_dwg(file_path, config)
    elif ext == ".slddrw":
        pages = preview_slddrw(file_path, config)
    else:
        pages = [_make_error_image("未対応形式", f"拡張子 '{ext}' には対応していません。")]

    # 全ページを RGB に正規化して返す
    return [_rgba_to_rgb(p) for p in pages[:max_pages]]
