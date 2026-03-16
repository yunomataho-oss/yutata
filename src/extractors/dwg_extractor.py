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

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional

logger = logging.getLogger(__name__)

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


def set_odafc_path(oda_exe: str) -> None:
    """
    ezdxf.options 経由で ODA 実行パスを設定する。

    ezdxf 1.x では odafc.unix_exec_path / odafc.win_exec_path への
    直接代入は効かない。ezdxf.options.set() が唯一の正式な設定方法。
    この関数を呼ぶことで odafc.is_installed() / odafc.readfile() が
    カスタムパスを認識するようになる。
    """
    try:
        import ezdxf
        if sys.platform == "win32":
            ezdxf.options.set("odafc-addon", "win_exec_path",  oda_exe)
        else:
            ezdxf.options.set("odafc-addon", "unix_exec_path", oda_exe)
    except Exception:
        pass


def _ensure_display_env() -> dict:
    """
    Linux/macOS で ODA を実行する際に DISPLAY 環境変数を保証する。

    - DISPLAY が既に設定されている → そのまま返す
    - Xvfb が使えるが DISPLAY 未設定 → ':99' を設定
    - どちらもなければ ':0' を仮設定（失敗しても ODA 側でエラーになる）

    戻り値: 子プロセスに渡す env dict (None なら現在の環境をそのまま)
    """
    if sys.platform == "win32":
        return os.environ.copy()

    env = os.environ.copy()
    if not env.get("DISPLAY"):
        # DISPLAY が未設定の場合は安全な値を設定する
        # (ezdxf の _linux_dummy_display は os.environ["DISPLAY"] を参照するため
        #  KeyError を防ぐ)
        display_candidate = ":99" if shutil.which("Xvfb") else ":0"
        env["DISPLAY"] = display_candidate
        logger.debug("DISPLAY not set; defaulting to %s for ODA", display_candidate)
    return env


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
            # ODA パスを ezdxf.options に設定（odafc.readfile が参照する）
            set_odafc_path(oda_exe)

            # --- 方法 1: ezdxf.addons.odafc を試みる ---
            result = self._extract_via_ezdxf_odafc(file_path, oda_exe)
            if result.error:
                # ezdxf アダプターが失敗した場合は CLI に切り替え
                logger.debug("odafc method failed (%s), falling back to CLI",
                             result.error[:120])
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

        Windows 固有の注意点:
          - ezdxf の _odafc_failed は returncode != 0 または stderr != "" で失敗と判定する。
            ODA が情報メッセージを stderr に出すだけで UnknownODAFCError になることがある。
            そのため audit=True でエラーになった場合は audit=False でリトライする。

        Linux/macOS 固有の注意点:
          - _linux_dummy_display が os.environ["DISPLAY"] を参照するため DISPLAY 未設定で
            KeyError が発生する。_ensure_display_env() で事前に設定する。
        """
        result = ExtractionResult(file_path=file_path, file_type="dwg")
        try:
            import ezdxf
            from ezdxf.addons import odafc

            audit_flag = bool(self.config.get("oda_audit", True))

            # Linux/macOS: DISPLAY 環境変数を一時設定
            env = _ensure_display_env()
            old_display = os.environ.get("DISPLAY")
            try:
                if sys.platform != "win32" and not os.environ.get("DISPLAY"):
                    os.environ["DISPLAY"] = env["DISPLAY"]

                # DWG → メモリ上の DXF Document
                try:
                    doc = odafc.readfile(file_path, audit=audit_flag)
                except Exception as first_exc:
                    # Windows: audit=True で stderr に出力がありエラー判定された場合
                    # audit=False でリトライする
                    if audit_flag and sys.platform == "win32":
                        logger.debug(
                            "odafc readfile failed with audit=True (%s), retrying with audit=False",
                            first_exc
                        )
                        doc = odafc.readfile(file_path, audit=False)
                    else:
                        raise
            finally:
                # DISPLAY 環境変数を元に戻す
                if sys.platform != "win32":
                    if old_display is None:
                        os.environ.pop("DISPLAY", None)
                    else:
                        os.environ["DISPLAY"] = old_display

            # 以降は DXF と同じ処理（レイアウト別セクション形式で保存）
            from .dxf_extractor import _collect_texts_from_layout
            texts: List[str] = []
            layout_sections: List[str] = []

            model_texts = _collect_texts_from_layout(doc.modelspace())
            if model_texts:
                layout_sections.append("[Model]")
                layout_sections.extend(model_texts)
            texts.extend(model_texts)

            for layout in doc.layouts:
                if layout.name != "Model":
                    lt = _collect_texts_from_layout(layout)
                    if lt:
                        layout_sections.append(f"[{layout.name}]")
                        layout_sections.extend(lt)
                    texts.extend(lt)

            for block in doc.blocks:
                texts.extend(_collect_texts_from_layout(block))

            result.texts = list(dict.fromkeys(t for t in texts if t.strip()))
            result.drawing_numbers = _extract_drawing_numbers(result.texts)
            result.texts_blob_sections = "\n".join(layout_sections)
            result.raw_metadata = {
                "dxf_version":     doc.dxfversion,
                "converted_via":   "ezdxf.addons.odafc",
                "oda_executable":  oda_exe,
            }

        except ImportError:
            result.error = "odafc_import_error: ezdxf not installed"
        except Exception as exc:
            logger.debug("odafc extract failed for %s: %s", file_path, exc)
            result.error = f"odafc_error: {exc}"

        return result

    # ------------------------------------------------------------------
    # 方法 2: ODA File Converter CLI 直接呼び出し → DXF → ezdxf 解析
    # ------------------------------------------------------------------
    def _run_oda_cli(
        self,
        oda_exe: str,
        tmp_in: str,
        tmp_out: str,
        version: str,
        audit_flag: str,
    ) -> tuple:
        """
        ODA File Converter を実際に起動してコマンド結果を返す。
        戻り値: (returncode, stdout_log, stderr_log)

        Windows 固有対応:
          - subprocess.run の text=True は Windows でエンコードエラーの原因になるため
            bytes モードで受け取り、複数エンコードで decode を試みる。
          - CREATE_NO_WINDOW フラグでコンソールウィンドウのポップアップを抑制する。
        """
        cmd = [oda_exe, tmp_in, tmp_out, version, "DXF", "0", audit_flag]
        env = _ensure_display_env()

        if sys.platform != "win32":
            if shutil.which("xvfb-run"):
                cmd = ["xvfb-run", "-a"] + cmd

        # Windows: CREATE_NO_WINDOW でコンソール非表示
        kwargs: dict = dict(timeout=120, capture_output=True, env=env)
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        proc = subprocess.run(cmd, **kwargs)
        returncode = proc.returncode

        # stdout/stderr の安全な decode
        def _safe_decode(b: bytes) -> str:
            if not b:
                return ""
            for enc in ("utf-8", "cp932", "cp1252", "latin-1"):
                try:
                    return b.decode(enc)
                except (UnicodeDecodeError, AttributeError):
                    pass
            return b.decode("utf-8", errors="replace")

        stdout_log = _safe_decode(proc.stdout).strip()
        stderr_log = _safe_decode(proc.stderr).strip()
        logger.debug("ODA CLI rc=%d cmd=%s stdout=%s stderr=%s",
                     returncode, cmd[:4], stdout_log[:200], stderr_log[:200])
        return returncode, stdout_log, stderr_log

    def _extract_via_cli(self, file_path: str, oda_exe: str) -> ExtractionResult:
        """
        ODAFileConverter を CLI として呼び出す。
        コマンド形式:
          ODAFileConverter <入力フォルダ> <出力フォルダ> <バージョン> <形式> <再帰> <Audit>

        Windows 固有対応:
          - CREATE_NO_WINDOW でコンソールポップアップを抑制
          - bytes モードで出力を受け取り多重エンコード decode
          - audit=True で失敗した場合に audit=False でリトライ
          - サブディレクトリも含めて DXF ファイルを検索

        Linux ではヘッドレス環境でも動作するよう DISPLAY 環境変数を設定する。
        """
        result = ExtractionResult(file_path=file_path, file_type="dwg")
        version    = self.config.get("oda_version", "ACAD2018")
        audit_flag = "1" if self.config.get("oda_audit", True) else "0"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_in  = os.path.join(tmpdir, "input")
            tmp_out = os.path.join(tmpdir, "output")
            os.makedirs(tmp_in,  exist_ok=True)
            os.makedirs(tmp_out, exist_ok=True)

            # 入力ファイルを孤立ディレクトリにコピー
            shutil.copy2(file_path, tmp_in)

            try:
                returncode, stdout_log, stderr_log = self._run_oda_cli(
                    oda_exe, tmp_in, tmp_out, version, audit_flag
                )
            except subprocess.TimeoutExpired:
                result.error = "ODA File Converter がタイムアウトしました (120秒)"
                return result
            except Exception as exc:
                result.error = f"ODA CLI 起動エラー: {exc}"
                return result

            # DXF 出力を再帰的に探す（サブディレクトリも含む）
            dxf_files = []
            for root, _, files in os.walk(tmp_out):
                for f in files:
                    if f.lower().endswith(".dxf"):
                        dxf_files.append(os.path.join(root, f))

            # Windows で audit=1 が原因で失敗した場合は audit=0 でリトライ
            if not dxf_files and audit_flag == "1" and sys.platform == "win32":
                logger.debug("No DXF output with audit=1, retrying with audit=0")
                # 出力ディレクトリをクリアしてリトライ
                for f in os.listdir(tmp_out):
                    try:
                        os.remove(os.path.join(tmp_out, f))
                    except Exception:
                        pass
                try:
                    returncode, stdout_log, stderr_log = self._run_oda_cli(
                        oda_exe, tmp_in, tmp_out, version, "0"
                    )
                    for root, _, files in os.walk(tmp_out):
                        for f in files:
                            if f.lower().endswith(".dxf"):
                                dxf_files.append(os.path.join(root, f))
                except Exception:
                    pass

            if dxf_files:
                dxf_path   = dxf_files[0]
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
                # CLI は実行されたが DXF 出力なし → バイナリスキャンへ
                fallback = self._extract_binary_fallback(file_path)
                result.texts          = fallback.texts
                result.drawing_numbers = fallback.drawing_numbers
                result.raw_metadata   = {
                    "converted_via":  "binary_fallback (ODA 出力なし)",
                    "oda_executable": oda_exe,
                    "oda_stdout":     stdout_log[:500],
                    "oda_stderr":     stderr_log[:500],
                }
                result.error = (
                    "ODA File Converter は検出されましたが DXF を出力しませんでした。\n"
                    f"ODA 実行ファイル: {oda_exe}\n"
                    f"stderr: {stderr_log[:300]}\n\n"
                    "【Windows の場合の対処法】\n"
                    "・設定バージョンを変更する (ACAD2018 → ACAD2013 など)\n"
                    "・管理者権限でアプリを実行する\n"
                    "・ODA の最新版をインストールする\n"
                    "  https://www.opendesign.com/guestfiles/oda_file_converter\n"
                    "【Linux の場合の対処法】\n"
                    "・sudo apt install xvfb でインストール後に再試行\n"
                    "・export DISPLAY=:0 (X Server 起動中の場合)"
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
