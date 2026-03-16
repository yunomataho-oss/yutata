"""
preview_engine.py — 各ファイル形式をサムネイル/プレビュー画像(PIL Image)に変換する。

対応形式:
  PDF     → PyMuPDF (fitz) でページをラスタライズ
  DXF     → ezdxf SVGBackend → fitz でラスタライズ  ★ 高速パイプライン
  DWG     → ODA変換後に DXF と同じパイプライン
  SLDDRW  → ODA / COM 変換後 DXF or PDF パイプライン

高速化ポイント:
  [DXF] matplotlib(≈1800ms) → 現在約 120ms まで高速化済み (500エンティティ時)
    ・TextPolicy.IGNORE: テキスト完全スキップ → fontTools 不要 (REPLACE_RECT比3倍高速)
    ・LinePolicy.SOLID: dash パターン計算をスキップ
    ・HatchPolicy.IGNORE: ハッチング計算をスキップ
    ・circle_approximation_count=16: 円近似頂点数を最小化
    ・max_flattening_distance=0.1: ベジェ展開精度を緩和
    ・output_coordinate_space=500: SVGパス数値精度を下げて make_path_str 軽減
    ・fixed_stroke_width=0.15: stroke 幅計算をスキップ
    ・get_xml_root_element() + ET.tostring(bytes): str.encode() をスキップ
  [PDF] DEFAULT_DPI=96 (旧150) → 約3倍速
  [キャッシュ] LRUキャッシュ: 同一ファイル+mtime でスキップ
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw

# ── サイズ定数 ────────────────────────────────────────────────────────────
DEFAULT_DPI          = 96              # PDF 描画 DPI
DXF_SVG_TARGET_W     = 800            # DXF 出力画像目標幅 (px)
DXF_SVG_COORD_SPACE  = 500             # SVGBackend output_coordinate_space (小さいほど高速)
DXF_BG_COLOR         = "#ffffff"
_SKIP_ENTITY_TYPES   = frozenset({"VIEWPORT"})

# ── ezdxf 描画設定 (モジュールレベルでキャッシュ) ──────────────────────────
# TextPolicy.IGNORE:  テキスト完全スキップ → fontTools不要で最速 (REPLACE_RECT比約3倍)
# LinePolicy.SOLID:   実線のみ (dash 計算スキップ)
# HatchPolicy.IGNORE: ハッチング無視 (ハッチング計算スキップ)
_DXF_DRAW_CONFIG = None  # 遅延初期化（ezdxf import 後）


def _get_dxf_draw_config():
    """ezdxf 用描画設定を取得する。モジュールレベルでキャッシュ。"""
    global _DXF_DRAW_CONFIG
    if _DXF_DRAW_CONFIG is None:
        from ezdxf.addons.drawing.config import (
            Configuration, TextPolicy, LinePolicy, HatchPolicy
        )
        _DXF_DRAW_CONFIG = Configuration(
            text_policy=TextPolicy.IGNORE,          # テキスト完全スキップ (最速・fontTools不要)
            line_policy=LinePolicy.SOLID,           # 実線のみ (dash計算スキップ)
            hatch_policy=HatchPolicy.IGNORE,        # ハッチング無視
            circle_approximation_count=16,          # 円近似山数 (旧32→さらに軽量)
            max_flattening_distance=0.1,            # ベジェ展開精度 (旧0.05→さらに軽量)
        )
    return _DXF_DRAW_CONFIG

MAX_PIXELS           = 3000 * 3000    # 上限ピクセル数
PREVIEW_BG           = "#ffffff"
ERROR_BG             = "#fff3e0"
ERROR_FG             = "#b71c1c"
PLACEHOLDER_SIZE     = (800, 600)

# ── プレビューキャッシュ ──────────────────────────────────────────────────
# (file_path, mtime) → list[PageResult] をキャッシュ
# ★軽量化: highlight_text をキーから除外し再利用率を向上
_PREVIEW_CACHE: dict = {}
_CACHE_MAX = 20   # 最大エントリ数


def _cache_get(file_path: str, mtime: float):
    key = (file_path, mtime)
    return _PREVIEW_CACHE.get(key)


def _cache_put(file_path: str, mtime: float, pages):
    key = (file_path, mtime)
    if len(_PREVIEW_CACHE) >= _CACHE_MAX:
        oldest = next(iter(_PREVIEW_CACHE))
        del _PREVIEW_CACHE[oldest]
    _PREVIEW_CACHE[key] = pages


# ── ページ結果 dataclass ──────────────────────────────────────────────────
@dataclass
class PageResult:
    """1 ページ/レイアウトのレンダリング結果。"""
    image:       Image.Image
    hit_boxes:   List[Tuple[int, int, int, int]] = field(default_factory=list)
    layout_name: str = ""

    # PIL.Image と同じ属性へのアクセスを透過させる
    @property
    def size(self):  return self.image.size
    @property
    def width(self): return self.image.width
    @property
    def height(self):return self.image.height
    @property
    def mode(self):  return self.image.mode
    @property
    def info(self):  return self.image.info

    def to_pil(self) -> Image.Image:
        img = self.image.copy()
        img.info["layout_name"] = self.layout_name
        img.info["hit_boxes"]   = self.hit_boxes
        return img


# ── DWG ディスクキャッシュ ───────────────────────────────────────────────
# ~/.cache/drawing_search/dwg_preview/ に PNG / DXF を永続キャッシュする。
# キー: SHA1(file_path + str(mtime))  → 重複変換・再レンダリングを防ぐ

def _dwg_cache_dir() -> str:
    """DWG キャッシュディレクトリを返す（なければ作成）。"""
    base = os.path.join(
        os.path.expanduser("~"), ".cache", "drawing_search", "dwg_preview"
    )
    os.makedirs(base, exist_ok=True)
    return base


def _dwg_cache_key(file_path: str, mtime: float) -> str:
    """(ファイルパス, mtime) → 16文字の16進数ハッシュ文字列。"""
    raw = f"{os.path.abspath(file_path)}|{mtime:.6f}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _dwg_dxf_cache_path(key: str) -> str:
    """DXF 中間ファイルのキャッシュパスを返す。"""
    return os.path.join(_dwg_cache_dir(), f"{key}.dxf")


def _dwg_png_cache_path(key: str, layout_name: str = "__default__") -> str:
    """PNG サムネイルのキャッシュパスを返す。"""
    safe = layout_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return os.path.join(_dwg_cache_dir(), f"{key}_{safe}.png")


def _dwg_cache_purge(max_entries: int = 200) -> None:
    """
    キャッシュが max_entries を超えたら古いファイルを削除する。
    200 ファイル × 平均 100 KB ≈ 20 MB が目安。
    """
    cache_dir = _dwg_cache_dir()
    try:
        files = [
            (os.path.getmtime(os.path.join(cache_dir, f)), os.path.join(cache_dir, f))
            for f in os.listdir(cache_dir)
            if os.path.isfile(os.path.join(cache_dir, f))
        ]
        if len(files) > max_entries:
            files.sort()  # 古い順
            for _, fp in files[: len(files) - max_entries]:
                try:
                    os.remove(fp)
                except OSError:
                    pass
    except OSError:
        pass


# ── ODA subprocess ヘルパー ───────────────────────────────────────────────

def _run_oda_subprocess(
    oda_exe: str,
    tmp_in: str,
    tmp_out: str,
    version: str = "ACAD2018",
    audit: str = "0",
    timeout: int = 30,
) -> None:
    """
    ODA File Converter を低優先度プロセスとして実行する。

    CPU 負荷対策:
      - Windows: CREATE_NO_WINDOW + BELOW_NORMAL_PRIORITY_CLASS
      - Linux/macOS: nice +10 でバックグラウンド優先度に下げる
    タイムアウト: デフォルト 30 秒（旧実装の 120 秒から短縮）
    """
    env = _ensure_display_env()
    cmd = [oda_exe, tmp_in, tmp_out, version, "DXF", "0", audit]

    if sys.platform == "win32":
        kwargs: dict = dict(
            timeout=timeout,
            capture_output=True,
            env=env,
            # コンソール非表示 + CPU 優先度: BELOW_NORMAL (0x4000)
            creationflags=subprocess.CREATE_NO_WINDOW | 0x4000,
        )
    else:
        # xvfb-run でヘッドレス対応 + nice +10 で低優先度
        if shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
        if shutil.which("nice"):
            cmd = ["nice", "-n", "10"] + cmd
        kwargs = dict(timeout=timeout, capture_output=True, env=env)

    subprocess.run(cmd, **kwargs)


# ── プレースホルダー生成 ──────────────────────────────────────────────────
def _make_placeholder(message: str, size: Tuple[int, int] = PLACEHOLDER_SIZE) -> Image.Image:
    img  = Image.new("RGB", size, PREVIEW_BG)
    draw = ImageDraw.Draw(img)
    margin = 40
    lines: List[str] = []
    for raw in message.split("\n"):
        while len(raw) > 78:
            lines.append(raw[:78]); raw = raw[78:]
        lines.append(raw)
    y = margin
    for line in lines:
        draw.text((margin, y), line, fill=ERROR_FG); y += 20
    return img


def _make_error_image(title: str, detail: str) -> Image.Image:
    img  = Image.new("RGB", PLACEHOLDER_SIZE, ERROR_BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, PLACEHOLDER_SIZE[0], 40], fill="#ef9a9a")
    draw.text((10, 10), f"[!] {title}", fill=ERROR_FG)
    y = 60
    for line in detail.split("\n"):
        draw.text((16, y), line, fill="#5d4037"); y += 18
        if y > PLACEHOLDER_SIZE[1] - 20: break
    return img


# ── 正規化 ────────────────────────────────────────────────────────────────
def _rgba_to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":   return img
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg
    return img.convert("RGB")


def _count_visible_entities(layout) -> int:
    return sum(1 for e in layout if e.dxftype() not in _SKIP_ENTITY_TYPES)


# ── DXF レンダリング: SVGBackend + fitz (高速) ───────────────────────────
def _render_dxf_layout_svg(
    doc,
    layout,
) -> PageResult:
    """
    ezdxf SVGBackend → PyMuPDF (fitz) でラスタライズする高速パイプライン。

    高速化ポイント:
      1. TextPolicy.IGNORE: テキスト完全スキップ → fontTools 不要 (REPLACE_RECT比3倍高速)
      2. LinePolicy.SOLID + HatchPolicy.IGNORE: dash/ハッチング計算をスキップ
      3. circle_approximation_count=16: 円近似頂点数を最小化
      4. max_flattening_distance=0.1: ベジェ展開精度を緩和
      5. output_coordinate_space=500 + fixed_stroke_width: SVGパス生成を軽減
      6. get_xml_root_element() + ET.tostring(bytes): str.encode() をスキップ
    """
    import fitz
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.svg import SVGBackend
    from ezdxf.addons.drawing.layout import Page, Settings, Units
    from ezdxf.addons.drawing.properties import LayoutProperties

    TARGET_W = DXF_SVG_TARGET_W

    ctx   = RenderContext(doc)
    props = LayoutProperties.from_layout(layout)
    props.set_colors(DXF_BG_COLOR)

    backend = SVGBackend()
    Frontend(ctx, backend, config=_get_dxf_draw_config()).draw_layout(
        layout, finalize=True, layout_properties=props
    )

    # get_xml_root_element + ET.tostring で直接 bytes 生成 (str.encode をスキップ)
    page_obj     = Page(TARGET_W / 96.0, (TARGET_W * 0.65) / 96.0, Units.inch)
    svg_settings = Settings(
        fit_page=True,
        output_coordinate_space=DXF_SVG_COORD_SPACE,
        fixed_stroke_width=0.15,
    )
    xml_root  = backend.get_xml_root_element(page_obj, settings=svg_settings)
    svg_bytes = ET.tostring(xml_root, encoding="utf-8", xml_declaration=True)

    if len(svg_bytes) < 100:
        return PageResult(_make_placeholder("DXF: 描画領域が空です"))

    # fitz で SVG → RGB 画像
    fdoc  = fitz.open(stream=svg_bytes, filetype="svg")
    fpage = fdoc[0]
    scale = TARGET_W / max(fpage.rect.width, 1)
    pix   = fpage.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img   = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    fdoc.close()

    return PageResult(image=img)


# ── DXF プレビュー ────────────────────────────────────────────────────────
def preview_dxf(
    file_path: str,
    layout_names: Optional[List[str]] = None,
    highlight_text: str = "",
) -> List[PageResult]:
    """DXF ファイルの各レイアウトを PageResult に変換する。"""
    try:
        import ezdxf
        import fitz  # noqa: F401 — 存在確認
    except ImportError as e:
        pkg = "ezdxf" if "ezdxf" in str(e) else "PyMuPDF"
        return [PageResult(_make_error_image(f"{pkg} 未インストール",
                                             f"pip install {pkg}"))]
    try:
        # ★軽量化: 直接読み込み優先、失敗時のみ recover にフォールバック
        try:
            doc = ezdxf.readfile(file_path)
        except Exception:
            import ezdxf.recover as recover
            doc, _ = recover.readfile(file_path)

        all_names = list(doc.layouts.names())
        if layout_names:
            target_names = [n for n in layout_names if n in all_names]
        else:
            paper  = [n for n in all_names if n != "Model"]
            model  = [n for n in all_names if n == "Model"]
            target_names = paper + model

        results: List[PageResult] = []
        rendered_any = False

        for name in target_names:
            layout = doc.layouts.get(name)
            if _count_visible_entities(layout) == 0:
                continue
            try:
                pr = _render_dxf_layout_svg(doc, layout)
                pr.layout_name = name
                pr.image.info["layout_name"] = name
                results.append(pr)
                rendered_any = True
            except Exception as exc:
                results.append(PageResult(
                    _make_error_image(f"レイアウト '{name}' のレンダリング失敗",
                                      str(exc)),
                    layout_name=name,
                ))

        if not rendered_any:
            return [PageResult(_make_placeholder(
                "DXF に描画可能なエンティティが見つかりませんでした。\n"
                "図面が空か、対応していない形式の可能性があります。"
            ))]
        return results

    except Exception as exc:
        return [PageResult(_make_error_image("DXF プレビューエラー", str(exc)))]


# ── PDF プレビュー ────────────────────────────────────────────────────────
def preview_pdf(
    file_path: str,
    page_numbers: Optional[List[int]] = None,
    dpi: int = DEFAULT_DPI,
    highlight_text: str = "",
) -> List[PageResult]:
    """PDF の各ページを PageResult に変換する。"""
    try:
        import fitz
    except ImportError:
        return [PageResult(_make_error_image("PyMuPDF 未インストール",
                                             "pip install PyMuPDF"))]
    try:
        doc    = fitz.open(file_path)
        pages  = page_numbers if page_numbers else list(range(len(doc)))
        mat    = fitz.Matrix(dpi / 72, dpi / 72)
        results: List[PageResult] = []

        for pg_idx in pages:
            if pg_idx >= len(doc): break
            page = doc[pg_idx]
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            img  = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pr = PageResult(image=img, layout_name=f"ページ {pg_idx + 1}")
            results.append(pr)

        doc.close()
        return results if results else [PageResult(
            _make_placeholder("PDF にページが見つかりません"))]

    except Exception as exc:
        return [PageResult(_make_error_image("PDF プレビューエラー", str(exc)))]


# ── DWG プレビュー ────────────────────────────────────────────────────────
def _ensure_display_env() -> dict:
    """
    Linux/macOS で ODA を実行する際に DISPLAY 環境変数を保証する。
    """
    env = os.environ.copy()
    if sys.platform != "win32" and not env.get("DISPLAY"):
        env["DISPLAY"] = ":99" if shutil.which("Xvfb") else ":0"
    return env


def _render_dwg_from_dxf(
    dxf_path: str,
    target_layout: Optional[str],
    cache_key: str,
) -> List[PageResult]:
    """
    変換済み DXF ファイルを読み込み、レイアウトをレンダリングする。
    PNG サムネイルをディスクにキャッシュして再利用する。

    target_layout が指定された場合は 1 枚のみレンダリングする。
    """
    try:
        import ezdxf
    except ImportError:
        return [PageResult(_make_error_image("ezdxf 未インストール", "pip install ezdxf"))]

    try:
        try:
            doc = ezdxf.readfile(dxf_path)
        except Exception:
            import ezdxf.recover as recover
            doc, _ = recover.readfile(dxf_path)
    except Exception as exc:
        return [PageResult(_make_error_image("DXF 読み込みエラー", str(exc)))]

    all_names  = list(doc.layouts.names())
    paper      = [n for n in all_names if n != "Model"]
    model      = [n for n in all_names if n == "Model"]
    candidates = paper + model

    # target_layout 指定時は一致するレイアウトのみ
    if target_layout is not None:
        candidates = [n for n in candidates if n == target_layout] or candidates[:1]

    results: List[PageResult] = []
    for name in candidates:
        layout = doc.layouts.get(name)
        if _count_visible_entities(layout) == 0:
            continue

        # ── PNG キャッシュ確認 ───────────────────────────────────────
        png_path = _dwg_png_cache_path(cache_key, name)
        if os.path.isfile(png_path):
            try:
                img = Image.open(png_path).copy()   # copy で PIL ファイルロックを解放
                pr  = PageResult(image=img, layout_name=name)
                results.append(pr)
                continue
            except Exception:
                pass  # 壊れたキャッシュは無視して再レンダリング

        # ── レンダリング ─────────────────────────────────────────────
        try:
            pr = _render_dxf_layout_svg(doc, layout)
            pr.layout_name = name
            pr.image.info["layout_name"] = name

            # PNG キャッシュに保存（失敗しても続行）
            try:
                pr.image.save(png_path, format="PNG", optimize=True)
            except Exception:
                pass

            results.append(pr)
        except Exception as exc:
            results.append(PageResult(
                _make_error_image(f"レイアウト '{name}' レンダリング失敗", str(exc)),
                layout_name=name,
            ))

    if not results:
        return [PageResult(_make_placeholder("DWG: 描画可能なレイアウトなし"))]
    return results


def preview_dwg(
    file_path: str,
    config: Optional[dict] = None,
    target_layout: Optional[str] = None,
    highlight_text: str = "",
) -> List[PageResult]:
    """
    DWG プレビューを生成する。

    高速化ポイント:
      1. DXF 中間ファイルをディスクキャッシュ → 2 回目以降 ODA 変換をスキップ
      2. PNG サムネイルをディスクキャッシュ → 再起動後も再利用
      3. ODA subprocess を低優先度で実行 (BELOW_NORMAL / nice +10)
      4. ODA タイムアウトを 30 秒に短縮 (旧: 120 秒)
      5. target_layout 指定時は指定レイアウトのみレンダリング
    """
    config     = config or {}
    audit_flag = bool(config.get("oda_audit", True))
    oda_timeout = int(config.get("oda_timeout", 30))  # デフォルト 30 秒

    # ── キャッシュキー ────────────────────────────────────────────────
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = 0.0
    cache_key = _dwg_cache_key(file_path, mtime)

    # ── DXF 中間ファイルキャッシュ確認 ───────────────────────────────
    dxf_cache = _dwg_dxf_cache_path(cache_key)
    if os.path.isfile(dxf_cache):
        # キャッシュ済み DXF → ODA 変換スキップ、直接レンダリング
        return _render_dwg_from_dxf(dxf_cache, target_layout, cache_key)

    # ── ODA 経由で変換 ───────────────────────────────────────────────
    try:
        from src.extractors.dwg_extractor import find_oda_executable
    except ImportError:
        find_oda_executable = lambda p: p if p and os.path.isfile(p) else None  # noqa: E731

    oda_exe = find_oda_executable(config.get("oda_path", ""))

    # ── 方法 1: ezdxf.addons.odafc (メモリ経由) ──────────────────────
    if oda_exe:
        try:
            import ezdxf
            from ezdxf.addons import odafc

            if sys.platform == "win32":
                ezdxf.options.set("odafc-addon", "win_exec_path",  oda_exe)
            else:
                ezdxf.options.set("odafc-addon", "unix_exec_path", oda_exe)

            old_display = os.environ.get("DISPLAY")
            display_env = _ensure_display_env()
            try:
                if sys.platform != "win32" and not os.environ.get("DISPLAY"):
                    os.environ["DISPLAY"] = display_env["DISPLAY"]

                if odafc.is_installed():
                    try:
                        doc = odafc.readfile(file_path, audit=audit_flag)
                    except Exception:
                        if audit_flag and sys.platform == "win32":
                            doc = odafc.readfile(file_path, audit=False)
                        else:
                            raise

                    all_names = list(doc.layouts.names())
                    paper     = [n for n in all_names if n != "Model"]
                    model     = [n for n in all_names if n == "Model"]
                    candidates = paper + model
                    if target_layout is not None:
                        candidates = [n for n in candidates if n == target_layout] or candidates[:1]

                    results: List[PageResult] = []
                    for name in candidates:
                        layout = doc.layouts.get(name)
                        if _count_visible_entities(layout) == 0:
                            continue

                        png_path = _dwg_png_cache_path(cache_key, name)
                        if os.path.isfile(png_path):
                            try:
                                img = Image.open(png_path).copy()
                                results.append(PageResult(image=img, layout_name=name))
                                continue
                            except Exception:
                                pass

                        try:
                            pr = _render_dxf_layout_svg(doc, layout)
                            pr.layout_name = name
                            pr.image.info["layout_name"] = name
                            try:
                                pr.image.save(png_path, format="PNG", optimize=True)
                            except Exception:
                                pass
                            results.append(pr)
                        except Exception as exc:
                            results.append(PageResult(
                                _make_error_image(f"レイアウト '{name}' レンダリング失敗", str(exc)),
                                layout_name=name,
                            ))

                    _dwg_cache_purge()
                    return results if results else [PageResult(
                        _make_placeholder("DWG: 描画可能なレイアウトなし"))]

            finally:
                if sys.platform != "win32":
                    if old_display is None:
                        os.environ.pop("DISPLAY", None)
                    else:
                        os.environ["DISPLAY"] = old_display

        except Exception:
            pass

    # ── 方法 2: ODA CLI → DXF → ディスクキャッシュ → レンダリング ────
    if oda_exe:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_in  = os.path.join(tmpdir, "in")
                tmp_out = os.path.join(tmpdir, "out")
                os.makedirs(tmp_in)
                os.makedirs(tmp_out)
                shutil.copy2(file_path, tmp_in)

                audit_str = "1" if audit_flag else "0"
                try:
                    _run_oda_subprocess(oda_exe, tmp_in, tmp_out,
                                        version=config.get("oda_version", "ACAD2018"),
                                        audit=audit_str, timeout=oda_timeout)
                except subprocess.TimeoutExpired:
                    pass  # タイムアウトでも出力を確認

                dxf_files: List[str] = []
                for root, _, files in os.walk(tmp_out):
                    for f in files:
                        if f.lower().endswith(".dxf"):
                            dxf_files.append(os.path.join(root, f))

                # Windows で audit=1 失敗時は audit=0 でリトライ
                if not dxf_files and audit_str == "1" and sys.platform == "win32":
                    try:
                        _run_oda_subprocess(oda_exe, tmp_in, tmp_out,
                                            version=config.get("oda_version", "ACAD2018"),
                                            audit="0", timeout=oda_timeout)
                    except subprocess.TimeoutExpired:
                        pass
                    for root, _, files in os.walk(tmp_out):
                        for f in files:
                            if f.lower().endswith(".dxf"):
                                dxf_files.append(os.path.join(root, f))

                if dxf_files:
                    # DXF をキャッシュディレクトリにコピー（次回以降 ODA 不要）
                    try:
                        shutil.copy2(dxf_files[0], dxf_cache)
                        _dwg_cache_purge()
                    except Exception:
                        pass
                    result = _render_dwg_from_dxf(dxf_files[0], target_layout, cache_key)
                    return result

        except Exception:
            pass

    return [PageResult(_make_error_image(
        "DWG プレビュー不可",
        "ODA File Converter が見つかりません。\n"
        "インストール後、「設定 > ODA File Converter 設定」で\n"
        "パスを登録してください。\n\n"
        "ダウンロード:\n"
        "https://www.opendesign.com/guestfiles/oda_file_converter",
    ))]


# ── SLDDRW プレビュー ─────────────────────────────────────────────────────
def preview_slddrw(
    file_path: str,
    config: Optional[dict] = None,
    highlight_text: str = "",
) -> List[PageResult]:
    config = config or {}

    if sys.platform == "win32":
        try:
            images = _slddrw_via_com(file_path)
            if images:
                results = []
                for i, img in enumerate(images):
                    pr = PageResult(image=img, layout_name=f"ページ {i + 1}")
                    results.append(pr)
                return results
        except Exception:
            pass

    return [PageResult(_make_error_image(
        "SLDDRW プレビュー制限",
        "SolidWorks プレビューには\n"
        "Windows + SolidWorks のインストールが必要です。\n\n"
        "テキスト検索結果は「詳細情報」パネルで確認できます。",
    ))]


def _slddrw_via_com(file_path: str) -> List[Image.Image]:
    import win32com.client as win32  # type: ignore
    sw = win32.Dispatch("SldWorks.Application")
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
        return [pr.image for pr in preview_pdf(pdf_path)]
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


# ── 統合エントリポイント ──────────────────────────────────────────────────
def get_preview(
    file_path: str,
    config: Optional[dict] = None,
    max_pages: int = 20,
    highlight_text: str = "",
    target_layout: Optional[str] = None,
    target_page: Optional[int] = None,
) -> List[PageResult]:
    """
    ファイル形式を自動判別してプレビュー結果リストを返す。
    同一ファイル+mtimeはLRUキャッシュから即座に返す。

    引数:
      target_layout : DXF/DWG のレイアウト名を指定すると、そのレイアウト1枚のみを返す。
                      None の場合は全レイアウトを返す（max_pages 上限あり）。
      target_page   : PDF のページ番号 (0始まり) を指定すると、そのページ1枚のみを返す。
                      None の場合は全ページを返す（max_pages 上限あり）。

    戻り値: list[PageResult]  (常に 1 件以上)
      .image       : PIL.Image.Image (RGB)
      .hit_boxes   : list[(x1,y1,x2,y2)]  常に空リスト (ハイライト機能削除済み)
      .layout_name : str
    """
    config = config or {}
    ext    = os.path.splitext(file_path)[1].lower()

    if not os.path.isfile(file_path):
        return [PageResult(_make_error_image("ファイルが見つかりません",
                                              file_path))]

    # ── キャッシュ確認 ────────────────────────────────────────────────
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = 0.0

    # target_layout/target_page が指定された場合はキャッシュキーに含める
    cache_key_extra = (target_layout, target_page)
    cache_key = (file_path, mtime, cache_key_extra)
    cached = _PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # target 未指定の場合は従来キーも確認（全ページキャッシュを流用）
    if target_layout is None and target_page is None:
        cached = _cache_get(file_path, mtime)
        if cached is not None:
            return cached

    # ── レンダリング ──────────────────────────────────────────────────
    if ext == ".pdf":
        page_numbers = [target_page] if target_page is not None else None
        pages = preview_pdf(file_path, page_numbers=page_numbers)
    elif ext == ".dxf":
        layout_names = [target_layout] if target_layout is not None else None
        pages = preview_dxf(file_path, layout_names=layout_names)
    elif ext == ".dwg":
        # target_layout を直接 preview_dwg に渡す（不要なレイアウトのレンダリングをスキップ）
        pages = preview_dwg(file_path, config, target_layout=target_layout)
    elif ext == ".slddrw":
        pages = preview_slddrw(file_path, config)
        if target_page is not None and len(pages) > 1:
            pages = [pages[target_page]] if target_page < len(pages) else pages[:1]
    else:
        pages = [PageResult(_make_error_image(
            "未対応形式", f"拡張子 '{ext}' には対応していません。"))]

    # RGB 正規化
    for pr in pages:
        pr.image = _rgba_to_rgb(pr.image)

    pages = pages[:max_pages]

    # ── キャッシュ保存 ────────────────────────────────────────────────
    if target_layout is None and target_page is None:
        _cache_put(file_path, mtime, pages)
    else:
        # ページ指定時は専用キーで保存（全ページキャッシュとは別管理）
        if len(_PREVIEW_CACHE) >= _CACHE_MAX:
            oldest = next(iter(_PREVIEW_CACHE))
            del _PREVIEW_CACHE[oldest]
        _PREVIEW_CACHE[cache_key] = pages

    return pages
