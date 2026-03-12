@echo off
REM ============================================================
REM setup.bat — Drawing Number Search App Windows セットアップ
REM ============================================================

cd /d "%~dp0"
echo ================================================================
echo   図面番号検索アプリ  Drawing Number Search -- Setup (Windows)
echo ================================================================
echo.

REM ── Python check ──────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python が見つかりません。
    echo         https://www.python.org/downloads/ からインストールしてください。
    echo         ※ インストール時に "Add Python to PATH" を必ずチェックしてください。
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do echo [CHECK] %%i

REM ── pip 最新化 ────────────────────────────────────────────────
echo.
echo [STEP 1] pip を最新化しています...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [WARN] pip の更新に失敗しましたが続行します。
)

REM ── 依存ライブラリ インストール ───────────────────────────────
echo.
echo [STEP 2] 依存ライブラリをインストールしています...
echo          (PyMuPDF / ezdxf / matplotlib / Pillow)
echo.
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] ライブラリのインストールに失敗しました。
    echo.
    echo  よくある原因:
    echo    1. インターネット接続を確認してください。
    echo    2. プロキシ環境の場合:
    echo       python -m pip install --proxy http://user:pass@host:port -r requirements.txt
    echo    3. 管理者権限が必要な場合はこのファイルを右クリック→管理者として実行。
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] 全ライブラリのインストールが完了しました。

REM ── インストール確認 ──────────────────────────────────────────
echo.
echo [STEP 3] インストール確認中...
python -c "import fitz;       print('[OK] PyMuPDF   :', fitz.__version__)"
if errorlevel 1 echo [NG] PyMuPDF のインポートに失敗しました。

python -c "import ezdxf;      print('[OK] ezdxf     :', ezdxf.__version__)"
if errorlevel 1 echo [NG] ezdxf のインポートに失敗しました。

python -c "import matplotlib; print('[OK] matplotlib :', matplotlib.__version__)"
if errorlevel 1 echo [NG] matplotlib のインポートに失敗しました。

python -c "import PIL;        print('[OK] Pillow     :', PIL.__version__)"
if errorlevel 1 echo [NG] Pillow のインポートに失敗しました。

python -c "import tkinter;    print('[OK] tkinter    : OK')"
if errorlevel 1 (
    echo [NG] tkinter が見つかりません。
    echo      Python 公式インストーラで再インストールしてください。
)

REM ── ODA File Converter 確認 ───────────────────────────────────
echo.
echo [STEP 4] ODA File Converter 確認 (DWGファイル解析用)...
where ODAFileConverter >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe" (
        echo [OK]   ODAFileConverter: C:\Program Files\ODA\ODAFileConverter\
    ) else (
        echo [INFO] ODA File Converter が見つかりません ^(DWGなしなら不要^)
        echo        DWGファイルを使う場合はこちらからダウンロード ^(無料^):
        echo        https://www.opendesign.com/guestfiles/oda_file_converter
        echo        インストール後、アプリの「設定 ^> ODA設定」でパスを登録してください。
    )
) else (
    echo [OK]   ODAFileConverter が PATH 上に見つかりました。
)

REM ── ランチャー作成 ────────────────────────────────────────────
echo.
echo [STEP 5] 起動スクリプトを作成しています...

(
    echo @echo off
    echo cd /d "%%~dp0"
    echo python -m src gui
    echo if errorlevel 1 pause
) > run_gui.bat

(
    echo @echo off
    echo cd /d "%%~dp0"
    echo python -m src %%*
) > run_cli.bat

echo [OK]   run_gui.bat  -- GUIアプリ起動
echo [OK]   run_cli.bat  -- CLI使用例: run_cli.bat search "DRW-001"

REM ── サンプルファイル生成 ──────────────────────────────────────
echo.
echo [STEP 6] テスト用サンプルファイルを生成しています...
python tests\create_samples.py
if errorlevel 1 echo [WARN] サンプル生成に失敗しましたが、アプリ自体は使用できます。

echo.
echo ================================================================
echo   セットアップ完了！  Setup Complete!
echo ================================================================
echo.
echo   [GUI起動]     run_gui.bat をダブルクリック
echo                 または: python -m src gui
echo.
echo   [CLI使用]     run_cli.bat search "DRW-001"
echo                 または: python -m src --help
echo.
echo   [テスト実行]  python -m pytest tests\ -v
echo.
echo   クイックスタート:
echo     1. run_gui.bat でアプリを起動
echo     2. 「フォルダ登録」で図面ファイルのフォルダを選択
echo     3. 図面番号を入力して「検索」ボタンを押す
echo.
pause
