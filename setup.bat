@echo off
REM ============================================================
REM setup.bat — Drawing Number Search App Windows セットアップ
REM ============================================================

cd /d "%~dp0"
echo ================================================================
echo   図面番号検索アプリ  Drawing Number Search -- Setup (Windows)
echo ================================================================
echo.

REM ── Python check ─────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo         https://www.python.org/downloads/ からインストールしてください。
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do echo [CHECK] %%i

REM ── pip install ───────────────────────────────────────────────────────────
echo.
echo [STEP 1] Installing Python dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt

REM ── pytest (optional) ─────────────────────────────────────────────────────
echo.
echo [STEP 2] Installing test dependencies (optional)...
python -m pip install pytest --quiet

REM ── tkinter check ─────────────────────────────────────────────────────────
echo.
echo [STEP 3] Checking tkinter...
python -c "import tkinter; print('[OK] tkinter available')" 2>nul || (
    echo [WARN] tkinter が見つかりません。
    echo        Python 公式インストーラでは標準で含まれています。
)

REM ── ODA File Converter check ──────────────────────────────────────────────
echo.
echo [INFO] ODA File Converter (DWG解析用)
where ODAFileConverter >nul 2>&1
if errorlevel 1 (
    echo [WARN] ODA File Converter が見つかりません。
    echo        DWGファイルを正確に解析するには以下からダウンロードしてください:
    echo        https://www.opendesign.com/guestfiles/oda_file_converter
) else (
    echo [OK]   ODAFileConverter が見つかりました。
)

REM ── Launcher scripts ──────────────────────────────────────────────────────
echo.
echo [STEP 4] Creating launcher scripts...

echo @echo off > run_gui.bat
echo cd /d "%%~dp0" >> run_gui.bat
echo python -m src gui >> run_gui.bat

echo @echo off > run_cli.bat
echo cd /d "%%~dp0" >> run_cli.bat
echo python -m src %%* >> run_cli.bat

echo [OK]   run_gui.bat  -- GUI起動
echo [OK]   run_cli.bat  -- CLI使用

REM ── Sample files ──────────────────────────────────────────────────────────
echo.
echo [STEP 5] Generating sample drawing files...
python tests\create_samples.py

echo.
echo ================================================================
echo   セットアップ完了！  Setup Complete!
echo ================================================================
echo.
echo   GUI 起動:   run_gui.bat
echo   CLI 使用:   python -m src --help
echo   テスト実行: pytest tests\ -v
echo.
echo   クイックスタート:
echo   1. run_gui.bat でアプリを起動
echo   2. 「フォルダ登録」で図面ファイルのフォルダを選択
echo   3. 図面番号を入力して「検索」ボタンを押す
echo.
pause
