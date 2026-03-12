"""
Main GUI application for Drawing Number Search.
Uses Tkinter (stdlib) — no extra GUI library needed.

Layout:
┌─────────────────────────────────────────────┐
│  [Search query       ] [Search▼] [Options]  │
│─────────────────────────────────────────────│
│  Results list (treeview)                    │
│─────────────────────────────────────────────│
│  Detail panel (texts / metadata)            │
│─────────────────────────────────────────────│
│  Status bar                                 │
└─────────────────────────────────────────────┘
Menubar: File > Index Folder / Index File / Clear Index / Exit
         View > Show All Indexed Files
         Help > About
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import List, Optional

# Ensure parent package is importable when running as __main__
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.search.search_engine import DrawingSearchEngine, IndexEntry, SearchResult


WINDOW_TITLE  = "図面番号検索アプリ  Drawing Number Search"
BG_COLOR      = "#f5f5f5"
ACCENT_COLOR  = "#1565c0"
ROW_EVEN      = "#ffffff"
ROW_ODD       = "#e8eaf6"
FONT_MAIN     = ("Yu Gothic UI", 11) if sys.platform == "win32" else ("Helvetica", 11)
FONT_BOLD     = ("Yu Gothic UI", 11, "bold") if sys.platform == "win32" else ("Helvetica", 11, "bold")
FONT_MONO     = ("Consolas", 10) if sys.platform == "win32" else ("Courier", 10)

MATCH_TYPE_OPTIONS = {
    "すべて (All)":       "all",
    "図面番号 (Drawing No.)": "drawing_number",
    "ファイル名 (Filename)":  "filename",
    "全文 (Full Text)":   "full_text",
}


class DrawingSearchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("1100x720")
        self.minsize(800, 550)
        self.configure(bg=BG_COLOR)

        self.engine = DrawingSearchEngine()
        self._results: List[SearchResult] = []
        self._index_thread: Optional[threading.Thread] = None

        self._build_menu()
        self._build_toolbar()
        self._build_main_panel()
        self._build_status_bar()
        self._refresh_status()

        # Keyboard shortcut
        self.bind("<Return>", lambda _e: self._do_search())
        self.bind("<F5>", lambda _e: self._do_search())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ #
    #  UI Construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_menu(self):
        menubar = tk.Menu(self)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="フォルダをインデックス登録 (Index Folder)…",
                              command=self._index_folder, accelerator="Ctrl+O")
        file_menu.add_command(label="ファイルをインデックス登録 (Index File)…",
                              command=self._index_file)
        file_menu.add_separator()
        file_menu.add_command(label="インデックスをクリア (Clear Index)",
                              command=self._clear_index)
        file_menu.add_separator()
        file_menu.add_command(label="終了 (Exit)", command=self._on_close, accelerator="Alt+F4")
        menubar.add_cascade(label="ファイル (File)", menu=file_menu)
        self.bind_all("<Control-o>", lambda _e: self._index_folder())

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="インデックス一覧 (Show All Indexed Files)",
                              command=self._show_all_indexed)
        menubar.add_cascade(label="表示 (View)", menu=view_menu)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使い方 (How to Use)", command=self._show_help)
        help_menu.add_command(label="バージョン情報 (About)", command=self._show_about)
        menubar.add_cascade(label="ヘルプ (Help)", menu=help_menu)

        self.config(menu=menubar)

    def _build_toolbar(self):
        toolbar = tk.Frame(self, bg=ACCENT_COLOR, pady=6)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        # Label
        tk.Label(toolbar, text="🔍", font=("", 16), bg=ACCENT_COLOR, fg="white").pack(side=tk.LEFT, padx=(10, 4))

        # Search entry
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(toolbar, textvariable=self._search_var, font=FONT_MAIN, width=40)
        self._search_entry.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        self._search_entry.focus()

        # Match type dropdown
        self._match_type_var = tk.StringVar(value="すべて (All)")
        match_cb = ttk.Combobox(
            toolbar,
            textvariable=self._match_type_var,
            values=list(MATCH_TYPE_OPTIONS.keys()),
            state="readonly",
            width=24,
            font=FONT_MAIN,
        )
        match_cb.pack(side=tk.LEFT, padx=(0, 6))

        # Options: regex / case sensitive
        self._regex_var = tk.BooleanVar(value=False)
        self._case_var  = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="正規表現 (Regex)",  variable=self._regex_var,
                        style="Toolbar.TCheckbutton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Checkbutton(toolbar, text="大小区別 (Case)",   variable=self._case_var,
                        style="Toolbar.TCheckbutton").pack(side=tk.LEFT, padx=(0, 10))

        # Search button
        search_btn = tk.Button(
            toolbar, text="検索 (Search)", font=FONT_BOLD,
            bg="#ffd600", fg="#000000", relief=tk.FLAT,
            padx=14, pady=4,
            command=self._do_search,
            cursor="hand2",
        )
        search_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Index folder button (quick access)
        index_btn = tk.Button(
            toolbar, text="📂 フォルダ登録 (Index Folder)", font=FONT_MAIN,
            bg="#37474f", fg="white", relief=tk.FLAT,
            padx=10, pady=4,
            command=self._index_folder,
            cursor="hand2",
        )
        index_btn.pack(side=tk.RIGHT, padx=(0, 10))

    def _build_main_panel(self):
        paned = tk.PanedWindow(self, orient=tk.VERTICAL, sashrelief=tk.RAISED,
                               sashwidth=5, bg=BG_COLOR)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # ---- Top: Results treeview ----
        top_frame = tk.Frame(paned, bg=BG_COLOR)
        paned.add(top_frame, minsize=200)

        result_label = tk.Label(top_frame, text="検索結果 (Results)",
                                font=FONT_BOLD, bg=BG_COLOR, fg=ACCENT_COLOR)
        result_label.pack(anchor=tk.W, padx=4, pady=(2, 0))

        cols = ("filename", "drawing_numbers", "match_type", "matched", "path")
        self._tree = ttk.Treeview(top_frame, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("filename",        text="ファイル名")
        self._tree.heading("drawing_numbers", text="図面番号")
        self._tree.heading("match_type",      text="マッチ種別")
        self._tree.heading("matched",         text="マッチ値/スニペット")
        self._tree.heading("path",            text="パス")
        self._tree.column("filename",        width=200, minwidth=120)
        self._tree.column("drawing_numbers", width=180, minwidth=100)
        self._tree.column("match_type",      width=120, minwidth=80,  anchor=tk.CENTER)
        self._tree.column("matched",         width=300, minwidth=150)
        self._tree.column("path",            width=250, minwidth=120)

        vsb = ttk.Scrollbar(top_frame, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(top_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

        self._tree.bind("<<TreeviewSelect>>", self._on_result_select)
        self._tree.bind("<Double-1>", self._open_file)

        # Alternating row colours
        self._tree.tag_configure("even", background=ROW_EVEN)
        self._tree.tag_configure("odd",  background=ROW_ODD)

        # Right-click context menu
        ctx = tk.Menu(self._tree, tearoff=0)
        ctx.add_command(label="ファイルを開く (Open File)",     command=self._open_file)
        ctx.add_command(label="フォルダを開く (Open Folder)",   command=self._open_containing_folder)
        ctx.add_command(label="パスをコピー (Copy Path)",        command=self._copy_path)
        self._tree.bind("<Button-3>", lambda e: ctx.post(e.x_root, e.y_root))

        # ---- Bottom: Detail panel ----
        bottom_frame = tk.Frame(paned, bg=BG_COLOR)
        paned.add(bottom_frame, minsize=120)

        detail_label = tk.Label(bottom_frame, text="詳細情報 (Detail)",
                                font=FONT_BOLD, bg=BG_COLOR, fg=ACCENT_COLOR)
        detail_label.pack(anchor=tk.W, padx=4, pady=(2, 0))

        self._detail_text = scrolledtext.ScrolledText(
            bottom_frame, font=FONT_MONO, wrap=tk.WORD,
            height=8, state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
        )
        self._detail_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

    def _build_status_bar(self):
        self._status_var = tk.StringVar()
        status_bar = tk.Label(
            self, textvariable=self._status_var,
            bd=1, relief=tk.SUNKEN, anchor=tk.W,
            font=("", 9), bg="#e0e0e0", fg="#333333",
        )
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=0, pady=0)

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    def _do_search(self, *_):
        query = self._search_var.get().strip()
        if not query:
            messagebox.showinfo("入力エラー", "検索キーワードを入力してください。")
            return

        match_key  = self._match_type_var.get()
        match_type = MATCH_TYPE_OPTIONS.get(match_key, "all")
        use_regex  = self._regex_var.get()
        case_sens  = self._case_var.get()

        self._status_var.set(f"検索中… / Searching for: {query}")
        self.update_idletasks()

        try:
            results = self.engine.search(
                query,
                match_type=match_type,
                case_sensitive=case_sens,
                use_regex=use_regex,
            )
        except re.error as e:
            messagebox.showerror("正規表現エラー", f"正規表現が無効です:\n{e}")
            self._status_var.set("正規表現エラー")
            return
        except Exception as e:
            messagebox.showerror("検索エラー", str(e))
            self._status_var.set(f"エラー: {e}")
            return

        self._results = results
        self._populate_results(results)
        self._status_var.set(
            f"{len(results)} 件ヒット  |  インデックス: {self.engine.index_size} ファイル  |  '{query}'"
        )

    def _populate_results(self, results: List[SearchResult]):
        self._tree.delete(*self._tree.get_children())
        self._clear_detail()

        for i, r in enumerate(results):
            dn_str = ", ".join(r.entry.drawing_numbers) if r.entry.drawing_numbers else "—"
            tag = "even" if i % 2 == 0 else "odd"
            self._tree.insert(
                "", tk.END, iid=str(i),
                values=(
                    r.entry.filename,
                    dn_str,
                    r.match_type,
                    r.matched_value[:100],
                    r.entry.file_path,
                ),
                tags=(tag,),
            )

    def _on_result_select(self, _event=None):
        sel = self._tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._results):
            return
        result = self._results[idx]
        self._show_detail(result)

    def _show_detail(self, result: SearchResult):
        entry = result.entry
        lines = [
            f"ファイル名  : {entry.filename}",
            f"パス       : {entry.file_path}",
            f"種別       : {entry.file_type.upper()}",
            f"図面番号   : {', '.join(entry.drawing_numbers) if entry.drawing_numbers else '—'}",
            f"タイトル   : {entry.title or '—'}",
            f"マッチ種別 : {result.match_type}",
            f"マッチ値   : {result.matched_value}",
            f"スコア     : {result.score:.2f}",
        ]
        if entry.error:
            lines.append(f"\n⚠ 注意 (Warning): {entry.error}")

        lines.append("\n─── 抽出テキスト (Extracted texts, first 3000 chars) ───")
        lines.append(entry.texts_blob[:3000] + ("…" if len(entry.texts_blob) > 3000 else ""))

        self._detail_text.configure(state=tk.NORMAL)
        self._detail_text.delete("1.0", tk.END)
        self._detail_text.insert(tk.END, "\n".join(lines))
        self._detail_text.configure(state=tk.DISABLED)

    def _clear_detail(self):
        self._detail_text.configure(state=tk.NORMAL)
        self._detail_text.delete("1.0", tk.END)
        self._detail_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ #
    #  Indexing                                                            #
    # ------------------------------------------------------------------ #

    def _index_folder(self):
        folder = filedialog.askdirectory(title="インデックス登録するフォルダを選択")
        if not folder:
            return

        recursive = messagebox.askyesno(
            "サブフォルダ",
            "サブフォルダも再帰的にスキャンしますか？\nScan subfolders recursively?",
        )

        self._run_indexing(folder=folder, recursive=recursive)

    def _index_file(self):
        exts = [
            ("Drawing files", "*.pdf *.dxf *.dwg *.slddrw *.SLDDRW"),
            ("PDF files",  "*.pdf"),
            ("DXF files",  "*.dxf"),
            ("DWG files",  "*.dwg"),
            ("SolidWorks", "*.slddrw"),
            ("All files",  "*.*"),
        ]
        path = filedialog.askopenfilename(
            title="インデックス登録するファイルを選択",
            filetypes=exts,
        )
        if not path:
            return
        self._run_single_file_indexing(path)

    def _run_single_file_indexing(self, file_path: str):
        self._status_var.set(f"インデックス登録中… {os.path.basename(file_path)}")
        self.update_idletasks()

        def task():
            try:
                entry = self.engine.index_file(file_path)
                self.engine._save_index()
                self.after(0, lambda: self._status_var.set(
                    f"登録完了: {entry.filename}  |  インデックス: {self.engine.index_size} ファイル"
                ))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("インデックスエラー", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def _run_indexing(self, folder: str, recursive: bool):
        if self._index_thread and self._index_thread.is_alive():
            messagebox.showwarning("処理中", "インデックス登録が既に実行中です。")
            return

        progress_win = tk.Toplevel(self)
        progress_win.title("インデックス登録中…")
        progress_win.geometry("500x150")
        progress_win.resizable(False, False)
        progress_win.grab_set()

        tk.Label(progress_win, text="ファイルをスキャン中…", font=FONT_MAIN).pack(pady=(16, 4))
        file_label = tk.Label(progress_win, text="", font=("", 9), wraplength=480)
        file_label.pack(pady=2)
        pbar = ttk.Progressbar(progress_win, mode="determinate", length=460)
        pbar.pack(pady=8)
        count_label = tk.Label(progress_win, text="0 / 0", font=("", 9))
        count_label.pack()

        def progress_cb(fpath, current, total):
            pct = int(current / total * 100) if total > 0 else 0
            self.after(0, lambda: file_label.config(text=os.path.basename(fpath)))
            self.after(0, lambda: pbar.config(value=pct))
            self.after(0, lambda: count_label.config(text=f"{current} / {total}"))
            self.after(0, self.update_idletasks)

        def task():
            indexed, errors = self.engine.index_directory(
                folder, recursive=recursive, progress_callback=progress_cb
            )
            self.after(0, progress_win.destroy)
            self.after(0, lambda: self._status_var.set(
                f"登録完了: {indexed} 件成功, {errors} 件エラー  |  インデックス合計: {self.engine.index_size} ファイル"
            ))
            self.after(0, lambda: messagebox.showinfo(
                "完了",
                f"インデックス登録が完了しました。\n\n"
                f"✅ 成功: {indexed} ファイル\n"
                f"⚠ エラー: {errors} ファイル\n"
                f"📚 合計インデックス: {self.engine.index_size} ファイル"
            ))

        self._index_thread = threading.Thread(target=task, daemon=True)
        self._index_thread.start()

    def _clear_index(self):
        if messagebox.askyesno("確認", "インデックスをすべて削除しますか？\nClear all indexed data?"):
            self.engine.clear_index()
            self._tree.delete(*self._tree.get_children())
            self._clear_detail()
            self._status_var.set("インデックスをクリアしました。")

    # ------------------------------------------------------------------ #
    #  Utility actions                                                     #
    # ------------------------------------------------------------------ #

    def _open_file(self, *_):
        sel = self._tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._results):
            return
        path = self._results[idx].entry.file_path
        self._os_open(path)

    def _open_containing_folder(self, *_):
        sel = self._tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._results):
            return
        folder = os.path.dirname(self._results[idx].entry.file_path)
        self._os_open(folder)

    def _copy_path(self, *_):
        sel = self._tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._results):
            return
        path = self._results[idx].entry.file_path
        self.clipboard_clear()
        self.clipboard_append(path)
        self._status_var.set(f"コピーしました: {path}")

    @staticmethod
    def _os_open(path: str):
        import subprocess
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _show_all_indexed(self):
        win = tk.Toplevel(self)
        win.title("インデックス済みファイル一覧")
        win.geometry("900x500")

        cols = ("filename", "type", "drawing_numbers", "path")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("filename",        text="ファイル名")
        tree.heading("type",            text="種別")
        tree.heading("drawing_numbers", text="図面番号")
        tree.heading("path",            text="パス")
        tree.column("filename",        width=200)
        tree.column("type",            width=60, anchor=tk.CENTER)
        tree.column("drawing_numbers", width=200)
        tree.column("path",            width=380)

        vsb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for i, entry in enumerate(self.engine.indexed_files):
            dn = ", ".join(entry.drawing_numbers) if entry.drawing_numbers else "—"
            tag = "even" if i % 2 == 0 else "odd"
            tree.insert("", tk.END, values=(entry.filename, entry.file_type.upper(), dn, entry.file_path),
                        tags=(tag,))

        tree.tag_configure("even", background=ROW_EVEN)
        tree.tag_configure("odd",  background=ROW_ODD)

        tk.Label(win, text=f"合計 {self.engine.index_size} ファイル", font=FONT_MAIN).pack(pady=4)

    def _show_help(self):
        msg = (
            "【使い方 / How to Use】\n\n"
            "1. 「📂 フォルダ登録」または メニュー > ファイル > フォルダをインデックス登録 で\n"
            "   図面ファイルが入ったフォルダを選択してください。\n\n"
            "2. 検索バーに図面番号またはキーワードを入力して\n"
            "   「検索」ボタンを押すか Enter キーを押してください。\n\n"
            "3. 検索結果をダブルクリックするとファイルが開きます。\n\n"
            "【対応ファイル形式】\n"
            "  PDF / DXF / DWG / SLDDRW (SolidWorks)\n\n"
            "【DWG について】\n"
            "  ODA File Converter がインストールされている場合、正確に解析します。\n"
            "  未インストールの場合はバイナリスキャン（簡易解析）になります。\n\n"
            "【SLDDRW について】\n"
            "  Windows + SolidWorks インストール済みの環境で最高精度で動作します。\n"
            "  それ以外の環境ではバイナリスキャンになります。\n"
        )
        messagebox.showinfo("使い方", msg)

    def _show_about(self):
        messagebox.showinfo(
            "バージョン情報",
            "図面番号検索アプリ  Drawing Number Search\n"
            "Version 1.0.0\n\n"
            "対応形式: PDF / DXF / DWG / SLDDRW\n"
            "エンジン: PyMuPDF + ezdxf + ODA File Converter\n\n"
            "© 2026",
        )

    # ------------------------------------------------------------------ #
    #  Misc                                                                #
    # ------------------------------------------------------------------ #

    def _refresh_status(self):
        self._status_var.set(
            f"準備完了  |  インデックス: {self.engine.index_size} ファイル  |  "
            "Ctrl+O でフォルダを登録 / Enter で検索"
        )

    def _on_close(self):
        self.destroy()


# ---------------------------------------------------------------------------#

def main():
    import re   # ensure import available for search
    app = DrawingSearchApp()
    app.mainloop()


if __name__ == "__main__":
    main()
