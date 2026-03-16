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

import csv
import datetime
import io
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import List, Optional

# PIL / Pillow は使用箇所で遅延 import する。
# トップレベルで import すると PIL 未インストール時に起動自体が失敗するため。

# Ensure parent package is importable when running as __main__
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.search.search_engine import DrawingSearchEngine, IndexEntry, SearchResult
from src.utils.helpers import load_config, save_config
from src.extractors.dwg_extractor import find_oda_executable, is_oda_installed


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


def _fmt_eta(seconds: float) -> str:
    """Convert remaining seconds to a human-readable string like '残り 1分23秒'."""
    if seconds <= 0:
        return ""
    s = int(seconds)
    if s < 60:
        return f"残り {s}秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"残り {m}分{s:02d}秒"
    h, m = divmod(m, 60)
    return f"残り {h}時間{m:02d}分"


class DrawingSearchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("1100x720")
        self.minsize(800, 550)
        self.configure(bg=BG_COLOR)

        self._config = load_config()
        self.engine = DrawingSearchEngine(config=self._config)
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
        file_menu.add_command(label="強制再インデックス (Force Re-index Folder)…",
                              command=self._index_folder_force)
        file_menu.add_separator()
        file_menu.add_command(label="インデックスをクリア (Clear Index)",
                              command=self._clear_index)
        file_menu.add_separator()
        file_menu.add_command(label="検索結果を CSV で保存… (Export Results to CSV)",
                              command=self._export_csv,
                              accelerator="Ctrl+Shift+E")
        file_menu.add_separator()
        file_menu.add_command(label="終了 (Exit)", command=self._on_close, accelerator="Alt+F4")
        menubar.add_cascade(label="ファイル (File)", menu=file_menu)
        self.bind_all("<Control-o>", lambda _e: self._index_folder())
        self.bind_all("<Control-E>", lambda _e: self._export_csv())

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="インデックス一覧 (Show All Indexed Files)",
                              command=self._show_all_indexed)
        menubar.add_cascade(label="表示 (View)", menu=view_menu)

        # Settings menu
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="ODA File Converter 設定…",
                                   command=self._open_oda_settings)
        settings_menu.add_command(label="ODA 接続テスト (Check ODA)",
                                   command=self._check_oda)
        menubar.add_cascade(label="設定 (Settings)", menu=settings_menu)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使い方 (How to Use)", command=self._show_help)
        help_menu.add_command(label="ODA 連携ガイド",     command=self._show_oda_guide)
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

        # CSV export button
        csv_btn = tk.Button(
            toolbar, text="📋 CSV出力", font=FONT_MAIN,
            bg="#2e7d32", fg="white", relief=tk.FLAT,
            padx=10, pady=4,
            command=self._export_csv,
            cursor="hand2",
        )
        csv_btn.pack(side=tk.RIGHT, padx=(0, 6))

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
        # ══ 左右水平分割 (結果リスト | プレビュー+詳細) ══
        h_paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashrelief=tk.RAISED,
                                  sashwidth=5, bg=BG_COLOR)
        h_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # ────────── 左ペイン: 結果リスト + 詳細テキスト ──────────────────
        left_paned = tk.PanedWindow(h_paned, orient=tk.VERTICAL, sashrelief=tk.RAISED,
                                     sashwidth=5, bg=BG_COLOR)
        h_paned.add(left_paned, minsize=380)

        # ---- 結果ツリー ----
        top_frame = tk.Frame(left_paned, bg=BG_COLOR)
        left_paned.add(top_frame, minsize=180)

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
        self._tree.column("filename",        width=160, minwidth=100)
        self._tree.column("drawing_numbers", width=140, minwidth=80)
        self._tree.column("match_type",      width=90,  minwidth=60,  anchor=tk.CENTER)
        self._tree.column("matched",         width=200, minwidth=100)
        self._tree.column("path",            width=200, minwidth=100)

        vsb = ttk.Scrollbar(top_frame, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(top_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

        self._tree.bind("<<TreeviewSelect>>", self._on_result_select)
        self._tree.bind("<Double-1>",         self._open_preview_window)
        self._tree.tag_configure("even", background=ROW_EVEN)
        self._tree.tag_configure("odd",  background=ROW_ODD)

        ctx = tk.Menu(self._tree, tearoff=0)
        ctx.add_command(label="🔍 プレビュー (Preview)",        command=self._open_preview_window)
        ctx.add_command(label="ファイルを開く (Open File)",      command=self._open_file)
        ctx.add_command(label="フォルダを開く (Open Folder)",    command=self._open_containing_folder)
        ctx.add_command(label="パスをコピー (Copy Path)",         command=self._copy_path)
        ctx.add_separator()
        ctx.add_command(label="📋 検索結果を CSV で保存…",        command=self._export_csv)
        self._tree.bind("<Button-3>", lambda e: ctx.post(e.x_root, e.y_root))

        # ---- 詳細テキスト ----
        bottom_frame = tk.Frame(left_paned, bg=BG_COLOR)
        left_paned.add(bottom_frame, minsize=100)

        detail_label = tk.Label(bottom_frame, text="詳細情報 (Detail)",
                                font=FONT_BOLD, bg=BG_COLOR, fg=ACCENT_COLOR)
        detail_label.pack(anchor=tk.W, padx=4, pady=(2, 0))

        self._detail_text = scrolledtext.ScrolledText(
            bottom_frame, font=FONT_MONO, wrap=tk.WORD,
            height=7, state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
        )
        self._detail_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # ────────── 右ペイン: インラインプレビュー ───────────────────────
        right_frame = tk.Frame(h_paned, bg="#2b2b2b")
        h_paned.add(right_frame, minsize=300)

        # プレビューヘッダー（上段）
        prev_header = tk.Frame(right_frame, bg="#37474f", pady=3)
        prev_header.pack(side=tk.TOP, fill=tk.X)

        tk.Label(prev_header, text="プレビュー (Preview)",
                 font=FONT_BOLD, bg="#37474f", fg="white").pack(side=tk.LEFT, padx=8)

        # レイアウト名ラベル（該当ページ1枚表示なのでページ送りボタンは不要）
        self._inline_page_label = tk.Label(prev_header, text="",
                                            bg="#37474f", fg="#90caf9", font=FONT_BOLD)
        self._inline_page_label.pack(side=tk.LEFT, padx=8)

        # 別ウィンドウで開くボタン（全ページ表示）
        tk.Button(prev_header, text="⬜ 別ウィンドウ (全ページ)", command=self._open_preview_window,
                  bg="#1565c0", fg="white", relief=tk.FLAT, padx=6,
                  cursor="hand2").pack(side=tk.RIGHT, padx=8)

        # ズームボタン
        tk.Button(prev_header, text="フィット", command=self._inline_fit,
                  bg="#546e7a", fg="white", relief=tk.FLAT, padx=4,
                  cursor="hand2").pack(side=tk.RIGHT, padx=2)
        tk.Button(prev_header, text="＋", command=self._inline_zoom_in,
                  bg="#546e7a", fg="white", relief=tk.FLAT, padx=4,
                  cursor="hand2").pack(side=tk.RIGHT, padx=2)
        tk.Button(prev_header, text="－", command=self._inline_zoom_out,
                  bg="#546e7a", fg="white", relief=tk.FLAT, padx=4,
                  cursor="hand2").pack(side=tk.RIGHT, padx=2)
        self._inline_zoom_label = tk.Label(prev_header, text="100%",
                                            bg="#37474f", fg="#ffd600", font=FONT_BOLD)
        self._inline_zoom_label.pack(side=tk.RIGHT, padx=4)

        # キャンバス
        self._prev_vscroll = tk.Scrollbar(right_frame, orient=tk.VERTICAL)
        self._prev_hscroll = tk.Scrollbar(right_frame, orient=tk.HORIZONTAL)
        self._prev_vscroll.pack(side=tk.RIGHT,  fill=tk.Y)
        self._prev_hscroll.pack(side=tk.BOTTOM, fill=tk.X)

        self._preview_canvas = tk.Canvas(
            right_frame, bg="#2b2b2b", highlightthickness=0,
            yscrollcommand=self._prev_vscroll.set,
            xscrollcommand=self._prev_hscroll.set,
            cursor="fleur",
        )
        self._preview_canvas.pack(fill=tk.BOTH, expand=True)
        self._prev_vscroll.config(command=self._preview_canvas.yview)
        self._prev_hscroll.config(command=self._preview_canvas.xview)

        # プレビュー状態変数
        self._inline_pages:   list = []   # list[PageResult]
        self._inline_tk_img   = None      # GC 防止
        self._inline_current  = 0
        self._inline_zoom     = 1.0
        # プレビューデバウンス・キャンセル制御
        self._preview_token: int = 0   # リクエストごとにインクリメント
        self._preview_after_id = None  # after() ID (デバウンス用)

        # キャンバスドラッグ
        self._preview_canvas.bind("<ButtonPress-1>",  self._prev_drag_start)
        self._preview_canvas.bind("<B1-Motion>",       self._prev_drag_move)
        self._preview_canvas.bind("<MouseWheel>",      self._prev_wheel)
        self._preview_canvas.bind("<Button-4>",        self._prev_wheel)
        self._preview_canvas.bind("<Button-5>",        self._prev_wheel)
        self._preview_canvas.bind("<Control-MouseWheel>", self._prev_ctrl_wheel)
        self._preview_canvas.bind("<Control-Button-4>",   self._prev_ctrl_wheel)
        self._preview_canvas.bind("<Control-Button-5>",   self._prev_ctrl_wheel)

        # 初期メッセージ
        self._show_preview_placeholder("図面を選択するとプレビューが表示されます")

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
        # インラインプレビューを非同期でロード（該当ページのみ表示）
        self._load_inline_preview(result)

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
    #  Inline Preview Methods                                              #
    # ------------------------------------------------------------------ #

    def _show_preview_placeholder(self, message: str):
        """プレビューキャンバスにメッセージを表示する。"""
        self._preview_canvas.delete("all")
        w = max(self._preview_canvas.winfo_width(),  300)
        h = max(self._preview_canvas.winfo_height(), 200)
        self._preview_canvas.create_text(
            w // 2, h // 2,
            text=message, fill="#546e7a",
            font=("Helvetica", 13), justify=tk.CENTER,
        )

    def _load_inline_preview(self, result):
        """バックグラウンドスレッドでプレビュー画像を生成する（デバウンス付き）。"""
        # デバウンス: 250ms 以内に連続選択された場合は最後だけ処理 (CPU負荷削減)
        if self._preview_after_id is not None:
            self.after_cancel(self._preview_after_id)
        self._preview_after_id = self.after(
            250, lambda: self._start_preview_load(result)
        )

    @staticmethod
    def _resolve_target(result) -> tuple:
        """
        SearchResult から対象レイアウト名 (DXF/DWG) またはページ番号 (PDF) を導出する。
        戻り値: (target_layout: str|None, target_page: int|None)
        """
        entry      = result.entry
        ext        = os.path.splitext(entry.file_path)[1].lower()
        match_type = result.match_type
        matched    = result.matched_value  # マッチした文字列

        target_layout: Optional[str] = None
        target_page:   Optional[int] = None

        if ext in (".dxf", ".dwg"):
            # full_text マッチの場合: matched_value を含むレイアウトを探す
            if match_type == "full_text" and matched.strip():
                try:
                    import ezdxf
                    doc = ezdxf.readfile(entry.file_path)
                    from src.extractors.dxf_extractor import _collect_texts_from_layout
                    import re
                    pattern = re.compile(re.escape(matched.strip()), re.IGNORECASE)
                    # paper space → model space の順で探す
                    all_names = list(doc.layouts.names())
                    ordered   = [n for n in all_names if n != "Model"] + \
                                [n for n in all_names if n == "Model"]
                    for name in ordered:
                        layout = doc.layouts.get(name)
                        texts  = _collect_texts_from_layout(layout)
                        if any(pattern.search(t) for t in texts):
                            target_layout = name
                            break
                except Exception:
                    pass  # フォールバック: 全レイアウト
            # drawing_number / filename: 最初のページ(デフォルト)で十分

        elif ext == ".pdf":
            # PDF ページ番号の特定は texts_blob から困難なため常に先頭ページ
            target_page = 0

        return target_layout, target_page

    def _start_preview_load(self, result):
        """実際のレンダリング開始（デバウンス後に呼ばれる）。"""
        self._preview_after_id = None
        # トークンを更新: 古いワーカーの結果は無視される
        self._preview_token += 1
        token     = self._preview_token
        file_path = result.entry.file_path

        self._show_preview_placeholder("🔄 レンダリング中...")
        self._inline_pages   = []
        self._inline_current = 0
        self._inline_zoom    = 1.0

        def worker():
            from src.preview.preview_engine import get_preview, _make_error_image, PageResult
            try:
                target_layout, target_page = self._resolve_target(result)
                pages = get_preview(
                    file_path, self._config,
                    max_pages=1,
                    target_layout=target_layout,
                    target_page=target_page,
                )
            except Exception as exc:
                pages = [PageResult(_make_error_image("プレビューエラー", str(exc)))]
            # トークンが変わっていたら（別の選択が来た）何もしない
            if token == self._preview_token:
                self.after(0, lambda: self._on_inline_loaded(pages))

        threading.Thread(target=worker, daemon=True).start()

    def _on_inline_loaded(self, pages):
        self._inline_pages   = pages
        self._inline_current = 0
        self._inline_zoom    = 1.0
        self._draw_inline_page()

    def _draw_inline_page(self):
        """現在のページをインラインキャンバスに描画する。"""
        if not self._inline_pages:
            return

        idx = self._inline_current
        pr  = self._inline_pages[idx]

        # PageResult または PIL.Image を吸収
        img = pr.image if hasattr(pr, 'image') else pr

        # ウィンドウ幅に合わせて自動フィット（初回のみ zoom=1 → fit に調整）
        cw = max(self._preview_canvas.winfo_width(),  300)
        ch = max(self._preview_canvas.winfo_height(), 200)
        if self._inline_zoom == 1.0:
            zw = cw / img.width
            zh = ch / img.height
            self._inline_zoom = min(zw, zh) * 0.97

        new_w = max(1, int(img.width  * self._inline_zoom))
        new_h = max(1, int(img.height * self._inline_zoom))
        from PIL import Image as _Image, ImageTk
        # 縮小時は NEAREST (最高速)、拡大時は BILINEAR (LANCZOSは不要なほど重い)
        if self._inline_zoom >= 1.0:
            filt = _Image.BILINEAR
        else:
            filt = _Image.NEAREST
        resized = img.resize((new_w, new_h), filt)
        self._inline_tk_img = ImageTk.PhotoImage(resized)   # GC 防止

        self._preview_canvas.delete("all")
        self._preview_canvas.create_image(0, 0, anchor=tk.NW,
                                           image=self._inline_tk_img)
        self._preview_canvas.configure(scrollregion=(0, 0, new_w, new_h))
        self._preview_canvas.xview_moveto(0)
        self._preview_canvas.yview_moveto(0)

        layout = img.info.get("layout_name") or getattr(pr, 'layout_name', '') or ""
        self._inline_page_label.config(text=layout)
        self._inline_zoom_label.config(text=f"{int(self._inline_zoom * 100)}%")

        self._status_var.set(
            f"{layout}  |  ズーム: {int(self._inline_zoom * 100)}%  |  "
            "Ctrl+ホイールでズーム / ドラッグでスクロール / ダブルクリックで別ウィンドウ(全ページ)"
        )

    def _inline_prev_page(self):
        if self._inline_current > 0:
            self._inline_current -= 1
            self._inline_zoom = 1.0
            self._draw_inline_page()

    def _inline_next_page(self):
        if self._inline_pages and self._inline_current < len(self._inline_pages) - 1:
            self._inline_current += 1
            self._inline_zoom = 1.0
            self._draw_inline_page()

    def _inline_zoom_in(self):
        self._inline_zoom = min(4.0, self._inline_zoom + 0.15)
        self._redraw_inline()

    def _inline_zoom_out(self):
        self._inline_zoom = max(0.1, self._inline_zoom - 0.15)
        self._redraw_inline()

    def _inline_fit(self):
        self._inline_zoom = 1.0   # trigger auto-fit in draw
        self._draw_inline_page()

    def _redraw_inline(self):
        if not self._inline_pages:
            return
        idx = self._inline_current
        pr  = self._inline_pages[idx]
        img = pr.image if hasattr(pr, 'image') else pr
        new_w = max(1, int(img.width  * self._inline_zoom))
        new_h = max(1, int(img.height * self._inline_zoom))
        from PIL import Image as _Image, ImageTk
        # 縮小時は NEAREST (最高速)、拡大時は BILINEAR
        if self._inline_zoom >= 1.0:
            filt = _Image.BILINEAR
        else:
            filt = _Image.NEAREST
        resized = img.resize((new_w, new_h), filt)
        self._inline_tk_img = ImageTk.PhotoImage(resized)
        self._preview_canvas.delete("all")
        self._preview_canvas.create_image(0, 0, anchor=tk.NW, image=self._inline_tk_img)
        self._preview_canvas.configure(scrollregion=(0, 0, new_w, new_h))
        self._inline_zoom_label.config(text=f"{int(self._inline_zoom * 100)}%")

    def _prev_drag_start(self, event):
        self._preview_canvas.scan_mark(event.x, event.y)

    def _prev_drag_move(self, event):
        self._preview_canvas.scan_dragto(event.x, event.y, gain=1)

    def _prev_wheel(self, event):
        if sys.platform == "win32":
            delta = -1 if event.delta < 0 else 1
        else:
            delta = -1 if event.num == 5 else 1
        self._preview_canvas.yview_scroll(-delta, "units")

    def _prev_ctrl_wheel(self, event):
        if sys.platform == "win32":
            delta = event.delta
        else:
            delta = -120 if event.num == 5 else 120
        if delta > 0:
            self._inline_zoom_in()
        else:
            self._inline_zoom_out()

    def _open_preview_window(self, *_):
        """選択中ファイルを別ウィンドウで大きく表示する。"""
        sel = self._tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._results):
            return
        file_path = self._results[idx].entry.file_path
        query     = self._search_var.get().strip()

        from src.preview.preview_panel import PreviewWindow
        PreviewWindow(self, file_path, config=self._config, highlight_text=query)

    def _index_folder(self):
        folder = filedialog.askdirectory(title="インデックス登録するフォルダを選択")
        if not folder:
            return
        recursive = messagebox.askyesno(
            "サブフォルダ",
            "サブフォルダも再帰的にスキャンしますか？\nScan subfolders recursively?",
        )
        self._run_indexing(folder=folder, recursive=recursive, force=False)

    def _index_folder_force(self):
        """強制再インデックス: 変更なしファイルも含めてすべて再解析する."""
        folder = filedialog.askdirectory(title="強制再インデックスするフォルダを選択")
        if not folder:
            return
        recursive = messagebox.askyesno(
            "サブフォルダ",
            "サブフォルダも再帰的にスキャンしますか？\nScan subfolders recursively?",
        )
        self._run_indexing(folder=folder, recursive=recursive, force=True)

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

    def _run_indexing(self, folder: str, recursive: bool, force: bool = False):
        if self._index_thread and self._index_thread.is_alive():
            messagebox.showwarning("処理中", "インデックス登録が既に実行中です。")
            return

        progress_win = tk.Toplevel(self)
        progress_win.title("インデックス登録中…")
        progress_win.geometry("520x210")
        progress_win.resizable(False, False)
        progress_win.grab_set()

        # ---- Header ----
        header = tk.Frame(progress_win, bg=BG_COLOR)
        header.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(
            header, text="ファイルをスキャン中…", font=FONT_MAIN, bg=BG_COLOR
        ).pack(side="left")
        speed_label = tk.Label(header, text="", font=("", 9), bg=BG_COLOR, fg="#666")
        speed_label.pack(side="right")

        # ---- Current file name ----
        file_label = tk.Label(
            progress_win, text="準備中…", font=("", 9), wraplength=490,
            anchor="w", justify="left"
        )
        file_label.pack(fill="x", padx=16, pady=(4, 0))

        # ---- Progress bar ----
        pbar = ttk.Progressbar(progress_win, mode="determinate", length=490)
        pbar.pack(padx=16, pady=6)

        # ---- Count + ETA ----
        info_frame = tk.Frame(progress_win)
        info_frame.pack(fill="x", padx=16)
        count_label = tk.Label(info_frame, text="0 / 0", font=("", 9))
        count_label.pack(side="left")
        eta_label = tk.Label(info_frame, text="", font=("", 9), fg="#444")
        eta_label.pack(side="right")

        # ---- Skip / Error info ----
        skip_label = tk.Label(
            progress_win, text="", font=("", 8), fg="#888"
        )
        skip_label.pack(pady=(2, 0))

        # ---- Cancel button ----
        cancelled = [False]
        def _cancel():
            cancelled[0] = True
            cancel_btn.config(state="disabled", text="キャンセル中…")
        cancel_btn = tk.Button(
            progress_win, text="キャンセル", command=_cancel, width=12
        )
        cancel_btn.pack(pady=(6, 10))

        # Timing state shared between callback and task
        _t_start = [time.time()]
        _skipped = [0]
        _errors_count = [0]

        def progress_cb(fpath, current, total):
            if cancelled[0]:
                return
            pct = int(current / total * 100) if total > 0 else 0
            elapsed = time.time() - _t_start[0]
            rate = current / elapsed if elapsed > 0.1 else 0
            remaining = (total - current) / rate if rate > 0.5 else 0

            fname = os.path.basename(fpath)
            speed_str = f"{rate:.1f} files/s" if rate > 0 else ""
            eta_str = _fmt_eta(remaining) if remaining > 0 else ""
            skip_str = f"スキップ(変更なし): {_skipped[0]}  エラー: {_errors_count[0]}"

            def _update():
                file_label.config(text=fname)
                pbar.config(value=pct)
                count_label.config(text=f"{current} / {total}")
                speed_label.config(text=speed_str)
                eta_label.config(text=eta_str)
                skip_label.config(text=skip_str)
            self.after(0, _update)

        def task():
            import time as _time
            _t_start[0] = _time.time()
            indexed, errors = self.engine.index_directory(
                folder,
                recursive=recursive,
                progress_callback=progress_cb,
                force=force,
            )
            _errors_count[0] = errors
            # Estimate skipped: total indexed – (indexed that were freshly parsed)
            # We report it from the engine's perspective: unchanged = index_size – errors
            if not cancelled[0]:
                self.after(0, progress_win.destroy)
                self.after(0, lambda: self._status_var.set(
                    f"登録完了: {indexed} 件  エラー: {errors} 件  "
                    f"インデックス合計: {self.engine.index_size} ファイル"
                ))
                self.after(0, lambda: messagebox.showinfo(
                    "完了",
                    f"インデックス登録が完了しました。\n\n"
                    f"  成功 : {indexed} ファイル\n"
                    f"  エラー: {errors} ファイル\n"
                    f"  合計インデックス: {self.engine.index_size} ファイル"
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
    #  CSV Export                                                          #
    # ------------------------------------------------------------------ #

    def _export_csv(self, *_):
        """検索結果をCSVファイルに保存するダイアログを開く。"""
        # 出力対象を選択
        if self._results:
            choice = self._ask_csv_target()
            if choice is None:
                return   # キャンセル
        else:
            # 検索結果がない場合はインデックス全件を対象にする
            choice = "index"

        # 保存先ダイアログ
        ts    = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"search_results_{ts}.csv" if choice == "results" else f"index_all_{ts}.csv"
        save_path = filedialog.asksaveasfilename(
            title="CSV を保存 (Save CSV)",
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV ファイル", "*.csv"), ("すべてのファイル", "*")],
        )
        if not save_path:
            return

        try:
            if choice == "results":
                self._write_results_csv(save_path, self._results)
                count = len(self._results)
            else:
                self._write_index_csv(save_path, list(self.engine.indexed_files))
                count = self.engine.index_size

            self._status_var.set(f"CSV 保存完了: {count} 件 → {save_path}")

            # 保存後に開くか確認
            if messagebox.askyesno(
                "CSV 保存完了",
                f"{count} 件を保存しました。\n\n{save_path}\n\nファイルを開きますか？",
            ):
                self._os_open(save_path)

        except Exception as exc:
            messagebox.showerror("CSV 保存エラー", str(exc))

    def _ask_csv_target(self) -> Optional[str]:
        """検索結果 / インデックス全件 のどちらを出力するか選択させる。"""
        win = tk.Toplevel(self)
        win.title("CSV 出力対象")
        win.resizable(False, False)
        win.grab_set()

        # ウィンドウを親の中央に配置
        self.update_idletasks()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        win.geometry(f"340x160+{px + pw//2 - 170}+{py + ph//2 - 80}")

        choice = tk.StringVar(value="results")

        tk.Label(win, text="出力する内容を選択してください",
                 font=FONT_BOLD, pady=8).pack()
        tk.Radiobutton(
            win, text=f"現在の検索結果 ({len(self._results)} 件)",
            variable=choice, value="results", font=FONT_MAIN,
        ).pack(anchor=tk.W, padx=24)
        tk.Radiobutton(
            win, text=f"インデックス全件 ({self.engine.index_size} 件)",
            variable=choice, value="index", font=FONT_MAIN,
        ).pack(anchor=tk.W, padx=24)

        result: List[Optional[str]] = [None]

        def ok():
            result[0] = choice.get()
            win.destroy()

        def cancel():
            win.destroy()

        btn_frame = tk.Frame(win)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="OK",       width=10, command=ok,     bg="#1565c0", fg="white").pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="キャンセル", width=10, command=cancel, bg="#546e7a", fg="white").pack(side=tk.LEFT, padx=6)

        win.bind("<Return>", lambda _: ok())
        win.bind("<Escape>", lambda _: cancel())
        win.wait_window()
        return result[0]

    @staticmethod
    def _write_results_csv(path: str, results: List[SearchResult]):
        """検索結果リストを CSV に書き出す。"""
        # BOM 付き UTF-8 で保存 (Excel でも文字化けしない)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ファイル名",
                "図面番号",
                "マッチ種別",
                "マッチ値",
                "スコア",
                "ファイル種別",
                "タイトル",
                "エラー",
                "ファイルパス",
                "フォルダ",
            ])
            for r in results:
                e = r.entry
                writer.writerow([
                    e.filename,
                    "; ".join(e.drawing_numbers) if e.drawing_numbers else "",
                    r.match_type,
                    r.matched_value,
                    f"{r.score:.3f}",
                    e.file_type.upper(),
                    e.title or "",
                    e.error or "",
                    e.file_path,
                    os.path.dirname(e.file_path),
                ])

    @staticmethod
    def _write_index_csv(path: str, entries: list):
        """インデックス全件を CSV に書き出す。"""
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ファイル名",
                "ファイル種別",
                "図面番号",
                "タイトル",
                "抽出テキスト冒頭500字",
                "エラー",
                "ファイルパス",
                "フォルダ",
            ])
            for e in entries:
                writer.writerow([
                    e.filename,
                    e.file_type.upper(),
                    "; ".join(e.drawing_numbers) if e.drawing_numbers else "",
                    e.title or "",
                    e.texts_blob[:500].replace("\n", " "),
                    e.error or "",
                    e.file_path,
                    os.path.dirname(e.file_path),
                ])

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

    # ------------------------------------------------------------------ #
    #  ODA Settings & Diagnostics                                          #
    # ------------------------------------------------------------------ #

    def _open_oda_settings(self):
        """ODA File Converter のパスを GUI から設定するダイアログ。"""
        win = tk.Toplevel(self)
        win.title("ODA File Converter 設定")
        win.geometry("620x340")
        win.resizable(False, False)
        win.grab_set()

        pad = {"padx": 16, "pady": 6}

        # ── 現在の状態表示 ─────────────────────────────────────────
        current_path = self._config.get("oda_path", "")
        oda_found    = find_oda_executable(current_path)
        status_color = "#2e7d32" if oda_found else "#c62828"
        status_text  = f"✅ 検出: {oda_found}" if oda_found else "❌ 未検出 — パスを手動で設定してください"

        tk.Label(win, text="ODA File Converter の実行ファイルパス",
                 font=FONT_BOLD).pack(anchor=tk.W, **pad)
        tk.Label(win, text=status_text, fg=status_color, font=("", 9),
                 wraplength=580).pack(anchor=tk.W, padx=16)

        # ── パス入力欄 ─────────────────────────────────────────────
        frm = tk.Frame(win)
        frm.pack(fill=tk.X, padx=16, pady=8)

        path_var = tk.StringVar(value=current_path)
        entry = ttk.Entry(frm, textvariable=path_var, width=58)
        entry.pack(side=tk.LEFT, padx=(0, 6))

        def browse():
            if sys.platform == "win32":
                ft = [("実行ファイル", "*.exe"), ("すべて", "*.*")]
            else:
                ft = [("すべて", "*")]
            p = filedialog.askopenfilename(title="ODAFileConverter を選択", filetypes=ft)
            if p:
                path_var.set(p)

        ttk.Button(frm, text="参照…", command=browse).pack(side=tk.LEFT)

        # ── バージョン選択 ─────────────────────────────────────────
        ver_frame = tk.Frame(win)
        ver_frame.pack(fill=tk.X, padx=16, pady=4)
        tk.Label(ver_frame, text="変換バージョン:", font=FONT_MAIN).pack(side=tk.LEFT)
        ver_var = tk.StringVar(value=self._config.get("oda_version", "ACAD2018"))
        versions = ["ACAD2018", "ACAD2013", "ACAD2010", "ACAD2007", "ACAD2004", "ACAD2000", "ACAD14", "ACAD12"]
        ttk.Combobox(ver_frame, textvariable=ver_var, values=versions,
                     state="readonly", width=14).pack(side=tk.LEFT, padx=8)
        tk.Label(ver_frame, text="(通常は ACAD2018 で問題なし)",
                 font=("", 9), fg="#555").pack(side=tk.LEFT)

        # ── Audit チェック ─────────────────────────────────────────
        audit_var = tk.BooleanVar(value=self._config.get("oda_audit", True))
        ttk.Checkbutton(win, text="変換時に Audit を実行する (推奨)",
                        variable=audit_var).pack(anchor=tk.W, padx=16, pady=2)

        # ── ダウンロードリンク ──────────────────────────────────────
        tk.Label(win,
                 text="📥 未インストールの場合: https://www.opendesign.com/guestfiles/oda_file_converter",
                 fg="#1565c0", cursor="hand2", font=("", 9)).pack(anchor=tk.W, padx=16, pady=4)

        # ── ボタン ─────────────────────────────────────────────────
        btn_frame = tk.Frame(win)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=12)

        def test_and_save():
            p   = path_var.get().strip()
            ver = ver_var.get()
            found = find_oda_executable(p)
            if found:
                self._config["oda_path"]    = p
                self._config["oda_version"] = ver
                self._config["oda_audit"]   = audit_var.get()
                save_config(self._config)
                # エンジンに反映
                self.engine.config.update(self._config)
                messagebox.showinfo("保存完了",
                    f"設定を保存しました。\n\n"
                    f"ODA パス : {found}\n"
                    f"バージョン: {ver}",
                    parent=win)
                win.destroy()
            else:
                messagebox.showerror("エラー",
                    f"ODA File Converter が見つかりません:\n{p or '(空)'}",
                    parent=win)

        def save_only():
            self._config["oda_path"]    = path_var.get().strip()
            self._config["oda_version"] = ver_var.get()
            self._config["oda_audit"]   = audit_var.get()
            save_config(self._config)
            self.engine.config.update(self._config)
            win.destroy()

        ttk.Button(btn_frame, text="テスト & 保存", command=test_and_save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="保存",          command=save_only).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="キャンセル",    command=win.destroy).pack(side=tk.LEFT)

    def _check_oda(self):
        """ODA の検出状態を詳しく表示する診断ダイアログ（実行テスト付き）。"""
        custom_path = self._config.get("oda_path", "")
        oda_exe     = find_oda_executable(custom_path)

        lines = ["─── ODA File Converter 診断レポート ───\n"]

        if oda_exe:
            lines.append(f"✅ 検出: {oda_exe}\n")
            # バージョン確認
            try:
                result = subprocess.run(
                    [oda_exe, "--version"],
                    capture_output=True, text=True, timeout=10
                )
                ver_out = (result.stdout + result.stderr).strip()
                if ver_out:
                    lines.append(f"バージョン情報: {ver_out[:200]}")
            except Exception as e:
                lines.append(f"バージョン取得不可 ({e})")

            # ── ezdxf.options への登録確認 ─────────────────────────────
            lines.append("")
            lines.append("─── ezdxf 設定状態 ───")
            try:
                import ezdxf
                from src.extractors.dwg_extractor import set_odafc_path
                set_odafc_path(oda_exe)   # 診断時にも確実に登録
                if sys.platform == "win32":
                    registered = ezdxf.options.get("odafc-addon", "win_exec_path").strip('"')
                else:
                    registered = ezdxf.options.get("odafc-addon", "unix_exec_path").strip('"')
                match = "✅" if registered == oda_exe else "⚠️"
                lines.append(f"  {match} ezdxf options 登録値: {registered or '(空)'}")
                lines.append(f"  期待値: {oda_exe}")
            except Exception as ex:
                lines.append(f"  ⚠️ ezdxf 設定確認エラー: {ex}")

            # ── DISPLAY 環境変数の確認 (Linux/macOS) ─────────────────
            if sys.platform != "win32":
                lines.append("")
                lines.append("─── 表示環境 (Linux/macOS) ───")
                disp = os.environ.get("DISPLAY", "")
                xvfb = shutil.which("Xvfb")
                xvfb_run = shutil.which("xvfb-run")
                lines.append(f"  DISPLAY 環境変数 : {disp or '(未設定)'}")
                lines.append(f"  Xvfb            : {'✅ ' + xvfb if xvfb else '❌ 未インストール'}")
                lines.append(f"  xvfb-run        : {'✅ ' + xvfb_run if xvfb_run else '❌ 未インストール'}")
                if not disp and not xvfb:
                    lines.append("  ⚠️ DISPLAY が未設定で Xvfb もない場合、")
                    lines.append("     ODA が GUI を開こうとしてクラッシュする可能性があります。")
                    lines.append("     対処法: sudo apt install xvfb  または")
                    lines.append("             export DISPLAY=:0  (X Server が起動している場合)")

            # ── 実際の変換テスト ──────────────────────────────────────
            lines.append("")
            lines.append("─── 変換テスト (最小 DWG ファイル) ───")
            try:
                import tempfile as _tmp
                from src.extractors.dwg_extractor import _ensure_display_env
                env = _ensure_display_env()

                # 最小限の DWG ヘッダーを持つダミーファイルで動作確認
                # (実際の変換は行えないが ODA が起動するか確認できる)
                with _tmp.TemporaryDirectory() as _td:
                    _fin  = os.path.join(_td, "test_in")
                    _fout = os.path.join(_td, "test_out")
                    os.makedirs(_fin); os.makedirs(_fout)

                    cmd_test = [oda_exe, _fin, _fout, "ACAD2018", "DXF", "0", "0"]
                    if sys.platform != "win32" and shutil.which("xvfb-run"):
                        cmd_test = ["xvfb-run", "-a"] + cmd_test

                    run_kwargs: dict = dict(capture_output=True, timeout=30, env=env)
                    if sys.platform == "win32":
                        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

                    try:
                        proc = subprocess.run(cmd_test, **run_kwargs)
                        rc = proc.returncode

                        def _dec(b):
                            if not b: return ""
                            for enc in ("utf-8", "cp932", "cp1252", "latin-1"):
                                try: return b.decode(enc)
                                except Exception: pass
                            return b.decode("utf-8", errors="replace")

                        out = (_dec(proc.stdout) + _dec(proc.stderr)).strip()
                        if rc == 0 or "converted" in out.lower() or out == "":
                            lines.append(f"  ✅ ODA 起動成功 (returncode={rc})")
                        else:
                            lines.append(f"  ⚠️ ODA 起動 returncode={rc}")
                        if out:
                            lines.append(f"  出力: {out[:300]}")
                        if rc != 0 and sys.platform == "win32":
                            lines.append("  ℹ️ Windows では ODA が空フォルダを変換しようとして")
                            lines.append("     エラーを返すことがあります (正常動作の場合もあります)")
                    except subprocess.TimeoutExpired:
                        lines.append("  ❌ ODA がタイムアウト (30秒) — GUI が固まっている可能性")
                        if sys.platform != "win32":
                            lines.append("     xvfb (仮想ディスプレイ) のインストールを推奨")
                        else:
                            lines.append("     管理者権限で実行を試してください")
                    except Exception as ex2:
                        lines.append(f"  ❌ ODA 実行エラー: {ex2}")
            except Exception as ex:
                lines.append(f"  テスト実行不可: {ex}")

        else:
            lines.append("❌ ODA File Converter が見つかりません\n")
            lines.append("確認した候補パス:")
            if sys.platform == "win32":
                candidates = [
                    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
                    r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
                ]
                for c in candidates:
                    mark = "✅" if os.path.isfile(c) else "❌"
                    lines.append(f"  {mark} {c}")
            found_cmd = shutil.which("ODAFileConverter")
            lines.append(f"  {'✅' if found_cmd else '❌'} PATH上: ODAFileConverter "
                         f"({'→ ' + found_cmd if found_cmd else '未発見'})")

        lines.append(f"\n設定ファイルのパス: {custom_path or '(未設定)'}")
        lines.append(f"設定バージョン    : {self._config.get('oda_version', 'ACAD2018')}")
        lines.append(f"Audit             : {self._config.get('oda_audit', True)}")
        lines.append("\n─── インストール方法 ───")
        lines.append("Windows : https://www.opendesign.com/guestfiles/oda_file_converter")
        lines.append("          .msi をダウンロードしてインストール")
        lines.append("Linux   : .deb → sudo gdebi ODAFileConverter*.deb")
        lines.append("          .rpm → sudo rpm -i ODAFileConverter*.rpm")
        lines.append("          AppImage → chmod +x *.AppImage で実行可能に")
        lines.append("macOS   : .dmg をマウントしてインストール")
        lines.append("\nインストール後、メニュー「設定 > ODA File Converter 設定」でパスを登録してください。")

        win = tk.Toplevel(self)
        win.title("ODA 診断")
        win.geometry("720x520")
        txt = scrolledtext.ScrolledText(win, font=FONT_MONO, wrap=tk.WORD,
                                         bg="#1e1e1e", fg="#d4d4d4")
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt.insert(tk.END, "\n".join(lines))
        txt.configure(state=tk.DISABLED)

        ttk.Button(win, text="設定を開く",
                   command=lambda: (win.destroy(), self._open_oda_settings())
                   ).pack(pady=(0, 8))

    def _show_oda_guide(self):
        """ODA File Converter の連携手順を表示。"""
        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ODA File Converter 連携ガイド\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "【なぜ必要か】\n"
            "  DWG は Autodesk 独自の非公開フォーマットです。\n"
            "  ODA File Converter (無料) を使うことで\n"
            "  DWG → DXF に変換し、高精度なテキスト抽出が可能になります。\n\n"
            "【STEP 1: ダウンロード】\n"
            "  https://www.opendesign.com/guestfiles/oda_file_converter\n"
            "  ※ アカウント不要・完全無料\n\n"
            "【STEP 2: インストール】\n"
            "  Windows  → .msi を実行\n"
            "  Linux    → sudo gdebi ODAFileConverter*.deb\n"
            "             または sudo rpm -i ODAFileConverter*.rpm\n"
            "  macOS    → .dmg をマウントして Applications へ\n\n"
            "【STEP 3: このアプリに登録】\n"
            "  メニュー「設定 > ODA File Converter 設定」を開き\n"
            "  インストールした実行ファイルのパスを指定して保存。\n\n"
            "【デフォルトインストール先】\n"
            "  Windows: C:\\Program Files\\ODA\\ODAFileConverter\\\n"
            "           ODAFileConverter.exe\n"
            "  Linux:   /usr/bin/ODAFileConverter\n"
            "  macOS:   /Applications/ODAFileConverter.app/\n"
            "           Contents/MacOS/ODAFileConverter\n\n"
            "【動作確認】\n"
            "  メニュー「設定 > ODA 接続テスト」で検出状態を確認できます。\n"
        )
        win = tk.Toplevel(self)
        win.title("ODA 連携ガイド")
        win.geometry("580x540")
        txt = scrolledtext.ScrolledText(win, font=FONT_MAIN, wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt.insert(tk.END, msg)
        txt.configure(state=tk.DISABLED)
        ttk.Button(win, text="設定を開く",
                   command=lambda: (win.destroy(), self._open_oda_settings())
                   ).pack(pady=(0, 8))


# ---------------------------------------------------------------------------#

def main():
    import re   # ensure import available for search
    app = DrawingSearchApp()
    app.mainloop()


if __name__ == "__main__":
    main()
