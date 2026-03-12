"""
DWG extractor — ODA File Converter 完全対応版
=============================================

連携戦略（優先順）:
  1. ezdxf.addons.odafc  (ezdxf 組み込みアダプター — 最も簡単)
  2. ODAFileConverter CLI 直接呼び出し (フォールバック)
  3. バイナリスキャン (ODA 未インストール時の最終手段)

ODA File Converter のインストール方法:
  Windows : https://www.opendesign.com/guestfiles/oda_file_converter
            デフォルト: C:\\Program Files\\ODA\\ODAFileConverter\\ODAFileConverter.exe
  Linux   : .deb/.rpm/.AppImage → sudo gdebi ODA*.deb  など
  macOS   : .dmg をマウントしてインストール
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional

from .base_extractor import BaseExtractor, ExtractionResult
from .dxf_extractor import DxfExtractor, _extract_drawing_numbers

# ── ODA 実行ファイルの候補パス ──────────────────────────────────────────────
_ODA_WIN_PATHS = [
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
    r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
]
_ODA_UNIX_COMMANDS = [
    "ODAFileConverter",          # PATH が通っている場合
]


def find_oda_executable(custom_path: str = "") -> Optional[str]:
    """
    ODA File Converter の実行ファイルパスを返す。
    見つからない場合は None。

    custom_path を指定するとそちらを最優先で確認する。
    """
    # 1) ユーザー指定パス
    if custom_path and os.path.isfile(custom_path):
        return custom_path

    # 2) Windows 既定インストール先
    if sys.platform == "win32":
        for p in _ODA_WIN_PATHS:
            if os.path.isfile(p):
                return p

    # 3) PATH 上のコマンド (Linux / macOS / Windows PATH)
    for cmd in _ODA_UNIX_COMMANDS:
        found = shutil.which(cmd)
        if found:
            return found

    # 4) AppImage / 任意パス (設定ファイル経由で渡された場合はカバー済み)
    return None


def is_oda_installed(custom_path: str = "") -> bool:
    """ODA File Converter がインストールされているか確認する。"""
    return find_oda_executable(custom_path) is not None


# ── バイナリスキャン (フォールバック) ─────────────────────────────────────
def _binary_text_scan(file_path: str) -> List[str]:
    """ASCII/UTF-16 文字列を DWG バイナリから抽出する簡易スキャン。"""
    texts: List[str] = []
    try:
        with open(file_path, "rb") as fh:
            data = fh.read()

        # ASCII 印刷可能文字の連続 (4 文字以上)
        for m in re.finditer(rb'[ -~]{4,}', data):
            try:
                decoded = m.group().decode("ascii", errors="ignore").strip()
                if decoded:
                    texts.append(decoded)
            except Exception:
                pass

        # UTF-16 LE スキャン (Windows 向け DWG に多い)
        try:
            text_utf16 = data.decode("utf-16-le", errors="ignore")
            for run in re.findall(r'[\u0020-\u007E\u3000-\u9FFF]{3,}', text_utf16):
                texts.append(run.strip())
        except Exception:
            pass

    except Exception:
        pass
    return [t for t in texts if t]


# ── メインエクストラクター ─────────────────────────────────────────────────
class DwgExtractor(BaseExtractor):
    """
    DWG ファイルからテキスト・図面番号を抽出する。

    config キー:
        "oda_path"    : ODA File Converter 実行ファイルの絶対パス (省略可)
        "oda_version" : 変換先 DXF バージョン文字列 (デフォルト "ACAD2018")
        "oda_audit"   : 変換時に Audit を実行するか (デフォルト True)
    """

    def extract(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="dwg")

        if not self._safe_read(file_path):
            result.error = f"ファイルが見つかりません: {file_path}"
            return result

        custom_oda = self.config.get("oda_path", "")
        oda_exe    = find_oda_executable(custom_oda)

        if oda_exe:
            # --- 方法 1: ezdxf.addons.odafc を試みる ---
            result = self._extract_via_ezdxf_odafc(file_path, oda_exe)
            if result.error and "odafc" in result.error.lower():
                # ezdxf アダプターが失敗した場合は CLI に切り替え
                result = self._extract_via_cli(file_path, oda_exe)
        else:
            # --- 方法 3: バイナリスキャン ---
            result = self._extract_binary_fallback(file_path)

        return result

    # ------------------------------------------------------------------
    # 方法 1: ezdxf 組み込みアダプター (ezdxf.addons.odafc)
    # ------------------------------------------------------------------
    def _extract_via_ezdxf_odafc(self, file_path: str, oda_exe: str) -> ExtractionResult:
        """
        ezdxf.addons.odafc を使って DWG を直接 ezdxf Document として読み込む。
        これが最も信頼性が高くコードもシンプル。
        """
        result = ExtractionResult(file_path=file_path, file_type="dwg")
        try:
            import ezdxf
            from ezdxf.addons import odafc

            # ODA 実行パスを ezdxf に教える
            if sys.platform == "win32":
                odafc.win_exec_path = oda_exe
            else:
                odafc.unix_exec_path = oda_exe

            # DWG → メモリ上の DXF Document
            doc = odafc.readfile(file_path)

            # 以降は DXF と同じ処理
            from .dxf_extractor import _collect_texts_from_layout
            texts: List[str] = []

            texts.extend(_collect_texts_from_layout(doc.modelspace()))
            for layout in doc.layouts:
                if layout.name != "Model":
                    texts.extend(_collect_texts_from_layout(layout))
            for block in doc.blocks:
                texts.extend(_collect_texts_from_layout(block))

            result.texts = list(dict.fromkeys(t for t in texts if t.strip()))
            result.drawing_numbers = _extract_drawing_numbers(result.texts)
            result.raw_metadata = {
                "dxf_version":     doc.dxfversion,
                "converted_via":   "ezdxf.addons.odafc",
                "oda_executable":  oda_exe,
            }

        except ImportError:
            result.error = "odafc_import_error: ezdxf not installed"
        except Exception as exc:
            result.error = f"odafc_error: {exc}"

        return result

    # ------------------------------------------------------------------
    # 方法 2: ODA File Converter CLI 直接呼び出し → DXF → ezdxf 解析
    # ------------------------------------------------------------------
    def _extract_via_cli(self, file_path: str, oda_exe: str) -> ExtractionResult:
        """
        ODAFileConverter を CLI として呼び出す。
        コマンド形式:
          ODAFileConverter <入力フォルダ> <出力フォルダ> <バージョン> <形式> <再帰> <Audit>
        """
        result = ExtractionResult(file_path=file_path, file_type="dwg")
        version = self.config.get("oda_version", "ACAD2018")
        audit   = "1" if self.config.get("oda_audit", True) else "0"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_in  = os.path.join(tmpdir, "input")
            tmp_out = os.path.join(tmpdir, "output")
            os.makedirs(tmp_in,  exist_ok=True)
            os.makedirs(tmp_out, exist_ok=True)

            # 入力ファイルを孤立ディレクトリにコピー
            shutil.copy2(file_path, tmp_in)

            cmd = [oda_exe, tmp_in, tmp_out, version, "DXF", "0", audit]

            # Linux で GUI が出ないよう xvfb-run を利用 (インストール済みなら)
            if sys.platform != "win32" and shutil.which("xvfb-run"):
                cmd = ["xvfb-run", "-a"] + cmd

            try:
                proc = subprocess.run(
                    cmd,
                    timeout=120,
                    capture_output=True,
                    text=True,
                )
                stderr_log = proc.stderr.strip() if proc.stderr else ""
                stdout_log = proc.stdout.strip() if proc.stdout else ""
            except subprocess.TimeoutExpired:
                result.error = "ODA File Converter がタイムアウトしました (120秒)"
                return result
            except Exception as exc:
                result.error = f"ODA CLI 起動エラー: {exc}"
                return result

            # DXF 出力を探す
            dxf_files = [f for f in os.listdir(tmp_out) if f.lower().endswith(".dxf")]

            if dxf_files:
                dxf_path   = os.path.join(tmp_out, dxf_files[0])
                dxf_result = DxfExtractor(self.config).extract(dxf_path)
                result.texts          = dxf_result.texts
                result.drawing_numbers = dxf_result.drawing_numbers
                result.title          = dxf_result.title
                result.raw_metadata   = {
                    "dxf_version":    dxf_result.raw_metadata.get("dxf_version", ""),
                    "converted_via":  "ODA File Converter CLI",
                    "oda_version":    version,
                    "oda_executable": oda_exe,
                    "oda_stdout":     stdout_log[:500],
                    "oda_stderr":     stderr_log[:500],
                }
                if dxf_result.error:
                    result.error = dxf_result.error
            else:
                # CLI は成功したが出力なし → バイナリスキャンへ
                fallback = self._extract_binary_fallback(file_path)
                result.texts          = fallback.texts
                result.drawing_numbers = fallback.drawing_numbers
                result.raw_metadata   = {
                    "converted_via": "binary_fallback (ODA 出力なし)",
                    "oda_stdout":    stdout_log[:500],
                    "oda_stderr":    stderr_log[:500],
                }
                result.error = (
                    f"ODA File Converter が DXF を出力しませんでした。"
                    f"\nstderr: {stderr_log[:200]}"
                )

        return result

    # ------------------------------------------------------------------
    # 方法 3: バイナリスキャン (最終フォールバック)
    # ------------------------------------------------------------------
    def _extract_binary_fallback(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(file_path=file_path, file_type="dwg")
        texts  = _binary_text_scan(file_path)
        result.texts          = texts
        result.drawing_numbers = _extract_drawing_numbers(texts)
        result.raw_metadata   = {"converted_via": "binary_scan_fallback"}
        result.error = (
            "ODA File Converter が見つかりません。バイナリスキャンで解析しました（精度低）。\n"
            "正確な解析には ODA File Converter をインストールしてください:\n"
            "https://www.opendesign.com/guestfiles/oda_file_converter"
        )
        return result
