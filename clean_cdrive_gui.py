#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clean-CDrive GUI — C 盘安全清理图形界面 (tkinter)
依赖: clean_cdrive.py（作为后端模块）
"""

import tkinter as tk
from tkinter import ttk
import threading
import queue
import logging
import os
import sys
from pathlib import Path

# ── 导入后端模块 ──────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(_SCRIPT_DIR))
import clean_cdrive as backend


# ── 自定义日志处理器 ────────────────────────────────────

class QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.q.put(self.format(record))


CLEAN_TASKS = [
    ("临时文件夹",       backend.clean_temp),
    ("预读取缓存",       backend.clean_prefetch),
    ("更新下载缓存",     backend.clean_windows_update),
    ("错误报告 (WER)",   backend.clean_wer),
    ("用户应用缓存",     backend.clean_user_cache),
    ("浏览器缓存",       backend.clean_browser_cache),
    ("传递优化缓存",     backend.clean_delivery_opt),
    ("回收站",           backend.clean_recycle_bin),
    ("最近文件列表",     backend.clean_user_recent),
    ("图标缓存",         backend.clean_icon_cache),
    ("旧系统日志",       backend.clean_old_logs),
    ("已卸载程序残留",   backend.search_orphan_appdata),
    ("Windows.old",      backend.clean_windows_old),
    ("磁盘清理 CleanMgr", backend.run_cleanmgr),
]


class CleanCDriveGUI:

    BG = "#ffffff"
    FG = "#1a1a1a"
    ACCENT = "#0078d4"
    SUCCESS = "#107c10"
    WARN_ORANGE = "#d83b01"
    HEADING = "#1a1a1a"
    MUTED = "#888"
    CARD_BG = "#ffffff"
    BORDER = "#e0e0e0"
    LOG_BG = "#1e1e1e"
    LOG_FG = "#d4d4d4"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Clean-CDrive — C 盘安全清理")
        self.root.geometry("720x700")
        self.root.minsize(640, 600)
        self.root.configure(bg="#f0f0f0")

        self.running = False
        self.log_q: queue.Queue = queue.Queue()
        self.check_vars: list[tk.BooleanVar] = []
        self.mode_var = tk.StringVar(value="preview")
        self.orphan_var = tk.StringVar(value="list")

        self._setup_logging()
        self._build_ui()
        self._update_disk()
        self._poll_log()
        self.root.mainloop()

    # ── 日志 ─────────────────────────────────────────────────

    def _setup_logging(self):
        qh = QueueHandler(self.log_q)
        # backend logger
        backend.log.handlers.clear()
        backend.log.addHandler(qh)
        backend.log.setLevel(logging.INFO)
        # root logger (防止 print 遗漏)
        for h in logging.root.handlers[:]:
            logging.root.removeHandler(h)
        logging.root.addHandler(qh)
        logging.root.setLevel(logging.INFO)

    # ── 界面组件 ─────────────────────────────────────────────

    def _card(self, parent, **kw):
        return tk.Frame(parent, bg=self.CARD_BG,
                        highlightbackground=self.BORDER,
                        highlightthickness=1, **kw)

    def _build_ui(self):
        r = self.root

        # ── 顶栏 ──
        top = tk.Frame(r, bg=self.ACCENT, height=52)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Label(top, text="Clean-CDrive — C 盘安全清理",
                 font=("Segoe UI Semibold", 14), bg=self.ACCENT,
                 fg="white").pack(side=tk.LEFT, padx=18)

        # ── 磁盘状态 ──
        self.disk_lbl = tk.Label(r, font=("Segoe UI", 10),
                                 bg="#f0f0f0", fg=self.HEADING,
                                 anchor="w", padx=4, pady=2)
        self.disk_lbl.pack(fill=tk.X, padx=16, pady=(8, 4))

        # ── 任务列表 ──
        card = self._card(r)
        card.pack(fill=tk.X, padx=16, pady=(0, 6))

        tk.Label(card, text="选择要清理的项目：",
                 font=("Segoe UI Semibold", 11), bg=self.CARD_BG,
                 fg=self.HEADING).pack(anchor="w", padx=14, pady=(8, 4))

        grid = tk.Frame(card, bg=self.CARD_BG)
        grid.pack(fill=tk.X, padx=14, pady=(0, 8))

        for i, (label, func) in enumerate(CLEAN_TASKS):
            var = tk.BooleanVar(value=True)
            self.check_vars.append(var)
            cb = tk.Checkbutton(grid, text=label, variable=var,
                                font=("Segoe UI", 10), bg=self.CARD_BG,
                                fg=self.FG, activebackground=self.CARD_BG,
                                selectcolor=self.CARD_BG)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=4, pady=1)

        # ── 模式选择 ──
        mode_card = self._card(r)
        mode_card.pack(fill=tk.X, padx=16, pady=(0, 6))

        # 运行模式
        row1 = tk.Frame(mode_card, bg=self.CARD_BG)
        row1.pack(fill=tk.X, padx=14, pady=(8, 2))
        tk.Label(row1, text="运行模式：", font=("Segoe UI Semibold", 10),
                 bg=self.CARD_BG, fg=self.HEADING).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(row1, text="预览（不删除）", variable=self.mode_var,
                       value="preview", font=("Segoe UI", 10),
                       bg=self.CARD_BG, selectcolor=self.CARD_BG
                       ).pack(side=tk.LEFT, padx=(0, 16))
        tk.Radiobutton(row1, text="执行清理", variable=self.mode_var,
                       value="clean", font=("Segoe UI", 10),
                       bg=self.CARD_BG, selectcolor=self.CARD_BG
                       ).pack(side=tk.LEFT)

        tk.Frame(mode_card, bg=self.BORDER, height=1).pack(fill=tk.X, padx=14, pady=4)

        # 孤儿模式
        row2 = tk.Frame(mode_card, bg=self.CARD_BG)
        row2.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Label(row2, text="孤儿清理：", font=("Segoe UI Semibold", 10),
                 bg=self.CARD_BG, fg=self.HEADING).pack(side=tk.LEFT, padx=(0, 12))
        tk.Radiobutton(row2, text="仅列出（安全）", variable=self.orphan_var,
                       value="list", font=("Segoe UI", 10),
                       bg=self.CARD_BG, selectcolor=self.CARD_BG
                       ).pack(side=tk.LEFT, padx=(0, 16))
        tk.Radiobutton(row2, text="激进清理（删除残留）", variable=self.orphan_var,
                       value="clean", font=("Segoe UI", 10),
                       bg=self.CARD_BG, selectcolor=self.CARD_BG
                       ).pack(side=tk.LEFT)

        # ── 按钮 ──
        btn_frame = tk.Frame(r, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, padx=16, pady=(4, 6))

        self.run_btn = tk.Button(
            btn_frame, text="开始扫描", font=("Segoe UI Semibold", 12),
            bg=self.ACCENT, fg="white", bd=0,
            activebackground="#106ebe", activeforeground="white",
            padx=28, pady=6, cursor="hand2",
            command=self._start
        )
        self.run_btn.pack(side=tk.LEFT)

        self.status_lbl = tk.Label(btn_frame, text="状态：就绪",
                                   font=("Segoe UI", 10), bg="#f0f0f0",
                                   fg="#666")
        self.status_lbl.pack(side=tk.LEFT, padx=(16, 0))

        # ── 日志输出 ──
        log_card = self._card(r)
        log_card.pack(fill=tk.BOTH, padx=16, pady=(0, 8), expand=True)

        self.log_txt = tk.Text(log_card, font=("Consolas", 9),
                               bg=self.LOG_BG, fg=self.LOG_FG,
                               bd=0, padx=8, pady=6, wrap=tk.WORD,
                               state=tk.DISABLED)
        self.log_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc = tk.Scrollbar(log_card, command=self.log_txt.yview)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_txt.configure(yscrollcommand=sc.set)

        # ── 底栏 ──
        ftr = tk.Frame(r, bg="#f0f0f0", height=26)
        ftr.pack(fill=tk.X)
        ftr.pack_propagate(False)
        tk.Label(ftr, font=("Segoe UI", 9), bg="#f0f0f0",
                 fg=self.MUTED).pack(side=tk.RIGHT, padx=16)

    # ── 磁盘信息 ─────────────────────────────────────────────

    def _update_disk(self):
        try:
            u = backend.shutil.disk_usage("C:\\")
            self.disk_lbl.config(
                text=(f"C:  已用 {u.used/1e9:.1f} GB / {u.total/1e9:.1f} GB"
                      f"  ({u.used/u.total*100:.0f}%)   "
                      f"可用 {u.free/1e9:.1f} GB")
            )
        except Exception:
            self.disk_lbl.config(text="C:   无法读取磁盘信息")

    # ── 日志轮询 ─────────────────────────────────────────────

    def _poll_log(self):
        while not self.log_q.empty():
            msg = self.log_q.get_nowait()
            self.log_txt.config(state=tk.NORMAL)
            self.log_txt.insert(tk.END, msg + "\n")
            self.log_txt.see(tk.END)
            self.log_txt.config(state=tk.DISABLED)
        self.root.after(80, self._poll_log)

    # ── 执行 ─────────────────────────────────────────────────

    def _start(self):
        if self.running:
            return

        self.log_txt.config(state=tk.NORMAL)
        self.log_txt.delete("1.0", tk.END)
        self.log_txt.config(state=tk.DISABLED)

        selected = [
            (label, func) for i, (label, func) in enumerate(CLEAN_TASKS)
            if self.check_vars[i].get()
        ]
        if not selected:
            self._log("请至少勾选一个清理项目")
            return

        dry_run = self.mode_var.get() == "preview"
        aggressive = self.orphan_var.get() == "clean"
        mode = "预览模式" if dry_run else "执行模式"

        self._log("=" * 44)
        self._log(f"  Clean-CDrive — {mode}")
        self._log("=" * 44)
        self._log(f"  勾选 {len(selected)}/{len(CLEAN_TASKS)} 个项目")
        self._log(f"  孤儿清理: {'激进' if aggressive else '仅列出'}")

        self.running = True
        self.run_btn.config(text="运行中...", state=tk.DISABLED, bg="#888")
        self.status_lbl.config(text="状态：运行中", fg=self.WARN_ORANGE)

        def worker():
            try:
                for label, func in selected:
                    self._log("")
                    self._log(f"[{label}]")
                    name = func.__name__
                    if name == "search_orphan_appdata":
                        func(dry_run, aggressive)
                    else:
                        func(dry_run)

                backend.show_disk_usage()
                self._log("")
                self._log("=" * 44)
                self._log(f"  {'清理完成!' if not dry_run else '预览结束'}")
                self._log("=" * 44)

                self.root.after(0, self._update_disk)
            except Exception as e:
                self._log(f"\n[错误] {e}")
            finally:
                self.running = False
                self.root.after(0, self._finish)

        threading.Thread(target=worker, daemon=True).start()

    def _log(self, msg: str):
        self.log_q.put(msg)

    def _finish(self):
        self.run_btn.config(text="开始扫描", state=tk.NORMAL, bg=self.ACCENT)
        self.status_lbl.config(text="状态：完成", fg=self.SUCCESS)


if __name__ == "__main__":
    CleanCDriveGUI()
