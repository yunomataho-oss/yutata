"""
Command-line interface for Drawing Number Search.
Usage:
    python -m src search "DRW-12345"
    python -m src index ./drawings
    python -m src index ./drawings --no-recursive
    python -m src list
    python -m src gui
    python -m src oda-check
    python -m src oda-set "C:/Program Files/ODA/ODAFileConverter/ODAFileConverter.exe"
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def cmd_gui(args):
    """Launch the GUI application."""
    from src.gui.app import main as gui_main
    gui_main()


def cmd_index(args):
    from src.search.search_engine import DrawingSearchEngine

    engine = DrawingSearchEngine()
    path = os.path.abspath(args.path)

    if os.path.isfile(path):
        print(f"[INFO] Indexing file: {path}")
        try:
            entry = engine.index_file(path)
            engine._save_index()
            print(f"[OK]  {entry.filename}")
            if entry.drawing_numbers:
                print(f"      Drawing numbers: {', '.join(entry.drawing_numbers)}")
            if entry.error:
                print(f"[WARN] {entry.error}")
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
    elif os.path.isdir(path):
        recursive = not args.no_recursive
        print(f"[INFO] Indexing directory: {path}  (recursive={recursive})")

        total_files = [0]

        def progress(fpath, current, total):
            total_files[0] = total
            print(f"\r[{current:4d}/{total:4d}] {os.path.basename(fpath):<50}", end="", flush=True)

        indexed, errors = engine.index_directory(path, recursive=recursive, progress_callback=progress)
        print()
        print(f"[DONE] Indexed: {indexed}  Errors: {errors}  Total in index: {engine.index_size}")
    else:
        print(f"[ERROR] Path not found: {path}", file=sys.stderr)
        sys.exit(1)


def cmd_search(args):
    from src.search.search_engine import DrawingSearchEngine

    engine = DrawingSearchEngine()
    results = engine.search(
        args.query,
        match_type=args.match_type,
        case_sensitive=args.case_sensitive,
        use_regex=args.regex,
    )

    if not results:
        print(f"No results found for: {args.query}")
        return

    print(f"\n{'─'*80}")
    print(f"  {len(results)} result(s) for '{args.query}'")
    print(f"{'─'*80}")

    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r.entry.filename}")
        print(f"    Path         : {r.entry.file_path}")
        print(f"    Type         : {r.entry.file_type.upper()}")
        print(f"    Drawing Nos  : {', '.join(r.entry.drawing_numbers) or '—'}")
        print(f"    Match        : {r.match_type}  →  {r.matched_value[:80]}")
        print(f"    Score        : {r.score:.2f}")
        if r.entry.error:
            print(f"    ⚠ Warning   : {r.entry.error}")

    if args.json:
        import dataclasses
        output = []
        for r in results:
            d = dataclasses.asdict(r.entry)
            d["match_type"]    = r.match_type
            d["matched_value"] = r.matched_value
            d["score"]         = r.score
            output.append(d)
        print("\n─── JSON Output ───")
        print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_list(args):
    from src.search.search_engine import DrawingSearchEngine

    engine = DrawingSearchEngine()
    entries = engine.indexed_files

    if not entries:
        print("Index is empty. Use 'index <path>' to add files.")
        return

    print(f"\n{'─'*90}")
    print(f"  {len(entries)} file(s) in index")
    print(f"{'─'*90}")
    for e in entries:
        dn = ", ".join(e.drawing_numbers) if e.drawing_numbers else "—"
        print(f"  [{e.file_type.upper():7s}] {e.filename:<45} {dn}")


def cmd_oda_check(args):
    """ODA File Converter の検出状態を診断する。"""
    from src.extractors.dwg_extractor import find_oda_executable, is_oda_installed
    from src.utils.helpers import load_config
    import shutil, subprocess

    config      = load_config()
    custom_path = config.get("oda_path", "")
    oda_exe     = find_oda_executable(custom_path)

    print("\n──────────────────────────────────────────────")
    print("  ODA File Converter 診断レポート")
    print("──────────────────────────────────────────────")

    if oda_exe:
        print(f"✅ 検出: {oda_exe}")
        # バージョン確認 (--version オプションが使えるかどうかは実装依存)
        try:
            r = subprocess.run([oda_exe, "--version"],
                               capture_output=True, text=True, timeout=10)
            ver = (r.stdout + r.stderr).strip()
            if ver:
                print(f"   バージョン情報: {ver[:200]}")
        except Exception:
            pass
    else:
        print("❌ ODA File Converter が見つかりません")
        print("\n確認したパス:")
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
                r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
            ]
            for c in candidates:
                print(f"  {'✅' if os.path.isfile(c) else '❌'} {c}")
        cmd_found = shutil.which("ODAFileConverter")
        print(f"  {'✅' if cmd_found else '❌'} PATH上の ODAFileConverter "
              f"({'→ ' + cmd_found if cmd_found else '未発見'})")

    print(f"\n設定ファイルのパス  : {custom_path or '(未設定)'}")
    print(f"設定バージョン      : {config.get('oda_version', 'ACAD2018')}")
    print(f"Audit               : {config.get('oda_audit', True)}")
    print("\nインストール: https://www.opendesign.com/guestfiles/oda_file_converter")
    print("設定コマンド: python -m src oda-set <パス>")


def cmd_oda_set(args):
    """ODA File Converter のパスを設定ファイルに保存する。"""
    from src.extractors.dwg_extractor import find_oda_executable
    from src.utils.helpers import load_config, save_config

    path   = args.path
    config = load_config()

    if path == "auto":
        found = find_oda_executable("")
        if found:
            config["oda_path"] = ""   # 自動検出に任せる
            save_config(config)
            print(f"[OK] 自動検出モードに設定しました。検出済み: {found}")
        else:
            print("[ERROR] ODA File Converter が自動検出できませんでした。")
            print("        手動でパスを指定してください: python -m src oda-set <パス>")
        return

    found = find_oda_executable(path)
    if found:
        config["oda_path"] = path
        if args.version:
            config["oda_version"] = args.version
        save_config(config)
        print(f"[OK] 設定を保存しました。")
        print(f"     パス     : {found}")
        print(f"     バージョン: {config.get('oda_version', 'ACAD2018')}")
    else:
        print(f"[ERROR] 指定したパスに ODA File Converter が見つかりません: {path}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="drawing_search",
        description="図面番号検索アプリ / Drawing Number Search Tool",
    )
    subparsers = parser.add_subparsers(dest="command")

    # gui
    p_gui = subparsers.add_parser("gui", help="GUIを起動 / Launch GUI")
    p_gui.set_defaults(func=cmd_gui)

    # index
    p_index = subparsers.add_parser("index", help="ファイル/フォルダをインデックス登録 / Index files")
    p_index.add_argument("path", help="File or directory to index")
    p_index.add_argument("--no-recursive", action="store_true",
                         help="Do not recurse into subdirectories")
    p_index.set_defaults(func=cmd_index)

    # search
    p_search = subparsers.add_parser("search", help="図面番号を検索 / Search drawing numbers")
    p_search.add_argument("query", help="Search query (drawing number or keyword)")
    p_search.add_argument("--match-type", default="all",
                          choices=["all", "drawing_number", "full_text", "filename"],
                          help="Type of match to perform (default: all)")
    p_search.add_argument("--case-sensitive", action="store_true",
                          help="Enable case-sensitive matching")
    p_search.add_argument("--regex", action="store_true",
                          help="Treat query as regular expression")
    p_search.add_argument("--json", action="store_true",
                          help="Output results as JSON")
    p_search.set_defaults(func=cmd_search)

    # list
    p_list = subparsers.add_parser("list", help="インデックス済みファイル一覧 / List indexed files")
    p_list.set_defaults(func=cmd_list)

    # oda-check
    p_oda_check = subparsers.add_parser("oda-check",
        help="ODA File Converter の検出状態を確認 / Check ODA installation")
    p_oda_check.set_defaults(func=cmd_oda_check)

    # oda-set
    p_oda_set = subparsers.add_parser("oda-set",
        help="ODA File Converter のパスを設定 / Set ODA executable path")
    p_oda_set.add_argument("path",
        help="ODA 実行ファイルの絶対パス (または 'auto' で自動検出)")
    p_oda_set.add_argument("--version", default="",
        help="変換バージョン (例: ACAD2018, ACAD2013) デフォルト: ACAD2018")
    p_oda_set.set_defaults(func=cmd_oda_set)

    args = parser.parse_args()

    if args.command is None:
        # Default: launch GUI
        cmd_gui(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
