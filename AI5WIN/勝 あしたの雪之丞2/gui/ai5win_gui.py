#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI5WIN 工具集成 GUI

把当前目录下的 AI5WIN 命令行工具集成为 Tkinter 分页界面。
设计原则：
- 不改动底层工具算法，GUI 只负责参数收集、路径选择、日志显示和子进程调用。
- 每类功能独立分页，避免所有参数挤在同一页。
- G24 / MSK 相关功能提供原始 G24 图像、MSK 图层、参考图层路径输入。

运行：
  python ai5win_gui.py

依赖：
  Python 3.9+
  Pillow（G24/MSK/字库预览相关工具需要）
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "AI5WIN Integrated Tools GUI"
SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify="left", bg="#ffffe0", relief="solid", borderwidth=1,
                         padx=6, pady=3, wraplength=520)
        label.pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        canvas = tk.Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width))


class PathRow(ttk.Frame):
    def __init__(self, master, label: str, var: tk.StringVar, *, mode: str = "file", save: bool = False,
                 filetypes=None, width: int = 58, tooltip: str = ""):
        super().__init__(master)
        self.var = var
        self.mode = mode
        self.save = save
        self.filetypes = filetypes or [("All files", "*.*")]
        lab = ttk.Label(self, text=label, width=18, anchor="e")
        lab.pack(side="left", padx=(0, 6))
        ent = ttk.Entry(self, textvariable=var, width=width)
        ent.pack(side="left", fill="x", expand=True)
        btn = ttk.Button(self, text="浏览", width=7, command=self.browse)
        btn.pack(side="left", padx=(6, 0))
        if tooltip:
            ToolTip(lab, tooltip)
            ToolTip(ent, tooltip)

    def browse(self):
        initial = self.var.get().strip()
        initialdir = None
        if initial:
            p = Path(initial)
            initialdir = str(p if p.is_dir() else p.parent)
            if not Path(initialdir).exists():
                initialdir = None
        if self.mode == "dir":
            val = filedialog.askdirectory(initialdir=initialdir)
        elif self.save:
            val = filedialog.asksaveasfilename(initialdir=initialdir, filetypes=self.filetypes)
        else:
            val = filedialog.askopenfilename(initialdir=initialdir, filetypes=self.filetypes)
        if val:
            self.var.set(val)


class AnyPathRow(ttk.Frame):
    """Path row that can browse either a file or a directory.

    This is used for tools whose CLI accepts both single-file mode and batch-directory mode.
    """
    def __init__(self, master, label: str, var: tk.StringVar, *, save_file: bool = False,
                 filetypes=None, width: int = 54, tooltip: str = ""):
        super().__init__(master)
        self.var = var
        self.save_file = save_file
        self.filetypes = filetypes or [("All files", "*.*")]
        lab = ttk.Label(self, text=label, width=18, anchor="e")
        lab.pack(side="left", padx=(0, 6))
        ent = ttk.Entry(self, textvariable=var, width=width)
        ent.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="文件", width=6, command=self.browse_file).pack(side="left", padx=(6, 0))
        ttk.Button(self, text="目录", width=6, command=self.browse_dir).pack(side="left", padx=(4, 0))
        if tooltip:
            ToolTip(lab, tooltip)
            ToolTip(ent, tooltip)

    def _initialdir(self):
        initial = self.var.get().strip()
        if not initial:
            return None
        p = Path(initial)
        initialdir = str(p if p.is_dir() else p.parent)
        return initialdir if Path(initialdir).exists() else None

    def browse_file(self):
        if self.save_file:
            val = filedialog.asksaveasfilename(initialdir=self._initialdir(), filetypes=self.filetypes)
        else:
            val = filedialog.askopenfilename(initialdir=self._initialdir(), filetypes=self.filetypes)
        if val:
            self.var.set(val)

    def browse_dir(self):
        val = filedialog.askdirectory(initialdir=self._initialdir())
        if val:
            self.var.set(val)


class LogPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.text = tk.Text(self, height=12, wrap="word", font=("Consolas", 10))
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def write(self, s: str):
        self.text.insert("end", s)
        self.text.see("end")

    def clear(self):
        self.text.delete("1.0", "end")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x780")
        self.minsize(960, 660)
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.running = False

        self._build_styles()
        self._build_ui()
        self.after(80, self._poll_output)

    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Hint.TLabel", foreground="#555555")

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="AI5WIN 工具集成 GUI", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="清空日志", command=lambda: self.log.clear()).pack(side="right")
        ttk.Button(top, text="打开工具目录", command=self.open_script_dir).pack(side="right", padx=(0, 8))

        paned = ttk.Panedwindow(root, orient="vertical")
        paned.pack(fill="both", expand=True, pady=(10, 0))

        self.nb = ttk.Notebook(paned)
        paned.add(self.nb, weight=5)
        self.log = LogPanel(paned)
        paned.add(self.log, weight=2)

        self._build_arc_tab()
        self._build_mes_tab()
        self._build_font_tab()
        self._build_g24_tab()
        self._build_msk_tab()
        self._build_exe_tab()
        self._build_misc_tab()

    # ───────────────────────── command helpers ─────────────────────────
    def open_script_dir(self):
        path = str(SCRIPT_DIR)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def run_cmd(self, args: list[str], title: str = ""):
        if self.running:
            messagebox.showwarning("正在运行", "已有任务正在执行，请等当前任务结束。")
            return
        cmd = [PYTHON] + [str(x) for x in args]
        self.running = True
        self.log.write("\n" + "=" * 90 + "\n")
        self.log.write((title or "执行命令") + "\n")
        self.log.write("$ " + " ".join(cmd) + "\n\n")

        def worker():
            try:
                p = subprocess.Popen(cmd, cwd=str(SCRIPT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace")
                assert p.stdout is not None
                for line in p.stdout:
                    self.output_queue.put(line)
                rc = p.wait()
                self.output_queue.put(f"\n[进程结束] exit_code={rc}\n")
            except Exception as e:
                self.output_queue.put(f"\n[GUI ERROR] {e}\n")
            finally:
                self.output_queue.put("__RUN_DONE__")

        threading.Thread(target=worker, daemon=True).start()

    def _poll_output(self):
        try:
            while True:
                s = self.output_queue.get_nowait()
                if s == "__RUN_DONE__":
                    self.running = False
                else:
                    self.log.write(s)
        except queue.Empty:
            pass
        self.after(80, self._poll_output)

    def _require(self, *vars_: tk.StringVar) -> bool:
        missing = [v for v in vars_ if not v.get().strip()]
        if missing:
            messagebox.showerror("缺少路径", "请先填写必要的路径参数。")
            return False
        return True

    def _section(self, parent, title: str, hint: str = ""):
        lf = ttk.LabelFrame(parent, text=title, padding=10)
        lf.pack(fill="x", padx=6, pady=6)
        if hint:
            ttk.Label(lf, text=hint, style="Hint.TLabel", wraplength=980, justify="left").pack(fill="x", pady=(0, 8))
        return lf

    def _tab(self, title: str):
        sf = ScrollFrame(self.nb)
        self.nb.add(sf, text=title)
        return sf.inner

    # ───────────────────────── ARC ─────────────────────────
    def _build_arc_tab(self):
        tab = self._tab("ARC 封包/解包")
        arc_in = tk.StringVar(); arc_out = tk.StringVar()
        sec = self._section(tab, "ARC 解包", "解包 ARC 到目录，并生成 __filelist.txt 以保持重封包顺序。")
        PathRow(sec, "输入 ARC", arc_in, filetypes=[("ARC", "*.arc *.ARC"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "输出目录", arc_out, mode="dir").pack(fill="x", pady=3)
        ttk.Button(sec, text="开始解包", command=lambda: self._run_arc_unpack(arc_in, arc_out)).pack(anchor="e", pady=(8, 0))

        dir_in = tk.StringVar(); arc_save = tk.StringVar()
        sec = self._section(tab, "ARC 封包", "优先读取输入目录中的 __filelist.txt；没有时按文件名排序封包。")
        PathRow(sec, "输入目录", dir_in, mode="dir").pack(fill="x", pady=3)
        PathRow(sec, "输出 ARC", arc_save, save=True, filetypes=[("ARC", "*.arc"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="开始封包", command=lambda: self._run_arc_pack(dir_in, arc_save)).pack(anchor="e", pady=(8, 0))

    def _run_arc_unpack(self, src, out):
        if self._require(src, out):
            self.run_cmd(["ai5win_arc_tool.py", "unpack", src.get(), out.get()], "ARC 解包")

    def _run_arc_pack(self, src, out):
        if self._require(src, out):
            self.run_cmd(["ai5win_arc_tool.py", "pack", src.get(), out.get()], "ARC 封包")

    # ───────────────────────── MES ─────────────────────────
    def _build_mes_tab(self):
        tab = self._tab("MES 文本")
        mes_src = tk.StringVar(); json_out = tk.StringVar()
        sec = self._section(tab, "MES 提取", "支持单文件或目录。目录模式会批量输出同名 JSON。")
        PathRow(sec, "MES 文件/目录", mes_src, mode="file", filetypes=[("MES", "*.mes *.MES"), ("All", "*.*")],
                tooltip="可以直接输入目录路径；浏览按钮默认选文件。目录也可手动粘贴。" ).pack(fill="x", pady=3)
        PathRow(sec, "JSON/输出目录", json_out, save=True, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="开始提取", command=lambda: self._run_mes_extract(mes_src, json_out)).pack(anchor="e", pady=(8, 0))

        inj_src = tk.StringVar(); inj_json = tk.StringVar(); inj_out = tk.StringVar(); inj_map = tk.StringVar()
        sec = self._section(tab, "MES 注入", "使用 replace_map.json 将译文映射到 CP932 借码位，并修正跳转偏移。")
        PathRow(sec, "原 MES/目录", inj_src, filetypes=[("MES", "*.mes *.MES"), ("All", "*.*")], tooltip="可以输入单个 MES 或 MES 目录。目录模式会批量注入。" ).pack(fill="x", pady=3)
        PathRow(sec, "JSON/目录", inj_json, filetypes=[("JSON", "*.json"), ("All", "*.*")], tooltip="单文件注入填单个 JSON；目录注入填 JSON 目录。" ).pack(fill="x", pady=3)
        PathRow(sec, "输出文件/目录", inj_out, save=True, filetypes=[("MES", "*.mes"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "replace_map", inj_map, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="开始注入", command=lambda: self._run_mes_inject(inj_src, inj_json, inj_out, inj_map)).pack(anchor="e", pady=(8, 0))

    def _run_mes_extract(self, src, out):
        if self._require(src):
            args = ["ai5win_mes_extract.py", src.get()]
            if out.get().strip(): args.append(out.get())
            self.run_cmd(args, "MES 文本提取")

    def _run_mes_inject(self, src, jsrc, out, mapp):
        if self._require(src, jsrc, mapp):
            args = ["ai5win_mes_inject.py", src.get(), jsrc.get()]
            if out.get().strip(): args.append(out.get())
            args += ["--map", mapp.get()]
            self.run_cmd(args, "MES 文本注入")

    # ───────────────────────── Font ─────────────────────────
    def _build_font_tab(self):
        tab = self._tab("字库/映射流程")
        scan_src = tk.StringVar(); charset_out = tk.StringVar(value="build/charset.json")
        fields = tk.StringVar(value="name,message")
        no_norm = tk.BooleanVar(value=False); include_nl = tk.BooleanVar(value=False)
        sec = self._section(tab, "1. 扫描译文字符", "扫描 GalTransl JSON 的 name/message 字段，生成 charset.json。")
        PathRow(sec, "JSON/目录", scan_src, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "charset 输出", charset_out, save=True, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="字段", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(opt, textvariable=fields, width=30).pack(side="left")
        ttk.Checkbutton(opt, text="不规范化文本", variable=no_norm).pack(side="left", padx=16)
        ttk.Checkbutton(opt, text="包含换行", variable=include_nl).pack(side="left")
        ttk.Button(sec, text="生成 charset.json", command=lambda: self._run_scan_chars(scan_src, charset_out, fields, no_norm, include_nl)).pack(anchor="e", pady=(8,0))

        charset = tk.StringVar(value="build/charset.json"); fontdir = tk.StringVar(); replace_out = tk.StringVar(value="build/replace_map.json")
        cnjp = tk.StringVar(); banks = tk.StringVar(value="FONT00,FONT01,FONT02"); no_over = tk.BooleanVar(value=False)
        sec = self._section(tab, "2. 生成 replace_map", "从原始 FONT00/01/02 中挑选可借用的 CP932 双字节码位。")
        PathRow(sec, "charset.json", charset, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "原字库目录", fontdir, mode="dir").pack(fill="x", pady=3)
        PathRow(sec, "输出 map", replace_out, save=True, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "CnJpMap 可选", cnjp, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="Banks", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(opt, textvariable=banks, width=30).pack(side="left")
        ttk.Checkbutton(opt, text="禁止外部映射覆盖已有 glyph", variable=no_over).pack(side="left", padx=16)
        ttk.Button(sec, text="生成 replace_map", command=lambda: self._run_hanzi_map(charset, fontdir, replace_out, cnjp, banks, no_over)).pack(anchor="e", pady=(8,0))

        rep = tk.StringVar(value="build/replace_map.json"); ttf = tk.StringVar(); orig_font = tk.StringVar(); data_font_out = tk.StringVar(value="build/DATA_FONT")
        font_banks = tk.StringVar(value="FONT00,FONT01,FONT02"); font_size = tk.IntVar(value=22); mask_mode = tk.StringVar(value="clear")
        literal = tk.BooleanVar(value=False); no_fonthan = tk.BooleanVar(value=False)
        sec = self._section(tab, "3. 构建 DATA_FONT", "根据 replace_map 和 TTF 重绘字库，输出 FONT00/01/02/FONTHAN 与 build_manifest.json。")
        PathRow(sec, "replace_map", rep, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "字体 TTF/OTF", ttf, filetypes=[("Font", "*.ttf *.otf *.ttc"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "原字库目录", orig_font, mode="dir").pack(fill="x", pady=3)
        PathRow(sec, "输出目录", data_font_out, mode="dir").pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="Banks", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(opt, textvariable=font_banks, width=28).pack(side="left")
        ttk.Label(opt, text="字号").pack(side="left", padx=(16, 3))
        ttk.Spinbox(opt, from_=8, to=48, textvariable=font_size, width=6).pack(side="left")
        ttk.Label(opt, text="MSK").pack(side="left", padx=(16, 3))
        ttk.Combobox(opt, values=["clear", "smooth"], textvariable=mask_mode, width=10, state="readonly").pack(side="left")
        ttk.Checkbutton(opt, text="literal LZSS", variable=literal).pack(side="left", padx=16)
        ttk.Checkbutton(opt, text="不复制 FONTHAN", variable=no_fonthan).pack(side="left")
        ttk.Button(sec, text="构建字库", command=lambda: self._run_font_gen(rep, ttf, orig_font, data_font_out, font_banks, font_size, mask_mode, literal, no_fonthan)).pack(anchor="e", pady=(8,0))

    def _run_scan_chars(self, src, out, fields, no_norm, include_nl):
        if self._require(src):
            args = ["scan_chars.py", src.get()]
            if out.get().strip(): args.append(out.get())
            if fields.get().strip(): args += ["--fields", fields.get().strip()]
            if no_norm.get(): args.append("--no-normalize")
            if include_nl.get(): args.append("--include-newline")
            self.run_cmd(args, "扫描译文字符")

    def _run_hanzi_map(self, charset, fontdir, out, cnjp, banks, no_over):
        if self._require(charset, fontdir, out):
            args = ["hanzi_replacer.py", charset.get(), fontdir.get(), out.get(), "--banks", banks.get().strip() or "FONT00,FONT01,FONT02"]
            if cnjp.get().strip(): args += ["--cnjp-map", cnjp.get()]
            if no_over.get(): args.append("--no-external-overwrite")
            self.run_cmd(args, "生成 replace_map")

    def _run_font_gen(self, rep, ttf, orig, out, banks, size, mask, literal, no_fonthan):
        if self._require(rep, ttf, orig, out):
            args = ["font_gen.py", rep.get(), ttf.get(), orig.get(), out.get(), "--banks", banks.get().strip() or "FONT00,FONT01,FONT02", "--size", str(size.get()), "--mask-mode", mask.get()]
            if literal.get(): args.append("--literal-lzss")
            if no_fonthan.get(): args.append("--no-copy-fonthan")
            self.run_cmd(args, "构建 DATA_FONT")

    # ───────────────────────── G24 ─────────────────────────
    def _build_g24_tab(self):
        tab = self._tab("G24 图像")
        g24_info = tk.StringVar()
        sec = self._section(tab, "G24 信息", "读取原始 G24 的 x/y/宽高/stride/raw size。")
        PathRow(sec, "原始 G24", g24_info, filetypes=[("G24", "*.g24 *.G24"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="查看信息", command=lambda: self._run_g24_info(g24_info)).pack(anchor="e", pady=(8,0))

        g24_src = tk.StringVar(); png_out = tk.StringVar()
        sec = self._section(tab, "G24 -> PNG", "支持单个 .G24 和 G24 目录批量。单文件时输出填 .png；目录批量时输出填目录，保持相对目录结构。")
        AnyPathRow(sec, "原始 G24/目录", g24_src, filetypes=[("G24", "*.g24 *.G24"), ("All", "*.*")]).pack(fill="x", pady=3)
        AnyPathRow(sec, "输出 PNG/目录", png_out, save_file=True, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="转换为 PNG", command=lambda: self._run_g24_to_png(g24_src, png_out)).pack(anchor="e", pady=(8,0))

        png_src = tk.StringVar(); g24_out = tk.StringVar(); ref_g24 = tk.StringVar()
        x = tk.IntVar(value=0); y = tk.IntVar(value=0); comp = tk.StringVar(value="greedy")
        sec = self._section(tab, "PNG -> G24", "支持单 PNG 或 PNG 目录批量。参考原 G24 可以填单文件，也可以填原 G24 目录；目录模式会按同名/相对路径读取每张图的 x/y。")
        AnyPathRow(sec, "PNG/目录", png_src, filetypes=[("PNG", "*.png *.PNG"), ("All", "*.*")]).pack(fill="x", pady=3)
        AnyPathRow(sec, "输出 G24/目录", g24_out, save_file=True, filetypes=[("G24", "*.g24"), ("All", "*.*")]).pack(fill="x", pady=3)
        AnyPathRow(sec, "参考原 G24/目录", ref_g24, filetypes=[("G24", "*.g24 *.G24"), ("All", "*.*")], tooltip="可选。单文件沿用该文件 x/y；目录批量按同名 G24 逐个读取 x/y。" ).pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="x/y", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Spinbox(opt, from_=-32768, to=32767, textvariable=x, width=8).pack(side="left")
        ttk.Spinbox(opt, from_=-32768, to=32767, textvariable=y, width=8).pack(side="left", padx=(6, 0))
        ttk.Label(opt, text="压缩").pack(side="left", padx=(16, 3))
        ttk.Combobox(opt, values=["greedy", "literal"], textvariable=comp, width=10, state="readonly").pack(side="left")
        ttk.Button(sec, text="转换为 G24", command=lambda: self._run_png_to_g24(png_src, g24_out, ref_g24, x, y, comp)).pack(anchor="e", pady=(8,0))

        rt_src = tk.StringVar(); rt_out = tk.StringVar()
        sec = self._section(tab, "Roundtrip 测试", "执行 G24 -> PNG -> G24 -> PNG，并比较 raw/png 是否一致。")
        PathRow(sec, "原始 G24", rt_src, filetypes=[("G24", "*.g24 *.G24"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "输出目录", rt_out, mode="dir").pack(fill="x", pady=3)
        ttk.Button(sec, text="开始 Roundtrip", command=lambda: self._run_g24_roundtrip(rt_src, rt_out)).pack(anchor="e", pady=(8,0))

    def _run_g24_info(self, src):
        if self._require(src): self.run_cmd(["ai5win_g24_tools.py", "info", src.get()], "G24 信息")

    def _run_g24_to_png(self, src, out):
        if self._require(src, out): self.run_cmd(["ai5win_g24_tools.py", "g24topng", src.get(), out.get()], "G24 -> PNG")

    def _run_png_to_g24(self, src, out, ref, x, y, comp):
        if self._require(src, out):
            args = ["ai5win_g24_tools.py", "png2g24", src.get(), out.get(), "--x", str(x.get()), "--y", str(y.get()), "--compress", comp.get()]
            if ref.get().strip(): args += ["--ref-g24", ref.get()]
            self.run_cmd(args, "PNG -> G24")

    def _run_g24_roundtrip(self, src, out):
        if self._require(src, out): self.run_cmd(["ai5win_g24_tools.py", "roundtrip", src.get(), out.get()], "G24 Roundtrip")

    # ───────────────────────── MSK ─────────────────────────
    def _build_msk_tab(self):
        tab = self._tab("MSK 图层")
        msk_info = tk.StringVar(); ref = tk.StringVar(); force = tk.BooleanVar(value=False)
        sec = self._section(tab, "MSK 信息", "查看解压长度、Type A/Type B/TITLE_PT_M 判断和候选尺寸。")
        PathRow(sec, "MSK 图层", msk_info, filetypes=[("MSK", "*.msk *.MSK"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "参考 G24/PNG", ref, filetypes=[("Image", "*.g24 *.G24 *.png *.PNG"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Checkbutton(sec, text="强制按 TITLE_PT_M 特殊格式判断", variable=force).pack(anchor="w", pady=3)
        ttk.Button(sec, text="查看 MSK 信息", command=lambda: self._run_msk_info(msk_info, ref, force)).pack(anchor="e", pady=(8,0))

        msk_src = tk.StringVar(); msk_ref = tk.StringVar(); msk_out = tk.StringVar(); size = tk.StringVar()
        split = tk.BooleanVar(value=False); force_dec = tk.BooleanVar(value=False)
        sec = self._section(tab, "MSK -> PNG", "支持单 MSK 或 MSK 目录。普通 Type B 建议填写原始 G24/PNG 作为尺寸参考；TITLE_PT_M 可额外输出三个 208x580 切片。")
        AnyPathRow(sec, "MSK/目录", msk_src, filetypes=[("MSK", "*.msk *.MSK"), ("All", "*.*")]).pack(fill="x", pady=3)
        AnyPathRow(sec, "参考 G24/PNG/目录", msk_ref, filetypes=[("Image", "*.g24 *.G24 *.png *.PNG"), ("All", "*.*")], tooltip="单文件填原始 G24/PNG；目录模式填 G24/PNG 所在目录。" ).pack(fill="x", pady=3)
        AnyPathRow(sec, "输出 PNG/目录", msk_out, save_file=True, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="显式尺寸", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(opt, textvariable=size, width=16).pack(side="left")
        ttk.Label(opt, text="例如 624x580", style="Hint.TLabel").pack(side="left", padx=(6, 12))
        ttk.Checkbutton(opt, text="拆分 TITLE_PT_M", variable=split).pack(side="left")
        ttk.Checkbutton(opt, text="强制特殊格式", variable=force_dec).pack(side="left", padx=16)
        ttk.Button(sec, text="解码 MSK", command=lambda: self._run_msk_decode(msk_src, msk_ref, msk_out, size, split, force_dec)).pack(anchor="e", pady=(8,0))

        g24 = tk.StringVar(); msk = tk.StringVar(); rgba = tk.StringVar(); merge_size = tk.StringVar(); merge_force = tk.BooleanVar(value=False)
        sec = self._section(tab, "原始 G24 + MSK 图层合成 RGBA", "用于检查普通 alpha 图层是否和原始 G24 正常对齐。TITLE_PT_M 不是一对一普通 alpha，不能直接用此功能。")
        PathRow(sec, "原始 G24", g24, filetypes=[("G24", "*.g24 *.G24"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "MSK 图层", msk, filetypes=[("MSK", "*.msk *.MSK"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "输出 RGBA PNG", rgba, save=True, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="显式尺寸", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(opt, textvariable=merge_size, width=16).pack(side="left")
        ttk.Checkbutton(opt, text="强制特殊格式", variable=merge_force).pack(side="left", padx=16)
        ttk.Button(sec, text="合成 RGBA", command=lambda: self._run_msk_merge(g24, msk, rgba, merge_size, merge_force)).pack(anchor="e", pady=(8,0))

        enc_src = tk.StringVar(); enc_out = tk.StringVar()
        sec = self._section(tab, "PNG -> MSK / RGBA Alpha -> MSK", "这是 img_msk_tool.py 的反向工作流：灰度 PNG 直接编码为 MSK；RGBA PNG 提取 Alpha 通道生成 _M.MSK。支持单文件和目录批量。")
        AnyPathRow(sec, "PNG/目录", enc_src, filetypes=[("PNG", "*.png *.PNG"), ("All", "*.*")]).pack(fill="x", pady=3)
        AnyPathRow(sec, "输出 MSK/目录", enc_out, save_file=True, filetypes=[("MSK", "*.msk"), ("All", "*.*")]).pack(fill="x", pady=3)
        btns = ttk.Frame(sec); btns.pack(anchor="e", pady=(8,0))
        ttk.Button(btns, text="灰度 PNG 编码 MSK", command=lambda: self._run_msk_encode(enc_src, enc_out)).pack(side="left", padx=(0,8))
        ttk.Button(btns, text="提取 Alpha 为 MSK", command=lambda: self._run_msk_extract_alpha(enc_src, enc_out)).pack(side="left")

        p0 = tk.StringVar(); p1 = tk.StringVar(); p2 = tk.StringVar(); jout = tk.StringVar()
        sec = self._section(tab, "TITLE_PT_M 特殊拼接", "把三个 208x580 灰度切片重新拼成 TITLE_PT_M.MSK。")
        PathRow(sec, "p0.png", p0, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "p1.png", p1, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "p2.png", p2, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "输出 MSK", jout, save=True, filetypes=[("MSK", "*.msk"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="拼接特殊 MSK", command=lambda: self._run_msk_join(p0, p1, p2, jout)).pack(anchor="e", pady=(8,0))

    def _run_msk_info(self, src, ref, force):
        if self._require(src):
            args = ["img_msk_tool.py", "info", src.get()]
            if ref.get().strip(): args += ["--ref", ref.get()]
            if force.get(): args.append("--force-special")
            self.run_cmd(args, "MSK 信息")

    def _run_msk_decode(self, src, ref, out, size, split, force):
        if self._require(src):
            args = ["img_msk_tool.py", "decode", src.get()]
            if ref.get().strip(): args.append(ref.get())
            if out.get().strip(): args.append(out.get())
            if size.get().strip(): args += ["--size", size.get().strip()]
            if split.get(): args.append("--split-special")
            if force.get(): args.append("--force-special")
            self.run_cmd(args, "MSK -> PNG")

    def _run_msk_merge(self, g24, msk, out, size, force):
        if self._require(g24, msk):
            args = ["img_msk_tool.py", "merge", g24.get(), msk.get()]
            if out.get().strip(): args.append(out.get())
            if size.get().strip(): args += ["--size", size.get().strip()]
            if force.get(): args.append("--force-special")
            self.run_cmd(args, "G24 + MSK 合成")

    def _run_msk_encode(self, src, out):
        if self._require(src):
            args = ["img_msk_tool.py", "encode", src.get()]
            if out.get().strip(): args.append(out.get())
            self.run_cmd(args, "PNG -> MSK")

    def _run_msk_extract_alpha(self, src, out):
        if self._require(src):
            args = ["img_msk_tool.py", "extract_alpha", src.get()]
            if out.get().strip(): args.append(out.get())
            self.run_cmd(args, "RGBA Alpha -> MSK")

    def _run_msk_join(self, p0, p1, p2, out):
        if self._require(p0, p1, p2, out):
            self.run_cmd(["img_msk_tool.py", "join_special", p0.get(), p1.get(), p2.get(), out.get()], "TITLE_PT_M 拼接")

    # ───────────────────────── EXE ─────────────────────────
    def _build_exe_tab(self):
        tab = self._tab("EXE Patch")
        exe = tk.StringVar(); manifest = tk.StringVar(); out = tk.StringVar(); dry = tk.BooleanVar(value=False); force = tk.BooleanVar(value=False)
        sec = self._section(tab, "Patch font_size_array", "根据 DATA_FONT/build_manifest.json 更新 Ai5win.exe 中 FONT00/FONT01/FONT02/FONTHAN 的 raw size 常量。")
        PathRow(sec, "Ai5win.exe", exe, filetypes=[("EXE", "*.exe"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "manifest", manifest, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "输出 EXE", out, save=True, filetypes=[("EXE", "*.exe"), ("All", "*.*")]).pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Checkbutton(opt, text="dry-run 只检查不写入", variable=dry).pack(side="left", padx=(154, 16))
        ttk.Checkbutton(opt, text="force 强制写入", variable=force).pack(side="left")
        ttk.Button(sec, text="Patch EXE", command=lambda: self._run_patch_exe(exe, manifest, out, dry, force)).pack(anchor="e", pady=(8,0))

    def _run_patch_exe(self, exe, manifest, out, dry, force):
        if self._require(exe, manifest):
            args = ["patch_exe_font_banks.py", exe.get(), manifest.get()]
            if out.get().strip(): args.append(out.get())
            if dry.get(): args.append("--dry-run")
            if force.get(): args.append("--force")
            self.run_cmd(args, "Patch EXE font_size_array")

    # ───────────────────────── Misc ─────────────────────────
    def _build_misc_tab(self):
        tab = self._tab("辅助工具")
        sec = self._section(tab, "反汇编 MES", "调用 ai5win_disasm.py 输出反汇编文本。该脚本原本直接按命令行参数使用。")
        dis_src = tk.StringVar()
        PathRow(sec, "MES 文件", dis_src, filetypes=[("MES", "*.mes *.MES"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="运行反汇编", command=lambda: self._run_simple_script("ai5win_disasm.py", [dis_src], "MES 反汇编")).pack(anchor="e", pady=(8,0))

        sec = self._section(tab, "检查某句话字库映射", "调用 verify_font_line.py，输出每个字符的借码位和一张 glyph 预览图。")
        mp = tk.StringVar(value="build/replace_map.json"); fd = tk.StringVar(value="build/DATA_FONT"); bank = tk.StringVar(value="FONT00"); line = tk.StringVar(); opng = tk.StringVar(value="line_verify.png")
        PathRow(sec, "replace_map", mp, filetypes=[("JSON", "*.json"), ("All", "*.*")]).pack(fill="x", pady=3)
        PathRow(sec, "DATA_FONT", fd, mode="dir").pack(fill="x", pady=3)
        opt = ttk.Frame(sec); opt.pack(fill="x", pady=3)
        ttk.Label(opt, text="Bank", width=18, anchor="e").pack(side="left", padx=(0,6))
        ttk.Combobox(opt, values=["FONT00", "FONT01", "FONT02", "FONTHAN"], textvariable=bank, width=12).pack(side="left")
        ttk.Label(opt, text="文本").pack(side="left", padx=(16,3))
        ttk.Entry(opt, textvariable=line, width=60).pack(side="left", fill="x", expand=True)
        PathRow(sec, "输出预览 PNG", opng, save=True, filetypes=[("PNG", "*.png"), ("All", "*.*")]).pack(fill="x", pady=3)
        ttk.Button(sec, text="检查并导出预览", command=lambda: self._run_verify_line(mp, fd, bank, line, opng)).pack(anchor="e", pady=(8,0))

        sec = self._section(tab, "原始命令行", "用于临时调用没有单独做成表单的小工具。参数按空格分割，不处理复杂引号；复杂参数建议仍在终端执行。")
        script = tk.StringVar(); args = tk.StringVar()
        row = ttk.Frame(sec); row.pack(fill="x", pady=3)
        ttk.Label(row, text="脚本", width=18, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Combobox(row, textvariable=script, values=[
            "check_map_conflicts.py", "debug_encode.py", "inspect_font_mapping.py", "patch_exe_font00_only.py"
        ], width=32).pack(side="left")
        ttk.Label(row, text="参数").pack(side="left", padx=(16,3))
        ttk.Entry(row, textvariable=args).pack(side="left", fill="x", expand=True)
        ttk.Button(sec, text="运行", command=lambda: self._run_raw(script, args)).pack(anchor="e", pady=(8,0))

    def _run_simple_script(self, script_name: str, vars_: list[tk.StringVar], title: str):
        if self._require(*vars_):
            self.run_cmd([script_name] + [v.get() for v in vars_], title)

    def _run_verify_line(self, mp, fd, bank, line, opng):
        if self._require(mp, fd, line):
            args = ["verify_font_line.py", mp.get(), fd.get(), bank.get(), line.get()]
            if opng.get().strip(): args.append(opng.get())
            self.run_cmd(args, "字库行检查")

    def _run_raw(self, script, args):
        if not script.get().strip():
            messagebox.showerror("缺少脚本", "请选择要运行的脚本。")
            return
        argv = [script.get().strip()]
        if args.get().strip():
            argv.extend(args.get().strip().split())
        self.run_cmd(argv, "原始命令行")


if __name__ == "__main__":
    App().mainloop()
