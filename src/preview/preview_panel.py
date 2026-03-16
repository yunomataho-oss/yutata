"""
preview_panel.py — プレビューウィンドウ (Tkinter)

機能:
  ・ページ/レイアウト一覧（サムネイル付きサイドバー）
  ・メインキャンバスへの拡大表示
  ・マウスホイール・ドラッグでスクロール
  ・Ctrl+ホイール / +/- キーでズーム (25%〜400%)
  ・「ページ/前・次」ボタン
  ・ウィンドウ幅に合わせてフィット表示
  ・背景スレッドで非同期レンダリング（UIブロックなし）
  ・ハイライトナビゲーション (◀前 / 次▶ で全ページ通しで移動)
"""
from __future__ import annotations

import os
import sys
import threading
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

# ── 定数 ─────────────────────────────────────────────────────────────────
THUMB_W, THUMB_H = 120, 85          # サムネイルサイズ縮小 (旧 140×100)
MIN_ZOOM, MAX_ZOOM = 0.25, 4.0
ZOOM_STEP          = 0.15
BG_CANVAS          = "#2b2b2b"
BG_SIDEBAR         = "#1e1e1e"
FG_SIDEBAR         = "#cccccc"
SEL_COLOR          = "#1565c0"
FONT_SIDE          = ("Helvetica", 9)
FONT_BOLD          = ("Helvetica", 10, "bold")

# プラットフォーム別マウスホイールイベント
_WHEEL_UP   = "<Button-4>" if sys.platform != "win32" else "<MouseWheel>"
_WHEEL_DOWN = "<Button-5>" if sys.platform != "win32" else "<MouseWheel>"


