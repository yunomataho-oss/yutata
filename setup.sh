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

# ── Python version check ─────────────────────────────────────────────────────
PY=$(python3 --version 2>&1 || echo "not found")
echo "[CHECK] Python: $PY"
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 が見つかりません。Python 3.9 以上をインストールしてください。"
    exit 1
fi

# ── pip install ───────────────────────────────────────────────────────────────
echo ""
echo "[STEP 1] Installing Python dependencies..."
python3 -m pip install --upgrade pip --quiet
python3 -m pip install -r requirements.txt

# ── Optional: pytest for tests ────────────────────────────────────────────────
echo ""
echo "[STEP 2] Installing test dependencies (optional)..."
python3 -m pip install pytest --quiet || true

# ── Check tkinter ─────────────────────────────────────────────────────────────
echo ""
echo "[STEP 3] Checking tkinter..."
python3 -c "import tkinter; print('[OK] tkinter is available')" 2>/dev/null || \
    echo "[WARN] tkinter が見つかりません。GUI を使う場合は以下を実行:"
echo "       Ubuntu/Debian: sudo apt-get install python3-tk"
echo "       Fedora/RHEL:   sudo dnf install python3-tkinter"
echo "       macOS:         brew install python-tk"

# ── ODA File Converter check ──────────────────────────────────────────────────
echo ""
echo "[INFO] ODA File Converter (DWG解析用)"
if command -v ODAFileConverter &>/dev/null; then
    echo "[OK]   ODAFileConverter が見つかりました: $(command -v ODAFileConverter)"
else
    echo "[WARN] ODA File Converter が見つかりません。"
    echo "       DWGファイルを正確に解析するには以下からダウンロードしてください:"
    echo "       https://www.opendesign.com/guestfiles/oda_file_converter"
    echo "       (なくても動作しますが、DWGはバイナリスキャン限定になります)"
fi

# ── Create launcher scripts ───────────────────────────────────────────────────
echo ""
echo "[STEP 4] Creating launcher scripts..."

# Linux / macOS launcher
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
echo "[OK]   run_cli.sh  — CLIを使用 (例: ./run_cli.sh search 'DRW-001')"

# ── Generate sample files ─────────────────────────────────────────────────────
echo ""
echo "[STEP 5] Generating sample drawing files for testing..."
python3 tests/create_samples.py || true

echo ""
echo "================================================================"
echo "  セットアップ完了！  Setup Complete!"
echo "================================================================"
echo ""
echo "  GUI 起動:   ./run_gui.sh"
echo "  CLI 使用:   python3 -m src --help"
echo "  テスト実行: pytest tests/ -v"
echo ""
echo "  クイックスタート:"
echo "  1. ./run_gui.sh でアプリを起動"
echo "  2. 「📂 フォルダ登録」で図面ファイルのフォルダを選択"
echo "  3. 図面番号を入力して「検索」ボタンを押す"
echo ""
