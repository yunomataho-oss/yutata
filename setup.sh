#!/usr/bin/env bash
# ============================================================
# setup.sh — Drawing Number Search App セットアップスクリプト
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================"
echo "  図面番号検索アプリ  Drawing Number Search — Setup"
echo "================================================================"
echo ""

# ── Python version check ──────────────────────────────────────────
PY=$(python3 --version 2>&1 || echo "not found")
echo "[CHECK] Python: $PY"
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 が見つかりません。Python 3.9 以上をインストールしてください。"
    exit 1
fi

# ── pip 最新化 ────────────────────────────────────────────────────
echo ""
echo "[STEP 1] pip を最新化しています..."
python3 -m pip install --upgrade pip --quiet || true

# ── 依存ライブラリ インストール ───────────────────────────────────
echo ""
echo "[STEP 2] 依存ライブラリをインストールしています..."
echo "         (PyMuPDF / ezdxf / matplotlib / Pillow)"
echo ""
if ! python3 -m pip install -r requirements.txt; then
    echo ""
    echo "[ERROR] ライブラリのインストールに失敗しました。"
    echo "        インターネット接続を確認してください。"
    echo "        プロキシ環境の場合:"
    echo "          pip install --proxy http://user:pass@host:port -r requirements.txt"
    exit 1
fi

# ── インストール確認 ──────────────────────────────────────────────
echo ""
echo "[STEP 3] インストール確認中..."
python3 -c "import fitz;       print('[OK] PyMuPDF   :', fitz.__version__)"       || echo "[NG] PyMuPDF"
python3 -c "import ezdxf;      print('[OK] ezdxf     :', ezdxf.__version__)"      || echo "[NG] ezdxf"
python3 -c "import matplotlib; print('[OK] matplotlib:', matplotlib.__version__)" || echo "[NG] matplotlib"
python3 -c "import PIL;        print('[OK] Pillow     :', PIL.__version__)"        || echo "[NG] Pillow"
python3 -c "import tkinter;    print('[OK] tkinter    : OK')" 2>/dev/null || {
    echo "[WARN] tkinter が見つかりません。GUIを使う場合は以下を実行:"
    echo "       Ubuntu/Debian: sudo apt-get install python3-tk"
    echo "       Fedora/RHEL:   sudo dnf install python3-tkinter"
    echo "       macOS:         brew install python-tk"
}

# ── pytest ────────────────────────────────────────────────────────
echo ""
echo "[STEP 4] pytest をインストールしています (テスト用・任意)..."
python3 -m pip install pytest --quiet || true

# ── ODA File Converter 確認 ───────────────────────────────────────
echo ""
echo "[STEP 5] ODA File Converter 確認 (DWGファイル解析用)..."
if command -v ODAFileConverter &>/dev/null; then
    echo "[OK]   ODAFileConverter: $(command -v ODAFileConverter)"
else
    echo "[INFO] ODA File Converter が見つかりません (DWGなしなら不要)"
    echo "       DWGファイルを使う場合はこちらからダウンロード (無料):"
    echo "       https://www.opendesign.com/guestfiles/oda_file_converter"
    echo "       インストール後、アプリの「設定 > ODA設定」でパスを登録してください。"
fi

# ── ランチャー作成 ────────────────────────────────────────────────
echo ""
echo "[STEP 6] 起動スクリプトを作成しています..."

cat > run_gui.sh << 'LAUNCHER'
#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m src gui
LAUNCHER
chmod +x run_gui.sh

cat > run_cli.sh << 'LAUNCHER'
#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m src "$@"
LAUNCHER
chmod +x run_cli.sh

echo "[OK]   run_gui.sh  — GUIを起動"
echo "[OK]   run_cli.sh  — CLI使用 (例: ./run_cli.sh search 'DRW-001')"

# ── サンプルファイル生成 ──────────────────────────────────────────
echo ""
echo "[STEP 7] テスト用サンプルファイルを生成しています..."
python3 tests/create_samples.py || echo "[WARN] サンプル生成に失敗しましたが、アプリ自体は使用できます。"

echo ""
echo "================================================================"
echo "  セットアップ完了！  Setup Complete!"
echo "================================================================"
echo ""
echo "  [GUI起動]     ./run_gui.sh"
echo "                または: python3 -m src gui"
echo ""
echo "  [CLI使用]     ./run_cli.sh search 'DRW-001'"
echo "                または: python3 -m src --help"
echo ""
echo "  [テスト実行]  python3 -m pytest tests/ -v"
echo ""
echo "  クイックスタート:"
echo "    1. ./run_gui.sh でアプリを起動"
echo "    2. 「フォルダ登録」で図面ファイルのフォルダを選択"
echo "    3. 図面番号を入力して「検索」ボタンを押す"
echo ""