class PreviewWindow(tk.Toplevel):
    """
    ファイルプレビューウィンドウ。
    独立した Toplevel として表示する。
    """

    def __init__(self, parent, file_path: str, config: Optional[dict] = None,
                 highlight_text: str = ""):
        super().__init__(parent)
        self.file_path      = file_path
        self.config         = config or {}
        self.highlight_text = highlight_text

        self._pages:   list = []   # list[PageResult]
        self._tk_imgs: List[Optional[ImageTk.PhotoImage]] = []
        self._current  = 0
        self._zoom     = 1.0
        self._drag_start: Optional[tuple] = None
        self._loading  = False
        self._thumb_tk_imgs: list = []   # サムネイル GC 防止

        # ハイライトナビゲーション
        self._hl_hit_list: list = []   # [(page_idx, x1,y1,x2,y2), ...]
        self._hl_hit_idx:  int  = -1

        fname = os.path.basename(file_path)
        self.title(f"プレビュー — {fname}")
        self.geometry("1000x760")
        self.minsize(600, 450)

        self._build_ui()
        self._start_load()

        # キーバインド
        self.bind("<plus>",       lambda _: self._zoom_in())
        self.bind("<minus>",      lambda _: self._zoom_out())
        self.bind("<equal>",      lambda _: self._zoom_in())
        self.bind("<0>",          lambda _: self._zoom_reset())
        self.bind("<f>",          lambda _: self._fit_window())
        self.bind("<Left>",       lambda _: self._prev_page())
        self.bind("<Right>",      lambda _: self._next_page())
        self.bind("<Escape>",     lambda _: self.destroy())
        self.bind("<n>",          lambda _: self._hl_next())
        self.bind("<p>",          lambda _: self._hl_prev())

    # ──────────────────────────────────────────────────────────────────
    #  UI 構築
    # ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── ツールバー ──────────────────────────────────────────────
        tb = tk.Frame(self, bg="#37474f", pady=4)
        tb.pack(side=tk.TOP, fill=tk.X)

        tk.Button(tb, text="◀ 前", command=self._prev_page,
                  bg="#546e7a", fg="white", relief=tk.FLAT,
                  padx=8).pack(side=tk.LEFT, padx=(8, 2))
        tk.Button(tb, text="次 ▶", command=self._next_page,
                  bg="#546e7a", fg="white", relief=tk.FLAT,
                  padx=8).pack(side=tk.LEFT, padx=(2, 12))

        self._page_label = tk.Label(tb, text="— / —", bg="#37474f", fg="white",
                                     font=FONT_BOLD, width=10)
        self._page_label.pack(side=tk.LEFT, padx=4)

        tk.Label(tb, text="ズーム:", bg="#37474f", fg="white").pack(side=tk.LEFT, padx=(20, 4))
        tk.Button(tb, text="−", command=self._zoom_out,
                  bg="#546e7a", fg="white", relief=tk.FLAT,
                  width=2).pack(side=tk.LEFT)

        self._zoom_label = tk.Label(tb, text="100%", bg="#37474f", fg="#ffd600",
                                     font=FONT_BOLD, width=6)
        self._zoom_label.pack(side=tk.LEFT, padx=2)

        tk.Button(tb, text="＋", command=self._zoom_in,
                  bg="#546e7a", fg="white", relief=tk.FLAT,
                  width=2).pack(side=tk.LEFT)
        tk.Button(tb, text="フィット", command=self._fit_window,
                  bg="#546e7a", fg="white", relief=tk.FLAT,
                  padx=6).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(tb, text="100%", command=self._zoom_reset,
                  bg="#546e7a", fg="white", relief=tk.FLAT,
                  padx=6).pack(side=tk.LEFT, padx=(4, 0))

        # ファイル名
        fname = os.path.basename(self.file_path)
        tk.Label(tb, text=fname, bg="#37474f", fg="#90caf9",
                 font=("Helvetica", 9)).pack(side=tk.RIGHT, padx=12)

        # ── ハイライトナビゲーションバー ────────────────────────────
        hl_bar = tk.Frame(self, bg="#455a64", pady=3)
        hl_bar.pack(side=tk.TOP, fill=tk.X)

        self._hl_info_var = tk.StringVar(value="")
        self._hl_info_lbl = tk.Label(
            hl_bar, textvariable=self._hl_info_var,
            bg="#455a64", fg="#ffd600", font=("Helvetica", 9),
        )
        self._hl_info_lbl.pack(side=tk.LEFT, padx=8)

        tk.Button(
            hl_bar, text="◀ 前の一致", command=self._hl_prev,
            bg="#f9a825", fg="#333", relief=tk.FLAT, padx=5,
            cursor="hand2", font=("Helvetica", 9),
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            hl_bar, text="次の一致 ▶", command=self._hl_next,
            bg="#f9a825", fg="#333", relief=tk.FLAT, padx=5,
            cursor="hand2", font=("Helvetica", 9),
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(
            hl_bar, text="(n=次  p=前)", bg="#455a64", fg="#90a4ae",
            font=("Helvetica", 8),
        ).pack(side=tk.LEFT, padx=4)

        # ── メインエリア (サイドバー + キャンバス) ──────────────────
        main_frame = tk.Frame(self, bg=BG_CANVAS)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # サイドバー（サムネイル）
        self._sidebar_frame = tk.Frame(main_frame, bg=BG_SIDEBAR, width=160)
        self._sidebar_frame.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar_frame.pack_propagate(False)

        sidebar_scroll = tk.Scrollbar(self._sidebar_frame, orient=tk.VERTICAL)
        sidebar_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._thumb_canvas = tk.Canvas(self._sidebar_frame, bg=BG_SIDEBAR,
                                        yscrollcommand=sidebar_scroll.set,
                                        width=155, highlightthickness=0)
        self._thumb_canvas.pack(fill=tk.BOTH, expand=True)
        sidebar_scroll.config(command=self._thumb_canvas.yview)

        self._thumb_inner = tk.Frame(self._thumb_canvas, bg=BG_SIDEBAR)
        self._thumb_canvas.create_window((0, 0), window=self._thumb_inner,
                                          anchor=tk.NW)
        self._thumb_inner.bind("<Configure>",
            lambda e: self._thumb_canvas.configure(
                scrollregion=self._thumb_canvas.bbox("all")))

        # メインキャンバス
        canvas_frame = tk.Frame(main_frame, bg=BG_CANVAS)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._vscroll = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        self._hscroll = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        self._vscroll.pack(side=tk.RIGHT,  fill=tk.Y)
        self._hscroll.pack(side=tk.BOTTOM, fill=tk.X)

        self._canvas = tk.Canvas(
            canvas_frame,
            bg=BG_CANVAS,
            yscrollcommand=self._vscroll.set,
            xscrollcommand=self._hscroll.set,
            highlightthickness=0,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._vscroll.config(command=self._canvas.yview)
        self._hscroll.config(command=self._canvas.xview)

        # キャンバスイベント
        self._canvas.bind("<ButtonPress-1>",   self._drag_start_cb)
        self._canvas.bind("<B1-Motion>",        self._drag_move_cb)
        self._canvas.bind("<ButtonRelease-1>",  self._drag_end_cb)
        self._canvas.bind("<Control-MouseWheel>", self._ctrl_wheel_cb)
        self._canvas.bind("<MouseWheel>",         self._wheel_cb)
        self._canvas.bind("<Button-4>",           self._wheel_cb)
        self._canvas.bind("<Button-5>",           self._wheel_cb)
        self._canvas.bind("<Control-Button-4>",   self._ctrl_wheel_cb)
        self._canvas.bind("<Control-Button-5>",   self._ctrl_wheel_cb)

        # ── ステータスバー ───────────────────────────────────────────
        self._status_var = tk.StringVar(value="読み込み中…")
        tk.Label(self, textvariable=self._status_var, anchor=tk.W,
                 bg="#eceff1", font=("", 9)).pack(side=tk.BOTTOM, fill=tk.X)

    # ──────────────────────────────────────────────────────────────────
    #  非同期ロード
    # ──────────────────────────────────────────────────────────────────

    def _start_load(self):
        self._loading = True
        self._show_loading_placeholder()
        t = threading.Thread(target=self._load_thread, daemon=True)
        t.start()

    def _show_loading_placeholder(self):
        self._canvas.delete("all")
        w = self._canvas.winfo_width()  or 800
        h = self._canvas.winfo_height() or 600
        self._canvas.create_text(w // 2, h // 2,
                                  text="🔄 レンダリング中...",
                                  fill="#90caf9", font=("Helvetica", 18))

    def _load_thread(self):
        try:
            from src.preview.preview_engine import get_preview
            pages = get_preview(self.file_path, self.config,
                                highlight_text=self.highlight_text)
        except Exception as exc:
            from src.preview.preview_engine import _make_error_image, PageResult
            pages = [PageResult(_make_error_image("プレビュー生成エラー", str(exc)))]

        self._pages   = pages
        self._tk_imgs = [None] * len(pages)
        self._loading = False
        self.after(0, self._on_load_done)

    def _on_load_done(self):
        if not self._pages:
            return
        self._current = 0

        # ハイライトボックスを全ページ横断でフラット化
        hit_list = []
        for pg_idx, pr in enumerate(self._pages):
            boxes = getattr(pr, 'hit_boxes', [])
            for box in boxes:
                hit_list.append((pg_idx, box[0], box[1], box[2], box[3]))
        self._hl_hit_list = hit_list
        self._hl_hit_idx  = 0 if hit_list else -1
        self._update_hl_info()

        self._build_thumbnails()
        self._fit_window()
        self._update_page_label()

        # 最初のヒットがあれば自動スクロール
        if hit_list:
            first_pg = hit_list[0][0]
            if first_pg != self._current:
                self._current = first_pg
            self.after(150, lambda: self._scroll_to_hit(0))

    # ──────────────────────────────────────────────────────────────────
    #  ハイライトナビゲーション
    # ──────────────────────────────────────────────────────────────────

    def _update_hl_info(self):
        total = len(self._hl_hit_list)
        hl    = self.highlight_text.strip()
        if not hl or total == 0:
            self._hl_info_var.set("" if not hl else f"ヒットなし: '{hl}'")
        else:
            cur = self._hl_hit_idx + 1 if self._hl_hit_idx >= 0 else 0
            self._hl_info_var.set(f"ヒット {cur} / {total}  \"{hl}\"")

    def _hl_next(self):
        if not self._hl_hit_list:
            return
        self._hl_hit_idx = (self._hl_hit_idx + 1) % len(self._hl_hit_list)
        self._scroll_to_hit(self._hl_hit_idx)
        self._update_hl_info()

    def _hl_prev(self):
        if not self._hl_hit_list:
            return
        self._hl_hit_idx = (self._hl_hit_idx - 1) % len(self._hl_hit_list)
        self._scroll_to_hit(self._hl_hit_idx)
        self._update_hl_info()

    def _scroll_to_hit(self, hit_idx: int):
        """指定ヒットのページに切り替え、そのボックスが見えるようにスクロールする。"""
        if hit_idx < 0 or hit_idx >= len(self._hl_hit_list):
            return
        pg_idx, x1, y1, x2, y2 = self._hl_hit_list[hit_idx]

        # ページが違う場合は切り替え（fit は維持）
        if pg_idx != self._current:
            self._current = pg_idx
            self._show_page(self._current)

        # ボックス中心にスクロール（ズーム座標に変換）
        z   = self._zoom
        cx  = int((x1 + x2) / 2 * z)
        cy  = int((y1 + y2) / 2 * z)
        cw  = self._canvas.winfo_width()  or 800
        ch  = self._canvas.winfo_height() or 600
        pr  = self._pages[pg_idx]
        img = pr.image if hasattr(pr, 'image') else pr
        img_w = int(img.width  * z)
        img_h = int(img.height * z)
        if img_w > 0:
            self._canvas.xview_moveto(max(0.0, (cx - cw // 2) / img_w))
        if img_h > 0:
            self._canvas.yview_moveto(max(0.0, (cy - ch // 2) / img_h))

    # ──────────────────────────────────────────────────────────────────
    #  サムネイル
    # ──────────────────────────────────────────────────────────────────

    def _build_thumbnails(self):
        """サムネイルを生成してサイドバーに表示する。
        BILINEAR を使って高速化 (LANCZOS より約3倍速)。
        """
        # 既存サムネイルをクリア
        for w in self._thumb_inner.winfo_children():
            w.destroy()
        self._thumb_tk_imgs = []

        for i, pr in enumerate(self._pages):
            # PageResult または PIL.Image を吸収
            img = pr.image if hasattr(pr, 'image') else pr

            # サムネイル生成: BILINEAR で高速化
            thumb = img.copy()
            thumb.thumbnail((THUMB_W, THUMB_H), Image.BILINEAR)
            bg = Image.new("RGB", (THUMB_W, THUMB_H), "#3c3c3c")
            ox = (THUMB_W - thumb.width)  // 2
            oy = (THUMB_H - thumb.height) // 2
            bg.paste(thumb, (ox, oy))

            # ヒットがあるページはサムネイルに赤バッジ
            n_hits = len(getattr(pr, 'hit_boxes', []))
            if n_hits:
                from PIL import ImageDraw
                draw = ImageDraw.Draw(bg)
                draw.rectangle([0, 0, 36, 13], fill="#e53935")
                draw.text((2, 1), f"●{n_hits}", fill="white")

            tk_img = ImageTk.PhotoImage(bg)
            self._thumb_tk_imgs.append(tk_img)

            frame = tk.Frame(self._thumb_inner, bg=BG_SIDEBAR,
                             cursor="hand2", pady=3)
            frame.pack(fill=tk.X, padx=4, pady=1)

            lbl = tk.Label(frame, image=tk_img, bg=BG_SIDEBAR,
                           relief=tk.FLAT, bd=2)
            lbl.pack()

            # レイアウト名 + ヒット件数
            layout_name = (img.info.get("layout_name")
                           or getattr(pr, 'layout_name', '')
                           or f"P{i + 1}")
            hit_badge = f" [{n_hits}]" if n_hits else ""
            page_lbl = tk.Label(frame,
                                text=f"{layout_name[:18]}{hit_badge}",
                                bg=BG_SIDEBAR, fg="#ffd600" if n_hits else FG_SIDEBAR,
                                font=FONT_SIDE, wraplength=115)
            page_lbl.pack()

            idx = i
            lbl.bind("<Button-1>",      lambda _, n=idx: self._select_page(n))
            page_lbl.bind("<Button-1>", lambda _, n=idx: self._select_page(n))
            frame.bind("<Button-1>",    lambda _, n=idx: self._select_page(n))

        self._highlight_thumb(0)

    def _highlight_thumb(self, idx: int):
        frames = self._thumb_inner.winfo_children()
        for i, f in enumerate(frames):
            color = SEL_COLOR if i == idx else BG_SIDEBAR
            f.configure(bg=color)
            for child in f.winfo_children():
                child.configure(bg=color)

    # ──────────────────────────────────────────────────────────────────
    #  表示
    # ──────────────────────────────────────────────────────────────────

    def _show_page(self, idx: int):
        if not self._pages or idx < 0 or idx >= len(self._pages):
            return

        self._current = idx
        pr  = self._pages[idx]
        img = pr.image if hasattr(pr, 'image') else pr

        # ズーム適用
        new_w = max(1, int(img.width  * self._zoom))
        new_h = max(1, int(img.height * self._zoom))
        # ズーム率が高い場合は LANCZOS、低い場合は BILINEAR で高速化
        filt = Image.LANCZOS if self._zoom >= 1.0 else Image.BILINEAR
        resized  = img.resize((new_w, new_h), filt)
        tk_img   = ImageTk.PhotoImage(resized)

        # 前のイメージを保持（GC 防止）
        self._tk_imgs[idx] = tk_img

        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor=tk.NW, image=tk_img)
        self._canvas.configure(scrollregion=(0, 0, new_w, new_h))
        self._canvas.xview_moveto(0)
        self._canvas.yview_moveto(0)

        self._update_page_label()
        self._highlight_thumb(idx)

        layout_name = (img.info.get("layout_name")
                       or getattr(pr, 'layout_name', '')
                       or f"ページ {idx + 1}")
        n_hits = len(getattr(pr, 'hit_boxes', []))
        hit_str = f"  ●{n_hits}箇所ハイライト" if n_hits else ""
        self._status_var.set(
            f"{layout_name}{hit_str}  |  "
            f"原寸: {img.width}×{img.height}px  |  "
            f"ズーム: {int(self._zoom * 100)}%  |  "
            f"ヒント: Ctrl+ホイールでズーム / ドラッグ / F=フィット / n=次ヒット / p=前ヒット"
        )

    def _update_page_label(self):
        total = len(self._pages)
        cur   = self._current + 1 if self._pages else 0
        self._page_label.config(text=f"{cur} / {total}")
        self._zoom_label.config(text=f"{int(self._zoom * 100)}%")

    def _select_page(self, idx: int):
        self._show_page(idx)

    def _prev_page(self):
        if self._current > 0:
            self._show_page(self._current - 1)

    def _next_page(self):
        if self._current < len(self._pages) - 1:
            self._show_page(self._current + 1)

    # ──────────────────────────────────────────────────────────────────
    #  ズーム
    # ──────────────────────────────────────────────────────────────────

    def _zoom_in(self):
        self._zoom = min(MAX_ZOOM, self._zoom + ZOOM_STEP)
        self._show_page(self._current)

    def _zoom_out(self):
        self._zoom = max(MIN_ZOOM, self._zoom - ZOOM_STEP)
        self._show_page(self._current)

    def _zoom_reset(self):
        self._zoom = 1.0
        self._show_page(self._current)

    def _fit_window(self):
        if not self._pages:
            return
        pr  = self._pages[self._current]
        img = pr.image if hasattr(pr, 'image') else pr   # PageResult 対応
        cw  = self._canvas.winfo_width()  or 800
        ch  = self._canvas.winfo_height() or 600
        zw  = cw  / max(img.width,  1)
        zh  = ch  / max(img.height, 1)
        self._zoom = max(MIN_ZOOM, min(zw, zh) * 0.97)
        self._show_page(self._current)

    # ──────────────────────────────────────────────────────────────────
    #  マウスイベント
    # ──────────────────────────────────────────────────────────────────

    def _drag_start_cb(self, event):
        self._canvas.scan_mark(event.x, event.y)
        self._drag_start = (event.x, event.y)

    def _drag_move_cb(self, event):
        if self._drag_start:
            self._canvas.scan_dragto(event.x, event.y, gain=1)

    def _drag_end_cb(self, _event):
        self._drag_start = None

    def _wheel_cb(self, event):
        """通常スクロール（縦方向）。"""
        if sys.platform == "win32":
            delta = -1 if event.delta < 0 else 1
        else:
            delta = -1 if event.num == 5 else 1
        self._canvas.yview_scroll(-delta, "units")

    def _ctrl_wheel_cb(self, event):
        """Ctrl + ホイール でズーム。"""
        if sys.platform == "win32":
            delta = event.delta
        else:
            delta = -120 if event.num == 5 else 120
        if delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()


# ── 単体テスト用 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python preview_panel.py <file> [highlight_text]")
        sys.exit(1)
    hl = sys.argv[2] if len(sys.argv) > 2 else ""
    root = tk.Tk()
    root.withdraw()
    win = PreviewWindow(root, sys.argv[1], highlight_text=hl)
    root.mainloop()
