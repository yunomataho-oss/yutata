"""
Command-line interface for Drawing Number Search.
Usage:
    python -m src.cli search "DRW-12345"
    python -m src.cli index ./drawings
    python -m src.cli index ./drawings --no-recursive
    python -m src.cli list
    python -m src.cli gui
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

    args = parser.parse_args()

    if args.command is None:
        # Default: launch GUI
        cmd_gui(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
