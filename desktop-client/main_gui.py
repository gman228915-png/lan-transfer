#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网互传 - 桌面 GUI

v1.7 起：
- 四张卡片可折叠（状态写入 settings.json）
- 卡片填充色按背景图在卡片位置的平均色混合（模拟透出背景，Tk 控件没有真 alpha）
- 界面文案来自 i18n/<lang>.json（简体中文 / 繁體中文 / English / 日本語 / 한국어 / Español / Русский）
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser
import socket

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 导入后端与多语言
import lan_transfer
import tr

# --------------------------------------------------------------------------- #
# 主题
# --------------------------------------------------------------------------- #
BG_BASE = "#0F1425"          # 窗口底色 / 画布兜底色
BG_HEADER = "#141B2F"        # 顶栏
PANEL = "#161E36"            # 卡片（深色半透明观感）
PANEL_ALT = "#1E2745"        # 斑马纹
CARD_LINE = "#2A3354"
TEXT = "#EAF0FF"
TEXT_SOFT = "#C8D0E8"
MUTED = "#8B97B5"
ACCENT = "#5B8CFF"
ACCENT_DEEP = "#4A6DF0"
ACCENT2 = "#31D6B0"
DANGER = "#FF6B6B"

# 用于混合卡片的基准色
BG_BASE_RGB = (15, 20, 37)
PANEL_RGB = (22, 30, 54)
PANEL_ALT_RGB = (30, 39, 69)
CARD_LINE_RGB = (42, 51, 84)

BG_MODES = ("fill", "fit", "tile", "center")
BG_DEFAULT_DIM = 45
# 不透明度：100% 为实色卡片，40% 时背景采样色权重更大，效果肉眼可见
CARD_OPACITY_MIN = 40
CARD_OPACITY_MAX = 100
CARD_OPACITY_DEFAULT = 75
CARD_OPACITY_LEGACY_DEFAULT = 88      # v1.7.0 的旧默认值，用于迁移
GLASS_LIFT = 58                       # 模拟玻璃感的提亮量，让低不透明度明显可辨
GLASS_FLOOR = 46

CARD_KEYS = ("info", "qr", "pub", "recv")
CARD_TITLE_KEYS = {
    "info": "card_info",
    "qr": "card_qr",
    "pub": "card_pub",
    "recv": "card_recv",
}
COLLAPSED_H = 42
CARD_HEADER_H = 40


def blend_rgb(a, b, ratio):
    """按比例把颜色 a 混向 b（ratio 越大越接近 b）。"""
    return tuple(int(a[i] * (1 - ratio) + b[i] * ratio) for i in range(3))


def darken_clamp(rgb, limit=92):
    """限制最大通道值，避免浅色背景把卡片冲成亮灰导致白字看不清。"""
    top = max(rgb) if rgb else 0
    if top <= limit:
        return tuple(int(v) for v in rgb)
    k = limit / float(top)
    return tuple(int(v * k) for v in rgb)


def lighten(rgb, delta=16):
    return tuple(min(255, int(v) + delta) for v in rgb)


def glass_target(rgb):
    """把背景采样色抬到「玻璃后面」的亮度，低不透明度时差异才看得出来。

    只做一次线性抬升，最终混色权重仍由滑块线性控制。
    """
    return tuple(min(255, max(GLASS_FLOOR, int(v)) + GLASS_LIFT) for v in rgb)


def gen_qr_image(url, box_size=5, border=3):
    """生成二维码 PIL Image 对象。"""
    try:
        import qrcode
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=box_size, border=border)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")
    except ImportError:
        return None


class LanTransferApp:
    def __init__(self, root):
        self.root = root

        # 语言要在创建任何控件之前确定，否则标题栏文字不会切换
        settings = lan_transfer.load_settings()
        ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
        tr.set_language(ui.get("language") or tr.detect_system_language())

        self.root.title("%s v%s" % (tr.t("app_title"), lan_transfer.APP_VERSION))
        self._place_window(ui)
        self.root.configure(bg=BG_BASE)

        self.server = None
        self._running = False
        self._busy = False
        self._share_url = ""
        self._qr_photo = None      # 保持引用防止被 GC
        self._qr_url = ""
        self._qr_modules = 0       # 二维码模块边长（用于按可用高度选 box_size）
        self._qr_box = 0
        self._icon_photo = None
        self._logo_photo = None

        # 背景：路径 / 显示方式 / 遮罩浓度
        self._bg_path, self._bg_mode, self._bg_dim = self._load_bg_config()
        self._bg_src = None        # 缓存的 PIL 原图（只在文件变化时重新读盘）
        self._bg_src_key = None
        self._bg_photo = None
        self._bg_composed = None   # 合成后的背景（用于采样卡片平均色）
        self._bg_drawn = None      # 上次绘制的 (尺寸, 配置)
        self._bg_job = None
        self._layout_job = None
        self._bg_dialog = None
        self._dirty = True

        # 界面状态：折叠 / 卡片不透明度
        self._collapsed = {key: False for key in CARD_KEYS}
        collapsed_saved = ui.get("collapsed")
        if isinstance(collapsed_saved, dict):
            for key in CARD_KEYS:
                self._collapsed[key] = bool(collapsed_saved.get(key, False))
        try:
            opacity = int(ui.get("card_opacity", CARD_OPACITY_DEFAULT))
        except (TypeError, ValueError):
            opacity = CARD_OPACITY_DEFAULT
        # v1.7.0 旧默认 88 且用户没手动改过：迁到新默认 75
        if opacity == CARD_OPACITY_LEGACY_DEFAULT and not ui.get("card_opacity_set"):
            opacity = CARD_OPACITY_DEFAULT
        self._card_opacity = max(CARD_OPACITY_MIN, min(CARD_OPACITY_MAX, opacity))
        self._geom_job = None
        self._geom_ready = False

        self._cards = {}
        self._card_rects = {}
        self._tagline = None
        self._tagline_shown = True

        # 读取上次保存的「局域网可见」设置（默认开启）
        try:
            lan_transfer.DISCOVERY_ENABLED = bool(settings.get("discovery", True))
        except Exception:
            lan_transfer.DISCOVERY_ENABLED = True

        self._apply_icons()
        self._build_ui()
        self._try_start()

    def _place_window(self, ui=None):
        """恢复用户上次手动调整的窗口大小；没有记录时按屏幕自适应。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        width = min(1120, max(940, sw - 96))
        height = min(820, max(620, sh - 80))
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - 20)

        saved = (ui or {}).get("window")
        if isinstance(saved, dict):
            try:
                w = int(saved.get("w", 0))
                h = int(saved.get("h", 0))
                if w >= 940 and h >= 600:
                    width, height = min(w, sw), min(h, sh)
                sx, sy = int(saved.get("x", x)), int(saved.get("y", y))
                x = max(0, min(sx, sw - width))
                y = max(0, min(sy, sh - height))
            except (TypeError, ValueError):
                pass
        self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))
        # 最小尺寸下也要保证四张卡（含全部展开）不被裁切；折叠不再改变窗口大小
        self.root.minsize(940, 660)
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.after(900, self._mark_geometry_ready)

    def _mark_geometry_ready(self):
        self._geom_ready = True

    def _on_root_configure(self, event):
        """用户拖动窗口边角时记录尺寸，折叠状态变化不会写这里。"""
        if event.widget is not self.root or not self._geom_ready:
            return
        if self._geom_job:
            try:
                self.root.after_cancel(self._geom_job)
            except Exception:
                pass
        self._geom_job = self.root.after(500, self._save_window_geometry)

    def _save_window_geometry(self):
        self._geom_job = None
        try:
            if self.root.state() != "normal":
                return
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w < 600 or h < 400:
                return
            cfg = lan_transfer.load_settings()
            ui = cfg.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            ui["window"] = {"w": int(w), "h": int(h),
                            "x": int(self.root.winfo_x()), "y": int(self.root.winfo_y())}
            cfg["ui"] = ui
            lan_transfer.save_settings(cfg)
        except Exception:
            pass

    # ------------------------------------------------------------------ 图标
    def _apply_icons(self):
        """标题栏 / 任务栏 / 窗口图标统一使用新品牌图标。"""
        ico = os.path.join(lan_transfer.get_resource_dir(), "icon.ico")
        if not os.path.isfile(ico):
            ico = os.path.join(lan_transfer.BASE_DIR, "icon.ico")

        png = os.path.join(lan_transfer.STATIC_DIR, "brand-256.png")
        try:
            self._icon_photo = tk.PhotoImage(file=png)
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            self._icon_photo = None

        if os.path.isfile(ico) and sys.platform.startswith("win"):
            try:
                # 不带 default 才是设置「当前窗口」的图标；带 default 只影响之后新建的窗口
                self.root.iconbitmap(ico)
                self.root.iconbitmap(default=ico)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # 画布承载整窗：背景图铺在底层，卡片浮在上面，四周与卡片间隙可见背景
        self.canvas = tk.Canvas(self.root, bg=BG_BASE, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._bg_item = self.canvas.create_image(0, 0, anchor="nw")

        # ---------------- 顶栏 ----------------
        header = tk.Frame(self.canvas, bg=BG_HEADER, height=58)
        self._header_win = self.canvas.create_window(0, 0, window=header, anchor="nw")

        logo_path = os.path.join(lan_transfer.STATIC_DIR, "brand-48.png")
        try:
            self._logo_photo = tk.PhotoImage(file=logo_path)
            tk.Label(header, image=self._logo_photo, bg=BG_HEADER).pack(
                side="left", padx=(16, 10), pady=5)
        except Exception:
            tk.Label(header, text="⇄", font=("Segoe UI", 22, "bold"),
                     fg=ACCENT, bg=BG_HEADER).pack(side="left", padx=(18, 8), pady=10)

        tk.Label(header, text=tr.t("app_name"), font=("Segoe UI", 16, "bold"),
                 fg=TEXT, bg=BG_HEADER).pack(side="left", pady=14)
        self._tagline = tk.Label(header, text=tr.t("tagline"),
                                 font=("Segoe UI", 9), fg=MUTED, bg=BG_HEADER)
        self._tagline.pack(side="left", padx=(12, 0), pady=14)

        head_right = tk.Frame(header, bg=BG_HEADER)
        head_right.pack(side="right", padx=14, pady=11)

        self.btn_appearance = self._make_btn(head_right, tr.t("btn_appearance"),
                                             self._open_bg_dialog, small=True)
        self.btn_appearance.pack(side="right", padx=(10, 0))
        self.btn_spaces = self._make_btn(head_right, tr.t("btn_spaces"),
                                         self._open_spaces_dialog, small=True)
        self.btn_spaces.pack(side="right", padx=(10, 0))
        self.btn_expand_all = self._make_btn(head_right, tr.t("btn_expand_all"),
                                             self._expand_all, small=True)
        self.btn_expand_all.pack(side="right", padx=(10, 0))
        self.btn_collapse_all = self._make_btn(head_right, tr.t("btn_collapse_all"),
                                               self._collapse_all, small=True)
        self.btn_collapse_all.pack(side="right")
        self._version_label = tk.Label(head_right, text="v" + lan_transfer.APP_VERSION,
                                       font=("Segoe UI", 9), fg=MUTED, bg=BG_HEADER)
        self._version_label.pack(side="right", padx=(0, 4))
        self.status_indicator = tk.Label(head_right, text=tr.t("status_stopped"),
                                         font=("Segoe UI", 11), fg=DANGER, bg=BG_HEADER)
        self.status_indicator.pack(side="right", padx=(0, 10))

        # 顶栏可见的卡片不透明度控件（放在左侧品牌区之后，窄窗口也优先保留）
        self.op_box = tk.Frame(header, bg=BG_HEADER)
        self.op_box.pack(side="left", padx=(20, 0), pady=10)
        tk.Label(self.op_box, text=tr.t("opacity_label"), bg=BG_HEADER, fg=TEXT_SOFT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self.op_scale = tk.Scale(
            self.op_box, from_=CARD_OPACITY_MIN, to=CARD_OPACITY_MAX,
            orient="horizontal", showvalue=False, command=self._on_opacity_move,
            bg=ACCENT, fg=TEXT, troughcolor="#0B1020", activebackground=ACCENT2,
            highlightthickness=0, bd=0, sliderrelief="flat", length=96, width=9)
        self.op_scale.pack(side="left")
        self.top_opacity_label = tk.Label(self.op_box, text="%d%%" % self._card_opacity,
                                          bg=BG_HEADER, fg=ACCENT2,
                                          font=("Segoe UI", 9), width=4, anchor="e")
        self.top_opacity_label.pack(side="left", padx=(2, 0))
        self.op_scale.set(int(self._card_opacity))

        # ---------------- 四张卡片（直接浮在画布上，靠 _layout 定位） ----------------
        self.info_card, info_body = self._create_card("info")
        self.qr_card, qr_body = self._create_card("qr")
        self.pub_card, pub_body = self._create_card("pub")
        self.recv_card, recv_body = self._create_card("recv")
        self._wins = {
            "info": self.canvas.create_window(0, 0, window=self.info_card, anchor="nw"),
            "qr": self.canvas.create_window(0, 0, window=self.qr_card, anchor="nw"),
            "pub": self.canvas.create_window(0, 0, window=self.pub_card, anchor="nw"),
            "recv": self.canvas.create_window(0, 0, window=self.recv_card, anchor="nw"),
        }

        # ---------------- 左：服务信息 ----------------
        self.info_text = tk.Text(info_body, height=6, bg=PANEL_ALT, fg=TEXT_SOFT,
                                 font=("Consolas", 9), relief="flat", wrap="word",
                                 borderwidth=0, padx=10, pady=7, highlightthickness=0,
                                 insertbackground=TEXT)
        self.info_text.pack(fill="x", padx=12, pady=(4, 8))
        self.info_text.insert("1.0", tr.t("info_waiting"))
        self.info_text.config(state="disabled")

        info_btns = tk.Frame(info_body, bg=PANEL)
        info_btns.pack(fill="x", padx=12, pady=(0, 8))

        self.btn_toggle = self._make_btn(info_btns, tr.t("btn_start"), self._toggle_server,
                                         primary=True)
        self.btn_toggle.pack(side="left", padx=(0, 6))
        self.btn_open_browser = self._make_btn(info_btns, tr.t("btn_open_web"),
                                               self._open_browser)
        self.btn_open_browser.pack(side="left")

        info_btns2 = tk.Frame(info_body, bg=PANEL)
        info_btns2.pack(fill="x", padx=12, pady=(0, 8))
        self.btn_copy_url = self._make_btn(info_btns2, tr.t("btn_copy_link"), self._copy_url)
        self.btn_copy_url.pack(side="left", padx=(0, 6))
        self.btn_open_pub_top = self._make_btn(info_btns2, tr.t("btn_open_folder"),
                                              self._open_publish_folder)
        self.btn_open_pub_top.pack(side="left")

        disc_row = tk.Frame(info_body, bg=PANEL)
        disc_row.pack(fill="x", padx=12, pady=(0, 10))
        self.discovery_var = tk.BooleanVar(value=lan_transfer.DISCOVERY_ENABLED)
        self.chk_discovery = tk.Checkbutton(
            disc_row, text=tr.t("chk_discovery"), variable=self.discovery_var,
            command=self._toggle_discovery, bg=PANEL, fg=TEXT_SOFT,
            selectcolor=PANEL_ALT, activebackground=PANEL,
            activeforeground="#ffffff", font=("Segoe UI", 9),
            borderwidth=0, highlightthickness=0, anchor="w")
        self.chk_discovery.pack(anchor="w", fill="x")
        # 状态单独一行：英/俄/西等长文案不会把右侧状态挤掉
        self.lbl_discovery = tk.Label(disc_row, text="", bg=PANEL,
                                      fg=MUTED, font=("Segoe UI", 8), anchor="e")
        self.lbl_discovery.pack(fill="x", anchor="e")

        # ---------------- 左：二维码 ----------------
        self.qr_container = tk.Frame(qr_body, bg=PANEL)
        self.qr_container.pack(fill="both", expand=True, padx=12, pady=(2, 2))

        self.qr_label = tk.Label(self.qr_container, text=tr.t("qr_placeholder"),
                                 bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        self.qr_label.pack(expand=True)

        self.url_label = tk.Label(qr_body, text="", bg=PANEL, fg=ACCENT2,
                                  font=("Consolas", 8), wraplength=340,
                                  justify="center")
        self.url_label.pack(fill="x", padx=12, pady=(0, 8))

        # ---------------- 右：发布文件 ----------------
        self.pub_space_var = tk.StringVar()
        pub_space_row = tk.Frame(pub_body, bg=PANEL)
        pub_space_row.pack(fill="x", padx=12, pady=(2, 2))
        tk.Label(pub_space_row, text=tr.t("space_label"), bg=PANEL, fg=TEXT_SOFT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.pub_space_combo = ttk.Combobox(pub_space_row, textvariable=self.pub_space_var,
                                            state="readonly", width=16,
                                            font=("Segoe UI", 9))
        self.pub_space_combo.pack(side="left", padx=(6, 0))
        self.pub_space_combo.bind("<<ComboboxSelected>>",
                                  lambda _e: self._on_space_change("pub"))
        self.pub_space_hint = tk.Label(pub_space_row, text="", bg=PANEL, fg=MUTED,
                                       font=("Segoe UI", 8), anchor="e")
        self.pub_space_hint.pack(side="right")

        tk.Label(pub_body, text=tr.t("pub_hint"),
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(2, 4))

        pub_wrap = tk.Frame(pub_body, bg=PANEL)
        pub_wrap.pack(fill="x", padx=12, pady=(0, 8))

        columns = ("name", "size")
        self.pub_tree = ttk.Treeview(pub_wrap, columns=columns, show="headings", height=6,
                                     style="Pub.Treeview")
        self._setup_tree(self.pub_tree, columns, "Pub")
        pub_scroll = ttk.Scrollbar(pub_wrap, orient="vertical",
                                   style="Pub.Vertical.TScrollbar",
                                   command=self.pub_tree.yview)
        self.pub_tree.configure(yscroll=pub_scroll.set)
        self._pub_scroll = pub_scroll
        self.pub_tree.pack(side="left", fill="x", expand=True)
        pub_scroll.pack(side="right", fill="y")

        pub_btns = tk.Frame(pub_body, bg=PANEL)
        pub_btns.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_add_pub = self._make_btn(pub_btns, tr.t("btn_add_files"),
                                          self._add_publish_files, primary=True)
        self.btn_add_pub.pack(side="left", padx=(0, 6))
        self.btn_add_pub_dir = self._make_btn(pub_btns, tr.t("btn_add_folder"),
                                              self._add_publish_folder)
        self.btn_add_pub_dir.pack(side="left", padx=(0, 6))
        self.btn_delete_pub = self._make_btn(pub_btns, tr.t("btn_delete_selected"),
                                             self._delete_publish_files)
        self.btn_delete_pub.pack(side="left", padx=(0, 6))
        self.btn_open_pub = self._make_btn(pub_btns, tr.t("btn_open_folder"),
                                           self._open_publish_folder)
        self.btn_open_pub.pack(side="right")

        # ---------------- 右：接收文件 ----------------
        self.recv_space_var = tk.StringVar()
        recv_space_row = tk.Frame(recv_body, bg=PANEL)
        recv_space_row.pack(fill="x", padx=12, pady=(2, 2))
        tk.Label(recv_space_row, text=tr.t("space_label"), bg=PANEL, fg=TEXT_SOFT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.recv_space_combo = ttk.Combobox(recv_space_row, textvariable=self.recv_space_var,
                                             state="readonly", width=16,
                                             font=("Segoe UI", 9))
        self.recv_space_combo.pack(side="left", padx=(6, 0))
        self.recv_space_combo.bind("<<ComboboxSelected>>",
                                   lambda _e: self._on_space_change("recv"))
        self.recv_space_hint = tk.Label(recv_space_row, text="", bg=PANEL, fg=MUTED,
                                        font=("Segoe UI", 8), anchor="e")
        self.recv_space_hint.pack(side="right")

        recv_wrap = tk.Frame(recv_body, bg=PANEL)
        recv_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        self.recv_tree = ttk.Treeview(recv_wrap, columns=columns, show="headings", height=6,
                                      style="Recv.Treeview")
        self._setup_tree(self.recv_tree, columns, "Recv")
        recv_scroll = ttk.Scrollbar(recv_wrap, orient="vertical",
                                    style="Recv.Vertical.TScrollbar",
                                    command=self.recv_tree.yview)
        self.recv_tree.configure(yscroll=recv_scroll.set)
        self._recv_scroll = recv_scroll
        self.recv_tree.pack(side="left", fill="both", expand=True)
        recv_scroll.pack(side="right", fill="y")

        recv_btns = tk.Frame(recv_body, bg=PANEL)
        recv_btns.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_refresh_recv = self._make_btn(recv_btns, tr.t("btn_refresh"),
                                               self._refresh_all)
        self.btn_refresh_recv.pack(side="left", padx=(0, 6))
        self.btn_open_recv = self._make_btn(recv_btns, tr.t("btn_open_recv_folder"),
                                            self._open_receive_folder)
        self.btn_open_recv.pack(side="left")

        self.lbl_recv_hint = tk.Label(recv_btns, text=tr.t("recv_hint"),
                                       bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                                       wraplength=180, justify="right", anchor="e")
        self.lbl_recv_hint.pack(side="right", padx=(8, 0))

        # 折叠状态：恢复上次保存的收起项
        for key in CARD_KEYS:
            self._apply_collapsed(key)
            self._refresh_card_chrome(key)

        self._sync_space_controls()

        # 布局 & 背景
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.after(80, self._layout)
        self.root.after(80, self._redraw_bg)

        # 定时刷新
        self.root.after(2000, self._auto_refresh)

    def _create_card(self, key):
        """卡片容器：统一标题行（标题 + 关键摘要 + 折叠按钮），正文放在 body 里。"""
        card = tk.Frame(self.canvas, bg=PANEL, highlightbackground=CARD_LINE,
                        highlightthickness=1)
        header = tk.Frame(card, bg=PANEL, height=CARD_HEADER_H)
        header.pack(fill="x", padx=10)
        header.pack_propagate(False)

        title = tk.Label(header, text=tr.t(CARD_TITLE_KEYS[key]), bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 11, "bold"), anchor="w")
        title.pack(side="left", pady=7)

        btn = tk.Button(header, text="⌃",
                        command=lambda k=key: self._toggle_card(k),
                        bg=PANEL, fg=MUTED, activebackground=PANEL_ALT,
                        activeforeground=TEXT, relief="flat", bd=0,
                        font=("Segoe UI", 11), padx=3, pady=0, width=2,
                        cursor="hand2", highlightthickness=0)
        btn.pack(side="right", pady=6)

        summary = tk.Label(header, text="", bg=PANEL, fg=MUTED,
                           font=("Segoe UI", 9), anchor="e")
        summary.pack(side="right", padx=(8, 6), pady=9)

        body = tk.Frame(card, bg=PANEL)
        body.pack(fill="both", expand=True)

        self._cards[key] = {"card": card, "header": header, "title": title,
                            "btn": btn, "summary": summary, "body": body}
        return card, body

    # ------------------------------------------------------------- 折叠控制
    def _card_title_text(self, key):
        return tr.t(CARD_TITLE_KEYS[key])

    def _card_summary_text(self, key):
        """收起后在标题行右侧显示本卡关键状态。"""
        if key == "info":
            if not self._running:
                return tr.t("sum_info_off")
            return tr.t("sum_info_run", port=lan_transfer.PORT)
        if key == "qr":
            if not self._running:
                return tr.t("sum_qr_off")
            addr = self._share_url.split("://", 1)[-1].split("/", 1)[0]
            return tr.t("sum_qr_run", addr=addr)
        if key == "pub":
            return tr.t("sum_pub", count=len(self.pub_tree.get_children()))
        return tr.t("sum_recv", count=len(self.recv_tree.get_children()))

    def _refresh_card_chrome(self, key):
        c = self._cards[key]
        collapsed = self._collapsed[key]
        try:
            c["title"].configure(text=self._card_title_text(key))
            # ⌃ 表示可收起，⌄ 表示可展开；四张卡位置、字号、颜色完全一致
            c["btn"].configure(text="⌄" if collapsed else "⌃",
                               fg=ACCENT2 if collapsed else MUTED)
            if collapsed:
                c["summary"].configure(text=self._card_summary_text(key))
                if not c["summary"].winfo_manager():
                    c["summary"].pack(side="right", padx=(8, 6), pady=9)
            elif c["summary"].winfo_manager():
                c["summary"].pack_forget()
        except tk.TclError:
            pass

    def _update_card_summaries(self):
        for key in CARD_KEYS:
            if self._collapsed[key]:
                try:
                    self._cards[key]["summary"].configure(
                        text=self._card_summary_text(key))
                except tk.TclError:
                    pass
        self._sync_tree_scroll("pub")
        self._sync_tree_scroll("recv")

    def _sync_tree_scroll(self, key):
        """内容放得下时隐藏列表滚动条，避免出现多余的滚动条。"""
        if self._collapsed.get(key):
            return
        tree = self.pub_tree if key == "pub" else self.recv_tree
        scroll = self._pub_scroll if key == "pub" else self._recv_scroll
        try:
            if len(tree.get_children()) <= int(tree.cget("height")):
                if scroll.winfo_manager():
                    scroll.pack_forget()
                return
        except (tk.TclError, ValueError):
            return
        if not scroll.winfo_manager():
            scroll.pack(side="right", fill="y")

    def _apply_collapsed(self, key):
        """按折叠状态显示 / 隐藏卡片正文。"""
        c = self._cards[key]
        body = c["body"]
        if self._collapsed[key]:
            body.pack_forget()
        elif not body.winfo_manager():
            body.pack(fill="both", expand=True)

    def _toggle_card(self, key):
        self._collapsed[key] = not self._collapsed[key]
        self._apply_collapsed(key)
        self._refresh_card_chrome(key)
        self._save_ui_config()
        self._layout()

    def _collapse_all(self):
        for key in CARD_KEYS:
            if not self._collapsed[key]:
                self._collapsed[key] = True
                self._apply_collapsed(key)
                self._refresh_card_chrome(key)
        self._save_ui_config()
        self._layout()

    def _expand_all(self):
        for key in CARD_KEYS:
            if self._collapsed[key]:
                self._collapsed[key] = False
                self._apply_collapsed(key)
                self._refresh_card_chrome(key)
        self._save_ui_config()
        self._layout()

    # 折叠不再调整窗口尺寸：释放出来的区域留给背景

    def _distribute_height(self, avail, gap, a_key, b_key, req, mins):
        """在可用高度内分配两列卡片高度；放得下就用自然高度，下面留背景。"""
        a_collapsed = self._collapsed[a_key]
        b_collapsed = self._collapsed[b_key]
        if a_collapsed and b_collapsed:
            return COLLAPSED_H, COLLAPSED_H
        if a_collapsed:
            hb = max(mins[b_key], min(req[b_key], avail - gap - COLLAPSED_H))
            return COLLAPSED_H, hb
        if b_collapsed:
            ha = max(mins[a_key], min(req[a_key], avail - gap - COLLAPSED_H))
            return ha, COLLAPSED_H

        ha = max(mins[a_key], req[a_key])
        hb = max(mins[b_key], req[b_key])
        total = ha + hb + gap
        if total <= avail + 8:
            return ha, hb

        room_a = ha - mins[a_key]
        room_b = hb - mins[b_key]
        budget = avail - gap - mins[a_key] - mins[b_key]
        if budget <= 0 or room_a + room_b <= 0:
            return mins[a_key], mins[b_key]
        scale = budget / float(room_a + room_b)
        return int(mins[a_key] + room_a * scale), int(mins[b_key] + room_b * scale)

    def _content_rows(self, key):
        """返回当前内容行数，用于判断某次拟合是否真的改变了请求高度。"""
        try:
            if key == "info":
                return int(self.info_text.cget("height"))
            tree = self.pub_tree if key == "pub" else self.recv_tree
            return int(tree.cget("height"))
        except (tk.TclError, ValueError, AttributeError):
            return 0

    def _fit_info_rows(self, card_h):
        """把服务信息文本框的行数限制在卡片可用高度内，避免被裁切。"""
        if not hasattr(self, "info_text"):
            return
        lines = getattr(self, "_info_lines", 6)
        # 窄窗口下长路径会换行：按当前宽度统计真实显示行数，避免内容被藏起来
        try:
            shown = self.info_text.count("1.0", "end", "displaylines")
            if isinstance(shown, tuple):
                shown = shown[0]
            lines = max(lines, int(shown))
        except (tk.TclError, TypeError, ValueError):
            pass
        rows = int((max(120, card_h) - 200) // 14)
        rows = max(4, min(max(lines + 1, 6), rows))
        try:
            if int(self.info_text.cget("height")) != rows:
                self.info_text.config(height=rows)
        except (tk.TclError, ValueError):
            pass

    def _want_tree_rows(self, key):
        """按实际文件数计算列表希望显示的行数（空列表只占 2 行）。"""
        tree = self.pub_tree if key == "pub" else self.recv_tree
        try:
            count = len(tree.get_children())
        except (tk.TclError, AttributeError):
            count = 0
        return max(2, min(8, count + 1))

    def _fit_tree_rows(self, key, card_h):
        """按卡片高度调整列表可见行数，避免内容溢出卡片。

        只在分配高度确实放不下时收缩，所以文件不多时整列表可见、不出滚动条；
        空列表也只占 2 行，把空间让给另一列。
        """
        tree = self.pub_tree if key == "pub" else self.recv_tree
        overhead = 176 if key == "pub" else 155
        rows = int((max(150, card_h) - overhead) // 26)
        rows = max(2, min(8, rows, self._want_tree_rows(key)))
        try:
            if int(tree.cget("height")) != rows:
                tree.configure(height=rows)
        except (tk.TclError, ValueError):
            pass

    # ------------------------------------------------------------- 布局/背景
    def _on_canvas_configure(self, _event=None):
        if self._layout_job:
            try:
                self.root.after_cancel(self._layout_job)
            except Exception:
                pass
        self._layout_job = self.root.after(60, self._layout)

    def _layout(self):
        self._layout_job = None
        try:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
        except tk.TclError:
            return
        if width < 600 or height < 420:
            return

        margin, gap, header_h, top = 20, 14, 58, 12
        left_w = max(340, min(400, int(width * 0.33)))
        right_w = width - margin * 2 - gap - left_w
        if right_w < 430:
            right_w = 430
            left_w = max(300, width - margin * 2 - gap - right_w)

        content_top = top + header_h + gap
        avail = height - content_top - 14

        # 长语言（英/俄/西）文案更长，窄窗口下先隐藏副标题，给按钮和滑块留空间
        if self._tagline is not None:
            if width < 1240 and self._tagline_shown:
                self._tagline.pack_forget()
                self._tagline_shown = False
            elif width >= 1240 and not self._tagline_shown:
                self._tagline.pack(side="left", padx=(12, 0), pady=14)
                self._tagline_shown = True

        # 窄窗口 + 长语言：隐藏版本号，外观入口收成图标，保证折叠按钮和不透明度滑块不被挤掉
        compact = width < 1040
        if compact:
            if self._version_label.winfo_manager():
                self._version_label.pack_forget()
            if self.btn_appearance.cget("text") != "⚙":
                self.btn_appearance.config(text="⚙")
        else:
            if not self._version_label.winfo_manager():
                self._version_label.pack(side="right", padx=(0, 4))
            if self.btn_appearance.cget("text") != tr.t("btn_appearance"):
                self.btn_appearance.config(text=tr.t("btn_appearance"))

        # 先确定宽度，再量高度（卡片里的换行文字依赖宽度）
        self.canvas.itemconfigure(self._header_win, width=width - margin * 2,
                                  height=header_h)
        self.canvas.coords(self._header_win, margin, top)

        self.canvas.itemconfigure(self._wins["info"], width=left_w)
        self.canvas.itemconfigure(self._wins["qr"], width=left_w)
        self.canvas.itemconfigure(self._wins["pub"], width=right_w)
        self.canvas.itemconfigure(self._wins["recv"], width=right_w)
        self.root.update_idletasks()

        # 自然高度堆叠：放得下就按内容，放不下才按比例收缩；底部余量留背景。
        # 二维码模块大小与列表行数会反向影响卡片请求高度，迭代两三次让它稳定。
        last = None
        info_h = qr_h = pub_h = recv_h = 0
        for _ in range(5):
            # 先按文件数给出列表的“希望行数”，再量卡片请求高度：
            # 否则空列表会把请求高度压到最小，之后文件变多也长不回来
            for key in ("pub", "recv"):
                if self._collapsed[key]:
                    continue
                want = self._want_tree_rows(key)
                tree = self.pub_tree if key == "pub" else self.recv_tree
                try:
                    if int(tree.cget("height")) != want:
                        tree.configure(height=want)
                except (tk.TclError, ValueError):
                    pass
            self.root.update_idletasks()
            req = {key: self._cards[key]["card"].winfo_reqheight()
                   for key in CARD_KEYS}
            info_min = min(req["info"], max(260, avail - gap - 145))
            info_h, qr_h = self._distribute_height(
                avail, gap, "info", "qr", req, {"info": info_min, "qr": 145})
            pub_h, recv_h = self._distribute_height(
                avail, gap, "pub", "recv", req, {"pub": 200, "recv": 150})

            changed = False
            # 列内容按分配高度收缩，保证最小窗口下不裁切、不出滚动条
            for key, height in (("info", info_h), ("pub", pub_h), ("recv", recv_h)):
                if self._collapsed[key]:
                    continue
                before = self._content_rows(key)
                if key == "info":
                    self._fit_info_rows(height)
                else:
                    self._fit_tree_rows(key, height)
                changed = changed or self._content_rows(key) != before

            # 窗口变矮时按可用高度缩小二维码模块，保证二维码与下方链接都不被裁掉
            if (self._qr_url and self._qr_modules and not self._collapsed["qr"]):
                box = self._qr_box_for_height(qr_h)
                if box != self._qr_box:
                    self._render_qr(box)
                    changed = True

            now = (info_h, qr_h, pub_h, recv_h)
            if now == last and not changed:
                break
            last = now

        self.canvas.itemconfigure(self._wins["info"], height=info_h)
        self.canvas.itemconfigure(self._wins["qr"], height=qr_h)
        self.canvas.itemconfigure(self._wins["pub"], height=pub_h)
        self.canvas.itemconfigure(self._wins["recv"], height=recv_h)

        self.canvas.coords(self._wins["info"], margin, content_top)
        self.canvas.coords(self._wins["qr"], margin, content_top + info_h + gap)
        self.canvas.coords(self._wins["pub"], margin + left_w + gap, content_top)
        self.canvas.coords(self._wins["recv"],
                           margin + left_w + gap, content_top + pub_h + gap)

        self._sync_tree_scroll("pub")
        self._sync_tree_scroll("recv")

        # 记录卡片位置，供背景采样与染色使用
        self._card_rects = {
            "info": (margin, content_top, left_w, info_h),
            "qr": (margin, content_top + info_h + gap, left_w, qr_h),
            "pub": (margin + left_w + gap, content_top, right_w, pub_h),
            "recv": (margin + left_w + gap, content_top + pub_h + gap, right_w, recv_h),
        }
        self._apply_card_tints()
        self._redraw_bg()

    def _load_bg_config(self):
        """从 settings.json 读取背景配置。"""
        try:
            bg = lan_transfer.load_settings().get("background")
        except Exception:
            bg = None
        if not isinstance(bg, dict):
            bg = {}
        path = str(bg.get("path") or "")
        mode = str(bg.get("mode") or "fill")
        if mode not in BG_MODES:
            mode = "fill"
        try:
            dim = int(bg.get("dim", BG_DEFAULT_DIM))
        except (TypeError, ValueError):
            dim = BG_DEFAULT_DIM
        return path, mode, max(0, min(100, dim))

    def _save_bg_config(self):
        try:
            cfg = lan_transfer.load_settings()
            cfg["background"] = {"path": self._bg_path, "mode": self._bg_mode,
                                 "dim": int(self._bg_dim)}
            lan_transfer.save_settings(cfg)
        except Exception:
            pass

    def _save_ui_config(self, language=None, opacity_set=False):
        """折叠状态 / 卡片不透明度 / 语言写入 settings.json。"""
        try:
            cfg = lan_transfer.load_settings()
            ui = cfg.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            ui["collapsed"] = {key: bool(self._collapsed[key]) for key in CARD_KEYS}
            ui["card_opacity"] = int(self._card_opacity)
            if opacity_set:
                ui["card_opacity_set"] = True
            ui["language"] = language or tr.current_language()
            cfg["ui"] = ui
            lan_transfer.save_settings(cfg)
        except Exception:
            pass

    def _load_bg_source(self):
        """读入背景原图并缓存；大图先等比缩到 2560，避免每次绘制都读盘/缩放。"""
        path = self._bg_path
        if not path or not os.path.isfile(path):
            self._bg_src, self._bg_src_key = None, None
            return None
        try:
            stat = os.stat(path)
            key = (path, stat.st_mtime, stat.st_size)
        except OSError:
            key = (path, 0, 0)
        if key == self._bg_src_key:
            return self._bg_src
        try:
            from PIL import Image, ImageOps
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            if max(img.size) > 2560:
                img.thumbnail((2560, 2560), Image.LANCZOS)
            self._bg_src, self._bg_src_key = img, key
        except Exception:
            self._bg_src, self._bg_src_key = None, key
        return self._bg_src

    def _compose_bg(self, width, height):
        """按显示方式合成背景，并叠加遮罩浓度；返回 PIL 图像或 None。"""
        from PIL import Image
        base = Image.new("RGB", (width, height), (15, 20, 37))
        src = self._load_bg_source()
        if src is None:
            return base if self._bg_path else None

        sw, sh = src.size
        mode = self._bg_mode
        if mode == "fill":
            ratio = max(width / float(sw), height / float(sh))
            img = src.resize((max(1, int(sw * ratio)), max(1, int(sh * ratio)),
                              ), Image.BILINEAR)
            left = max(0, (img.width - width) // 2)
            top_px = max(0, (img.height - height) // 2)
            img = img.crop((left, top_px, left + width, top_px + height))
        elif mode == "fit":
            ratio = min(width / float(sw), height / float(sh))
            img = src.resize((max(1, int(sw * ratio)), max(1, int(sh * ratio)),
                              ), Image.BILINEAR)
            canvas_img = base.copy()
            canvas_img.paste(img, ((width - img.width) // 2,
                                   (height - img.height) // 2))
            img = canvas_img
        elif mode == "tile":
            ratio = 480.0 / sh if sh > 480 else 1.0
            tile = src.resize((max(1, int(sw * ratio)), max(1, int(sh * ratio))),
                              Image.BILINEAR)
            img = base.copy()
            for ty in range(0, height, tile.height):
                for tx in range(0, width, tile.width):
                    img.paste(tile, (tx, ty))
        else:  # center
            img = base.copy()
            if sw > width or sh > height:
                left = max(0, (sw - width) // 2)
                top_px = max(0, (sh - height) // 2)
                img.paste(src.crop((left, top_px, min(sw, left + width),
                                   min(sh, top_px + height))), (0, 0))
            else:
                img.paste(src, ((width - sw) // 2, (height - sh) // 2))

        dim = max(0, min(100, int(self._bg_dim)))
        if dim > 0:
            dark = Image.new("RGB", (width, height), (8, 11, 20))
            img = Image.blend(img, dark, min(0.94, dim / 100.0 * 0.94))
        return img

    def _sample_avg(self, rect):
        """取卡片区域在背景图上的平均色。"""
        img = self._bg_composed
        if img is None:
            return BG_BASE_RGB
        from PIL import Image
        x, y, w, h = rect
        box = (max(0, int(x)), max(0, int(y)),
               min(img.width, max(1, int(x + w))), min(img.height, max(1, int(y + h))))
        if box[2] <= box[0] or box[3] <= box[1]:
            return BG_BASE_RGB
        try:
            resample = getattr(Image, "BOX", None) or Image.BILINEAR
            px = img.crop(box).resize((1, 1), resample).getpixel((0, 0))
            return tuple(int(v) for v in px[:3])
        except Exception:
            return BG_BASE_RGB

    def _base_option(self, widget, option):
        """记录控件第一次见到时的原始色，之后每次都按它重算，避免旧色被当成新基准。"""
        attr = "_lt_base_" + option
        if not hasattr(widget, attr):
            try:
                value = str(widget.cget(option))
            except tk.TclError:
                value = ""
            setattr(widget, attr, value)
        return getattr(widget, attr)

    def _apply_card_tints(self):
        """按「采样平均色 + 卡片不透明度」给每张卡片算出填充色与描边色。

        说明：Tk 子控件不支持真正的 alpha，这里是把背景平均色抬到玻璃亮度后
        与卡片色线性混合得到固定填充色，模拟半透明观感，并非真正的透明通道。
        背景色权重 = 1 - 不透明度/100，滑块每动一档都会重新计算。
        """
        if not self._card_rects:
            return
        alpha = self._card_opacity / 100.0       # 100% = 实色卡片
        style = ttk.Style()
        for key, rect in self._card_rects.items():
            avg = self._sample_avg(rect)
            target = glass_target(avg)
            fill = darken_clamp(blend_rgb(target, PANEL_RGB, alpha), 150)
            fill_alt = darken_clamp(blend_rgb(target, PANEL_ALT_RGB, alpha), 168)
            line = darken_clamp(blend_rgb(target, CARD_LINE_RGB, min(1.0, alpha + 0.2)), 175)
            btn_bg = darken_clamp(blend_rgb(target, CARD_LINE_RGB, max(0.55, alpha)), 160)
            head_bg = darken_clamp(blend_rgb(target, (35, 42, 68), alpha), 172)

            fill_hex = "#%02X%02X%02X" % fill
            alt_hex = "#%02X%02X%02X" % fill_alt
            btn_hex = "#%02X%02X%02X" % btn_bg
            line_hex = "#%02X%02X%02X" % line

            c = self._cards[key]
            for widget in (c["card"], c["header"], c["body"], c["title"]):
                try:
                    widget.configure(bg=fill_hex)
                except tk.TclError:
                    pass
            try:
                c["card"].configure(highlightbackground=line_hex, highlightcolor=line_hex)
            except tk.TclError:
                pass

            mapping = {PANEL: fill_hex, PANEL_ALT: alt_hex, CARD_LINE: btn_hex}
            self._tint_children(c["body"], mapping)
            self._tint_children(c["header"], mapping)

            prefix = {"pub": "Pub", "recv": "Recv"}.get(key)
            if prefix:
                row_bg = fill_hex
                zebra = alt_hex
                head = "#%02X%02X%02X" % head_bg
                tree = self.pub_tree if key == "pub" else self.recv_tree
                style.configure(prefix + ".Treeview", background=row_bg,
                                fieldbackground=row_bg, foreground="#FFFFFF")
                style.configure(prefix + ".Treeview.Heading", background=head,
                                foreground="#FFFFFF")
                style.configure(prefix + ".Vertical.TScrollbar",
                                background=alt_hex, troughcolor=row_bg,
                                bordercolor=row_bg, darkcolor=alt_hex,
                                lightcolor=alt_hex)
                tree.tag_configure("even", background=row_bg, foreground="#FFFFFF")
                tree.tag_configure("odd", background=zebra, foreground="#FFFFFF")

    def _tint_children(self, parent, mapping):
        """递归按原始基准色染色；按钮同步 hover 色，重复调用也不会残留旧色。"""
        for child in parent.winfo_children():
            for option in ("bg", "activebackground", "selectcolor", "troughcolor"):
                base = self._base_option(child, option)
                if base in mapping:
                    try:
                        child.configure(**{option: mapping[base]})
                    except tk.TclError:
                        pass

            if isinstance(child, tk.Button) and not getattr(child, "_primary_button", False):
                base = self._base_option(child, "bg")
                target = mapping.get(base)
                if target:
                    child._normal_bg = target
                    child._hover_bg = "#%02X%02X%02X" % lighten(
                        tuple(int(target[i:i + 2], 16) for i in (1, 3, 5)))
                    try:
                        child.configure(bg=target, activebackground=child._hover_bg)
                    except tk.TclError:
                        pass

            self._tint_children(child, mapping)

    def _redraw_bg(self):
        """重建背景图并贴到画布；尺寸/配置没变就跳过，避免重复缩放。"""
        self._bg_job = None
        try:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
        except tk.TclError:
            return
        if width < 2 or height < 2:
            return

        signature = (width, height, self._bg_path, self._bg_mode, int(self._bg_dim),
                     self._bg_src_key, int(self._card_opacity))
        if signature == self._bg_drawn and not self._dirty:
            return

        # 文件可能刚被换过：先取一次缓存键
        self._load_bg_source()
        signature = (width, height, self._bg_path, self._bg_mode, int(self._bg_dim),
                     self._bg_src_key, int(self._card_opacity))
        try:
            composed = self._compose_bg(width, height)
            self._bg_composed = composed
            if composed is None:
                self.canvas.itemconfigure(self._bg_item, image="")
                self._bg_photo = None
            else:
                from PIL import ImageTk
                self._bg_photo = ImageTk.PhotoImage(composed)
                self.canvas.itemconfigure(self._bg_item, image=self._bg_photo)
        except Exception:
            self._bg_composed = None
            self.canvas.itemconfigure(self._bg_item, image="")
            self._bg_photo = None
        self._bg_drawn = signature
        self._dirty = False
        self._apply_card_tints()

    def _schedule_bg(self):
        self._dirty = True
        if self._bg_job:
            try:
                self.root.after_cancel(self._bg_job)
            except Exception:
                pass
        self._bg_job = self.root.after(150, self._redraw_bg)

    # ---------------------------------------------------------------- 背景设置
    def _open_bg_dialog(self):
        if self._bg_dialog is not None:
            try:
                if self._bg_dialog.winfo_exists():
                    self._bg_dialog.lift()
                    self._bg_dialog.focus_set()
                    return
            except tk.TclError:
                pass

        win = tk.Toplevel(self.root)
        self._bg_dialog = win
        win.title(tr.t("bg_dialog_title"))
        win.configure(bg=BG_BASE)
        win.resizable(False, False)
        win.transient(self.root)

        wrap = tk.Frame(win, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(wrap, text=tr.t("bg_head"), bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(wrap, text=tr.t("bg_formats"),
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                 wraplength=420, justify="left").pack(anchor="w", padx=12)

        self.bg_file_label = tk.Label(
            wrap, text=self._bg_summary(), bg=PANEL, fg=TEXT_SOFT,
            font=("Segoe UI", 9), wraplength=420, justify="left")
        self.bg_file_label.pack(anchor="w", padx=12, pady=(10, 8))

        row = tk.Frame(wrap, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_pick_bg = self._make_btn(row, tr.t("bg_pick"), self._choose_bg, primary=True)
        self.btn_pick_bg.pack(side="left", padx=(0, 6))
        self.btn_reset_bg = self._make_btn(row, tr.t("bg_reset"), self._reset_bg)
        self.btn_reset_bg.pack(side="left")

        tk.Label(wrap, text=tr.t("bg_mode_label"), bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(4, 2))
        mode_row = tk.Frame(wrap, bg=PANEL)
        mode_row.pack(fill="x", padx=8, pady=(0, 8))
        self._bg_mode_var = tk.StringVar(value=self._bg_mode)
        for mode in BG_MODES:
            tk.Radiobutton(
                mode_row, text=tr.t("mode_" + mode), variable=self._bg_mode_var,
                value=mode, command=self._on_mode_change,
                bg=PANEL, fg=TEXT_SOFT, activebackground=PANEL, activeforeground=TEXT,
                selectcolor=PANEL_ALT, highlightthickness=0,
                font=("Segoe UI", 9), borderwidth=0, padx=2).pack(
                side="left", padx=(4, 10))

        # ---------------- 卡片不透明度 ----------------
        op_head = tk.Frame(wrap, bg=PANEL)
        op_head.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(op_head, text=tr.t("opacity_label"), bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.opacity_label = tk.Label(op_head, text="%d%%" % self._card_opacity,
                                      bg=PANEL, fg=ACCENT2, font=("Segoe UI", 9))
        self.opacity_label.pack(side="right")

        self._opacity_var = tk.IntVar(value=int(self._card_opacity))
        self._dialog_op_scale = tk.Scale(
            wrap, from_=CARD_OPACITY_MIN, to=CARD_OPACITY_MAX, orient="horizontal",
            variable=self._opacity_var, command=self._on_opacity_move, showvalue=False,
            bg=ACCENT, fg=TEXT, troughcolor="#0B1020", highlightthickness=0,
            sliderrelief="flat", activebackground=ACCENT2, bd=0,
            length=420)
        self._dialog_op_scale.pack(fill="x", padx=12, pady=(2, 2))
        tk.Label(wrap, text=tr.t("opacity_tip"), bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8), wraplength=420,
                 justify="left").pack(anchor="w", padx=12)

        # ---------------- 背景遮罩浓度 ----------------
        dim_head = tk.Frame(wrap, bg=PANEL)
        dim_head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(dim_head, text=tr.t("bg_dim_label"), bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.bg_dim_label = tk.Label(dim_head, text="%d%%" % self._bg_dim,
                                     bg=PANEL, fg=ACCENT2, font=("Segoe UI", 9))
        self.bg_dim_label.pack(side="right")

        self._bg_dim_var = tk.IntVar(value=int(self._bg_dim))
        tk.Scale(
            wrap, from_=0, to=100, orient="horizontal", variable=self._bg_dim_var,
            command=self._on_dim_move, showvalue=False,
            bg=PANEL, fg=TEXT, troughcolor=PANEL_ALT, highlightthickness=0,
            sliderrelief="flat", activebackground=ACCENT, bd=0,
            length=420).pack(fill="x", padx=12, pady=(2, 2))
        tk.Label(wrap, text=tr.t("bg_dim_tip"),
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=420, justify="left").pack(anchor="w", padx=12)

        # ---------------- 语言 ----------------
        lang_head = tk.Frame(wrap, bg=PANEL)
        lang_head.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(lang_head, text=tr.t("lang_label"), bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        names = [name for _code, name in tr.LANGUAGES]
        current_name = dict(tr.LANGUAGES).get(tr.current_language(), names[0])
        self._lang_var = tk.StringVar(value=current_name)
        lang_menu = tk.OptionMenu(lang_head, self._lang_var, *names,
                                  command=self._on_lang_change)
        lang_menu.configure(bg=PANEL_ALT, fg=TEXT, activebackground=CARD_LINE,
                            activeforeground=TEXT, relief="flat", bd=0,
                            highlightthickness=0, font=("Segoe UI", 9), padx=8)
        try:
            lang_menu["menu"].configure(bg=PANEL_ALT, fg=TEXT,
                                        activebackground=ACCENT, activeforeground="#FFFFFF",
                                        font=("Segoe UI", 9))
        except tk.TclError:
            pass
        lang_menu.pack(side="right")
        tk.Label(wrap, text=tr.t("lang_note"), bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8), wraplength=420,
                 justify="left").pack(anchor="w", padx=12, pady=(2, 0))

        close_row = tk.Frame(wrap, bg=PANEL)
        close_row.pack(fill="x", padx=12, pady=(12, 10))
        tk.Label(close_row, text=tr.t("bg_saved_tip"),
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side="left")
        self._make_btn(close_row, tr.t("btn_close"), win.destroy).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        win.update_idletasks()
        px = self.root.winfo_rootx() + max(20, (self.root.winfo_width() - win.winfo_width()) // 2)
        py = self.root.winfo_rooty() + max(20, (self.root.winfo_height() - win.winfo_height()) // 3)
        win.geometry("+%d+%d" % (max(10, px), max(10, py)))

    def _bg_summary(self):
        if self._bg_path and os.path.isfile(self._bg_path):
            return tr.t("bg_current_file", name=os.path.basename(self._bg_path))
        return tr.t("bg_current_default")

    def _choose_bg(self):
        path = filedialog.askopenfilename(
            title=tr.t("bg_pick_title"),
            filetypes=[(tr.t("bg_filter_image"), "*.jpg *.jpeg *.png *.webp *.bmp"),
                       (tr.t("filter_all"), "*.*")])
        if not path:
            return
        self._bg_path = os.path.abspath(path)
        self._apply_bg_change()

    def _reset_bg(self):
        self._bg_path = ""
        self._bg_mode = "fill"
        self._bg_dim = BG_DEFAULT_DIM
        if getattr(self, "_bg_mode_var", None) is not None:
            self._bg_mode_var.set(self._bg_mode)
        if getattr(self, "_bg_dim_var", None) is not None:
            self._bg_dim_var.set(self._bg_dim)
        if getattr(self, "bg_dim_label", None) is not None:
            self.bg_dim_label.config(text="%d%%" % self._bg_dim)
        self._apply_bg_change()

    def _on_mode_change(self):
        self._bg_mode = self._bg_mode_var.get()
        self._apply_bg_change()

    def _on_dim_move(self, value):
        try:
            self._bg_dim = int(float(value))
        except (TypeError, ValueError):
            return
        if getattr(self, "bg_dim_label", None) is not None:
            self.bg_dim_label.config(text="%d%%" % self._bg_dim)
        self._apply_bg_change()

    def _on_opacity_move(self, value):
        try:
            self._card_opacity = max(CARD_OPACITY_MIN,
                                     min(CARD_OPACITY_MAX, int(float(value))))
        except (TypeError, ValueError):
            return
        for scale in (getattr(self, "op_scale", None),
                      getattr(self, "_dialog_op_scale", None)):
            if scale is not None:
                try:
                    if int(float(scale.get())) != self._card_opacity:
                        scale.set(self._card_opacity)
                except (tk.TclError, ValueError):
                    pass
        for label in (getattr(self, "opacity_label", None),
                      getattr(self, "top_opacity_label", None)):
            if label is not None:
                try:
                    label.config(text="%d%%" % self._card_opacity)
                except tk.TclError:
                    pass
        self._save_ui_config(opacity_set=True)
        self._apply_card_tints()

    def _on_lang_change(self, name):
        code = next((c for c, n in tr.LANGUAGES if n == name), tr.current_language())
        if code == tr.current_language():
            return
        self._save_ui_config(language=code)
        if messagebox.askyesno(tr.t("lang_restart_title"), tr.t("lang_restart_msg"),
                               default=messagebox.YES, parent=self._bg_dialog or self.root):
            self._restart_app()

    def _restart_app(self):
        """按保存后的语言重启窗口。"""
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            args.append(os.path.abspath(sys.argv[0]))
        args += [a for a in sys.argv[1:] if a != "--headless"]
        try:
            subprocess.Popen(args, cwd=os.getcwd())
        except Exception:
            return
        try:
            self._stop_server()
        except Exception:
            pass
        self.root.destroy()

    def _apply_bg_change(self):
        if getattr(self, "bg_file_label", None) is not None:
            try:
                self.bg_file_label.config(text=self._bg_summary())
            except tk.TclError:
                pass
        self._bg_src_key = None       # 换图后强制重新读盘
        self._save_bg_config()
        self._schedule_bg()

    def _setup_tree(self, tree, columns, prefix):
        """统一两个文件列表的列宽和深色样式（白字 + 斑马纹 + 蓝色选中）。"""
        tree.heading("name", text=tr.t("col_name"))
        tree.heading("size", text=tr.t("col_size"))
        tree.column("name", width=300, minwidth=160, stretch=True, anchor="w")
        tree.column("size", width=90, minwidth=70, stretch=False, anchor="e")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        row_bg = PANEL
        zebra_bg = PANEL_ALT
        head_bg = "#232A44"
        head_active_bg = "#2A3354"
        select_bg = ACCENT
        text_fg = "#FFFFFF"

        style.configure(prefix + ".Treeview",
                        background=row_bg, foreground=text_fg,
                        fieldbackground=row_bg, borderwidth=0,
                        rowheight=26, font=("Segoe UI", 10))
        style.configure(prefix + ".Treeview.Heading",
                        background=head_bg, foreground=text_fg,
                        relief="flat", borderwidth=0,
                        font=("Segoe UI", 10, "bold"))
        style.map(prefix + ".Treeview",
                  foreground=[("selected", text_fg), ("!selected", text_fg)],
                  background=[("selected", select_bg)])
        style.map(prefix + ".Treeview.Heading",
                  foreground=[("active", text_fg), ("!active", text_fg)],
                  background=[("active", head_active_bg), ("!active", head_bg)])

        # 滚动条也用深色，避免白色系统滚动条破坏整体观感
        style.configure(prefix + ".Vertical.TScrollbar",
                        background=zebra_bg, troughcolor=row_bg,
                        bordercolor=row_bg, arrowcolor=MUTED,
                        darkcolor=zebra_bg, lightcolor=zebra_bg,
                        relief="flat", borderwidth=0)
        style.map(prefix + ".Vertical.TScrollbar",
                  background=[("active", "#34406B"), ("pressed", ACCENT)],
                  arrowcolor=[("active", TEXT_SOFT)])

        tree.tag_configure("even", background=row_bg, foreground=text_fg)
        tree.tag_configure("odd", background=zebra_bg, foreground=text_fg)
        tree.configure(style=prefix + ".Treeview")

    def _make_btn(self, parent, text, command, primary=False, small=False):
        """按钮：统一圆角观感（扁平），并带 hover / 按下态。"""
        if primary:
            normal, hover = ACCENT, "#6E9AFF"
            fg = "#FFFFFF"
        else:
            normal, hover = CARD_LINE, "#34406B"
            fg = TEXT_SOFT
        btn = tk.Button(parent, text=text, command=command,
                        bg=normal, fg=fg, activebackground=(ACCENT_DEEP if primary else "#3A4570"),
                        activeforeground="#FFFFFF", relief="flat", bd=0,
                        font=("Segoe UI", 9 if small else 10, "bold" if primary else "normal"),
                        padx=10 if small else 14, pady=4 if small else 6,
                        cursor="hand2", highlightthickness=0)
        btn._primary_button = primary
        btn._normal_bg = normal
        btn._hover_bg = hover

        def on_enter(_event):
            if str(btn.cget("state")) == "normal":
                btn.configure(bg=btn._hover_bg)

        def on_leave(_event):
            if str(btn.cget("state")) == "normal":
                btn.configure(bg=btn._normal_bg)

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    # ------------------------------------------------------------- 服务控制
    def _try_start(self):
        """启动时自动启动服务（延迟 500ms）。"""
        self.root.after(500, self._start_server)

    def _toggle_server(self):
        if self._running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self):
        if self._running:
            return

        try:
            os.makedirs(lan_transfer.PUBLISH_DIR, exist_ok=True)
            os.makedirs(lan_transfer.RECEIVE_DIR, exist_ok=True)

            # 每次启动生成新的访问口令
            import secrets
            lan_transfer.TOKEN = "".join(
                secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))

            # 是否广播发现由界面开关决定（默认开）
            lan_transfer.DISCOVERY_ENABLED = bool(self.discovery_var.get())

            # bind="lan"：只监听本机局域网 IP，端口被占用会自动顺延
            self.server = lan_transfer.run(port=8765, open_browser=False, bind="lan")
            self._running = True

            self.root.after(0, self._on_server_started)

        except Exception as e:
            messagebox.showerror(tr.t("dlg_start_failed"), str(e))
            self._running = False

    def _stop_server(self):
        if not self._running:
            return

        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        except Exception:
            pass
        lan_transfer.stop_beacon()
        self.server = None
        self._running = False
        self._share_url = ""

        self.status_indicator.config(text=tr.t("status_off"), fg=DANGER)
        self.btn_toggle.config(text=tr.t("btn_start"))
        self._set_info(tr.t("service_stopped"))
        self.url_label.config(text="")
        self._clear_qr()
        self._update_discovery_label()
        self._update_card_summaries()

    def _toggle_discovery(self):
        """切换「在局域网内可见」，并持久化到 settings.json。"""
        enabled = bool(self.discovery_var.get())
        lan_transfer.set_discovery_enabled(enabled, persist=True)
        self._update_discovery_label()
        if self._running:
            self._on_server_started()

    def _update_discovery_label(self):
        if not self._running:
            self.lbl_discovery.config(text=tr.t("disc_off"), fg=MUTED)
        elif lan_transfer.DISCOVERY_ENABLED:
            self.lbl_discovery.config(text=tr.t("disc_on"), fg=ACCENT2)
        else:
            self.lbl_discovery.config(text=tr.t("disc_closed"), fg=MUTED)

    def _on_server_started(self):
        """服务启动后更新界面（在主线程中调用）。"""
        try:
            ip = lan_transfer.local_ip()
            port = lan_transfer.PORT
            token = lan_transfer.TOKEN
            tls = getattr(lan_transfer, "USE_TLS", False)
            self._share_url = lan_transfer.build_url(ip)

            self.status_indicator.config(text=tr.t("status_running"), fg=ACCENT2)
            self.btn_toggle.config(text=tr.t("btn_stop"))

            if lan_transfer.BIND_LAN_ONLY:
                bind_line = tr.t("bind_lan_only", ip=ip)
            else:
                bind_line = tr.t("bind_all", ip=ip)
            if lan_transfer.PORT_IS_FALLBACK:
                bind_line += "\n" + tr.t("port_fallback", requested=lan_transfer.PORT_REQUESTED,
                                          port=port)

            info_lines = [
                "%s %s" % (tr.t("lbl_host"), socket.gethostname()),
                "%s %s" % (tr.t("lbl_ip"), ip),
                "%s %d" % (tr.t("lbl_port"), port),
                "%s %s  %s" % (tr.t("lbl_token"), token, tr.t("token_note")),
                "%s %s" % (tr.t("lbl_tls"),
                           tr.t("tls_on") if tls else tr.t("tls_off")),
                bind_line,
            ]
            info_text = "\n".join(info_lines)
            info_text += "\n" + tr.t("sp_header", file=os.path.basename(lan_transfer.SPACES_FILE))
            if lan_transfer.SPACES:
                for name, space in lan_transfer.SPACES.items():
                    info_text += "\n  %s [%s] %s" % (name, space["mode"], space["path"])
            info_text += "\n" + tr.t("discovery_line",
                                     state=(tr.t("disc_on_long")
                                            if lan_transfer.DISCOVERY_ENABLED
                                            else tr.t("disc_off_long")),
                                     port=lan_transfer.DISCOVERY_PORT)
            self._set_info(info_text)

            self.url_label.config(text=self._share_url)
            self._show_qr(self._share_url)
            self._refresh_all()
            self._update_discovery_label()
            self._update_card_summaries()

        except Exception as e:
            messagebox.showerror(tr.t("dlg_error"), str(e))

    def _set_info(self, text):
        self.info_text.config(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.config(state="disabled")
        # 行数自适应（长路径会换行，留 3 行余量），避免空间/目录信息被截断
        lines = text.count("\n") + 1
        self._info_lines = lines
        self.info_text.config(height=min(max(lines + 1, 6), 20))
        self.root.after(60, self._layout)

    def _show_qr(self, url):
        """记录二维码地址并按当前可用高度绘制。"""
        self._qr_url = url
        self._qr_box = 0
        self._qr_modules = 0
        box = self._qr_box_for_height(self.qr_card.winfo_height())
        self._render_qr(box)

    def _qr_box_for_height(self, card_h):
        """根据二维码卡片高度选择模块像素，保证二维码和下方链接都放得下。"""
        # 固定开销：卡片上下留白 + 标题 + 链接文字 + 各处间距（实测约 72px）
        fixed = 72
        if self._qr_modules:
            box = int(max(2, min(5, (max(80, card_h) - fixed) // self._qr_modules)))
        else:
            box = 4
        return box

    def _render_qr(self, box_size):
        """绘制二维码；模块像素变化时重画，避免被上部空间挤压而只显示一半。"""
        from PIL import ImageTk

        if not self._qr_url:
            return
        img = gen_qr_image(self._qr_url, box_size=box_size, border=3)
        if img is None:
            self.qr_label.config(image="", text=tr.t("qr_missing_lib"))
            return

        # qrcode 的 PilImage.width 是模块数、height 是像素数，统一用 height/box 求模块总数（含边框）
        modules = max(1, int(getattr(img, "height", 0)) // max(1, int(box_size)))
        self._qr_modules = modules
        self._qr_box = box_size
        self._qr_photo = ImageTk.PhotoImage(img)

        for w in self.qr_container.winfo_children():
            w.destroy()

        self.qr_label = tk.Label(self.qr_container, image=self._qr_photo,
                                 bg="white", padx=4, pady=2)
        self.qr_label.pack(expand=True, pady=2)

    def _clear_qr(self):
        self._qr_url = ""
        self._qr_box = 0
        self._qr_modules = 0
        for w in self.qr_container.winfo_children():
            w.destroy()
        self.qr_label = tk.Label(self.qr_container, text=tr.t("qr_placeholder"),
                                 bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        self.qr_label.pack(expand=True)
        self._qr_photo = None

    # --------------------------------------------------------------- 文件操作
    def _refresh_tree(self, tree, items):
        """清空并填充 Treeview。"""
        for item in tree.get_children():
            tree.delete(item)
        for idx, f in enumerate(items):
            tree.insert("", "end", tags=("odd" if idx % 2 else "even",),
                        values=(f["name"], lan_transfer.human_size(f["size"]),))

    # --------------------------------------------------------- 分区（spaces）
    def _space_names(self):
        return list(lan_transfer.SPACES.keys())

    def _current_space(self, key):
        """当前选中的分区；选中项失效时回退到默认分区。"""
        var = self.pub_space_var if key == "pub" else self.recv_space_var
        name = var.get()
        space = lan_transfer.SPACES.get(name)
        if space is None:
            name = lan_transfer.DEFAULT_SPACE_NAME
            space = lan_transfer.SPACES.get(name)
            var.set(name)
        return space

    def _sync_space_controls(self):
        """分区增删改后同步下拉取值与只读提示。"""
        names = self._space_names()
        for key, combo in (("pub", self.pub_space_combo), ("recv", self.recv_space_combo)):
            combo.configure(values=names)
            space = self._current_space(key)
            if space is not None:
                combo.set(space["name"])
        pub_space = self._current_space("pub")
        ro = bool(pub_space and pub_space["mode"] == "ro")
        for btn in (self.btn_add_pub, self.btn_add_pub_dir, self.btn_delete_pub):
            try:
                if ro:
                    btn.config(state="disabled", bg=PANEL_ALT, disabledforeground=MUTED)
                else:
                    btn.config(state="normal", bg=btn._normal_bg,
                               disabledforeground=MUTED)
            except tk.TclError:
                pass
        if ro:
            hint = tr.t("space_ro_hint")
        elif pub_space and pub_space["name"] == lan_transfer.DEFAULT_SPACE_NAME:
            hint = tr.t("space_default_tag")
        else:
            hint = ""
        self.pub_space_hint.config(text=hint)
        self.recv_space_hint.config(
            text=tr.t("spaces_fallback") if lan_transfer.SPACES_FALLBACK_REASON else "")

    def _on_space_change(self, key):
        """切换分区：立刻按新分区的目录刷新列表。"""
        self._sync_space_controls()
        self._refresh_all()

    def _open_spaces_dialog(self):
        if getattr(self, "_spaces_win", None) is not None:
            try:
                if self._spaces_win.winfo_exists():
                    self._spaces_win.lift()
                    self._refresh_spaces_list()
                    return
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        self._spaces_win = win
        win.title(tr.t("spaces_title"))
        win.configure(bg=BG_BASE)
        win.geometry("760x420")
        win.transient(self.root)

        tk.Label(win, text=tr.t("spaces_hint"), bg=BG_BASE, fg=TEXT_SOFT,
                 font=("Segoe UI", 9), wraplength=720, justify="left").pack(
            anchor="w", padx=14, pady=(12, 4))

        self._spaces_warn = tk.Label(win, text="", bg=BG_BASE, fg=DANGER,
                                     font=("Segoe UI", 9), wraplength=720, justify="left")
        self._spaces_warn.pack(anchor="w", padx=14)

        wrap = tk.Frame(win, bg=BG_BASE)
        wrap.pack(fill="both", expand=True, padx=14, pady=8)
        columns = ("name", "path", "mode", "files", "size", "default")
        heads = (tr.t("col_space_name"), tr.t("col_space_path"), tr.t("col_space_mode"),
                 tr.t("col_space_files"), tr.t("col_space_size"), tr.t("col_space_default"))
        self.spaces_tree = ttk.Treeview(wrap, columns=columns, show="headings", height=9,
                                        style="Pub.Treeview")
        for col, head, width in zip(columns, heads, (110, 300, 80, 70, 90, 70)):
            self.spaces_tree.heading(col, text=head)
            self.spaces_tree.column(col, width=width, anchor="w" if col in ("name", "path") else "center")
        scroll = ttk.Scrollbar(wrap, orient="vertical", style="Pub.Vertical.TScrollbar",
                               command=self.spaces_tree.yview)
        self.spaces_tree.configure(yscroll=scroll.set)
        self.spaces_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.spaces_tree.bind("<Double-1>", lambda _e: self._edit_space())

        btns = tk.Frame(win, bg=BG_BASE)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        self._make_btn(btns, tr.t("space_new"), self._new_space, primary=True).pack(side="left")
        self._make_btn(btns, tr.t("space_edit"), self._edit_space).pack(side="left", padx=(8, 0))
        self._make_btn(btns, tr.t("space_delete"), self._delete_space).pack(side="left", padx=(8, 0))
        self._make_btn(btns, tr.t("space_set_default"), self._default_space).pack(side="left", padx=(8, 0))
        self._make_btn(btns, tr.t("space_refresh"), self._refresh_spaces_list).pack(side="left", padx=(8, 0))
        self._make_btn(btns, tr.t("space_cancel"), win.destroy).pack(side="right")
        self._refresh_spaces_list()

    def _refresh_spaces_list(self):
        tree = getattr(self, "spaces_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        for space in lan_transfer.spaces_summary():
            tree.insert("", "end", iid=space["name"], values=(
                space["name"], space["path"],
                tr.t("mode_ro") if space["mode"] == "ro" else tr.t("mode_rw"),
                space["files"], lan_transfer.human_size(space["size"]),
                tr.t("space_default_tag") if space["default"] else "",
            ))
        reason = lan_transfer.SPACES_FALLBACK_REASON
        warn = getattr(self, "_spaces_warn", None)
        if warn is not None:
            warn.config(text=(tr.t("spaces_fallback") + "  " + reason) if reason else "")

    def _selected_space_name(self):
        tree = getattr(self, "spaces_tree", None)
        if tree is None:
            return ""
        selected = tree.selection()
        if not selected:
            messagebox.showinfo(tr.t("dlg_notice"), tr.t("select_first"))
            return ""
        return selected[0]

    def _new_space(self):
        self._space_form(None)

    def _edit_space(self):
        name = self._selected_space_name()
        if name:
            space = lan_transfer.SPACES.get(name)
            if space:
                self._space_form(space)

    def _space_form(self, space):
        """新建 / 编辑分区表单。"""
        editing = space is not None
        win = tk.Toplevel(self.root)
        win.title(tr.t("space_form_edit") if editing else tr.t("space_form_new"))
        win.configure(bg=BG_BASE)
        win.geometry("520x300")
        win.transient(self.root)
        win.grab_set()

        form = tk.Frame(win, bg=BG_BASE)
        form.pack(fill="both", expand=True, padx=16, pady=14)
        form.columnconfigure(1, weight=1)

        def label(row, text):
            tk.Label(form, text=text, bg=BG_BASE, fg=TEXT_SOFT, font=("Segoe UI", 9)).grid(
                row=row, column=0, sticky="w", pady=6)

        name_var = tk.StringVar(value=space["name"] if editing else "")
        path_var = tk.StringVar(value=space["path"] if editing else "")
        mode_var = tk.StringVar(value=space["mode"] if editing else "rw")
        desc_var = tk.StringVar(value=space.get("description", "") if editing else "")

        label(0, tr.t("fld_space_name"))
        entry_name = tk.Entry(form, textvariable=name_var, font=("Segoe UI", 9))
        entry_name.grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)

        label(1, tr.t("fld_space_path"))
        entry_path = tk.Entry(form, textvariable=path_var, font=("Segoe UI", 9))
        entry_path.grid(row=1, column=1, sticky="ew", pady=6)

        def browse():
            picked = filedialog.askdirectory(title=tr.t("fld_space_path"),
                                             initialdir=path_var.get() or None)
            if picked:
                path_var.set(picked)

        self._make_btn(form, tr.t("space_browse"), browse, small=True).grid(
            row=1, column=2, padx=(8, 0))

        label(2, tr.t("fld_space_mode"))
        mode_row = tk.Frame(form, bg=BG_BASE)
        mode_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=6)
        tk.Radiobutton(mode_row, text=tr.t("mode_rw"), variable=mode_var, value="rw",
                       bg=BG_BASE, fg=TEXT_SOFT, selectcolor=PANEL_ALT,
                       activebackground=BG_BASE, font=("Segoe UI", 9)).pack(side="left")
        tk.Radiobutton(mode_row, text=tr.t("mode_ro"), variable=mode_var, value="ro",
                       bg=BG_BASE, fg=TEXT_SOFT, selectcolor=PANEL_ALT,
                       activebackground=BG_BASE, font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))

        label(3, tr.t("fld_space_desc"))
        tk.Entry(form, textvariable=desc_var, font=("Segoe UI", 9)).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=6)

        tip = tk.Label(form, text="", bg=BG_BASE, fg=DANGER, font=("Segoe UI", 9),
                       wraplength=460, justify="left")
        tip.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        def save():
            fields = dict(name=name_var.get(), path=path_var.get(),
                          mode=mode_var.get(), description=desc_var.get())
            try:
                if editing:
                    lan_transfer.update_space(space["name"], **fields)
                else:
                    lan_transfer.create_space(**fields)
            except lan_transfer.SpaceError as exc:
                tip.config(text=str(exc))
                return
            win.destroy()
            self._refresh_spaces_list()
            self._sync_space_controls()
            self._refresh_all()

        actions = tk.Frame(form, bg=BG_BASE)
        actions.grid(row=5, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self._make_btn(actions, tr.t("space_cancel"), win.destroy).pack(side="right")
        self._make_btn(actions, tr.t("space_save"), save, primary=True).pack(
            side="right", padx=(0, 8))
        entry_name.focus_set()

    def _ask_confirm(self, title, message, ok_text):
        """自绘确认框：文案可换行，按钮与主题一致，便于截图核对。"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=BG_BASE)
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        result = {"ok": False}
        tk.Label(win, text=message, bg=BG_BASE, fg=TEXT, justify="left",
                 wraplength=400, font=("Segoe UI", 10)).pack(padx=20, pady=(18, 12))
        row = tk.Frame(win, bg=BG_BASE)
        row.pack(pady=(0, 16), padx=20, anchor="e")

        def ok():
            result["ok"] = True
            win.destroy()

        self._make_btn(row, tr.t("space_cancel"), win.destroy).pack(side="right")
        self._make_btn(row, ok_text, ok, primary=True).pack(side="right", padx=(0, 8))
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_height()) // 3)
        win.geometry("+%d+%d" % (x, y))
        self.root.wait_window(win)
        return result["ok"]

    def _delete_space(self):
        name = self._selected_space_name()
        if not name:
            return
        if not self._ask_confirm(tr.t("dlg_confirm_delete"),
                                 tr.t("space_confirm_delete", name=name),
                                 tr.t("space_delete")):
            return
        try:
            lan_transfer.delete_space(name)
        except lan_transfer.SpaceError as exc:
            messagebox.showwarning(tr.t("dlg_notice"), str(exc))
            return
        self._refresh_spaces_list()
        self._sync_space_controls()
        self._refresh_all()
        messagebox.showinfo(tr.t("dlg_done"), tr.t("space_delete_ok", name=name))

    def _default_space(self):
        name = self._selected_space_name()
        if not name:
            return
        try:
            lan_transfer.set_default_space(name)
        except lan_transfer.SpaceError as exc:
            messagebox.showwarning(tr.t("dlg_notice"), str(exc))
            return
        self._refresh_spaces_list()
        self._sync_space_controls()
        self._refresh_all()

    def _refresh_all(self):
        """按当前选中的分区刷新发布/接收两个列表。"""
        pub_space = self._current_space("pub")
        recv_space = self._current_space("recv")
        before = (len(self.pub_tree.get_children()), len(self.recv_tree.get_children()))
        self._refresh_tree(self.pub_tree, lan_transfer.list_files(
            pub_space["path"] if pub_space else lan_transfer.PUBLISH_DIR,
            pub_space["name"] if pub_space else None))
        self._refresh_tree(self.recv_tree, lan_transfer.list_files(
            recv_space["path"] if recv_space else lan_transfer.RECEIVE_DIR,
            recv_space["name"] if recv_space else None))
        after = (len(self.pub_tree.get_children()), len(self.recv_tree.get_children()))
        if before != after:
            # 文件数变化后按新数量重新分配列表可见行数
            self._layout()
        self._update_card_summaries()

    def _auto_refresh(self):
        """定时刷新（接收目录可能有手机上传的新文件）。"""
        if self._running and not self._busy:
            self._refresh_all()
        self.root.after(3000, self._auto_refresh)

    def _set_busy(self, busy, text=""):
        """复制大文件期间禁用按钮，避免重复操作。"""
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_add_pub, self.btn_add_pub_dir, self.btn_delete_pub,
                    self.btn_refresh_recv, self.btn_open_pub, self.btn_open_pub_top):
            try:
                btn.config(state=state)
            except tk.TclError:
                pass
        self.lbl_recv_hint.config(text=text or tr.t("recv_busy_hint"))
        if not busy:
            self._sync_space_controls()

    def _pub_target_dir(self):
        space = self._current_space("pub")
        if space is None:
            return lan_transfer.PUBLISH_DIR
        if space["mode"] == "ro":
            messagebox.showwarning(tr.t("dlg_notice"), tr.t("space_ro_hint"))
            return None
        return space["path"]

    def _add_publish_files(self):
        """添加文件到当前发布分区（后台复制，大文件不卡界面）。"""
        target = self._pub_target_dir()
        if not target:
            return
        paths = filedialog.askopenfilenames(
            title=tr.t("choose_files_title"),
            # 第一项即默认筛选项：「所有文件」，不按后缀过滤；其余仅为快捷筛选
            filetypes=[
                (tr.t("filter_all"), "*.*"),
                (tr.t("filter_docs"), "*.txt *.md *.pdf *.doc *.docx *.ppt *.pptx "
                                     "*.xls *.xlsx *.csv *.json"),
                (tr.t("filter_images"), "*.jpg *.jpeg *.png *.gif *.webp *.bmp *.heic"),
                (tr.t("filter_media"), "*.mp3 *.wav *.m4a *.mp4 *.mov *.mkv *.avi *.webm"),
                (tr.t("filter_archives"), "*.zip *.rar *.7z *.tar *.gz"),
            ])
        if not paths:
            return
        self._copy_to_dir(list(paths), target, tr.t("card_pub"))

    def _add_publish_folder(self):
        """把整个文件夹里的所有文件平铺发布到当前分区（后台复制，不按后缀过滤）。"""
        target = self._pub_target_dir()
        if not target:
            return
        src = filedialog.askdirectory(title=tr.t("choose_folder_title"))
        if not src:
            return

        folder_name = os.path.basename(src.rstrip(os.sep)) or src
        self._set_busy(True, tr.t("busy_reading", name=folder_name))

        def work():
            import shutil
            count, errors = 0, []
            # 递归遍历，任意后缀都发布；分区只支持单层文件，因此平铺
            for root, _dirs, files in os.walk(src):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        dest = os.path.join(
                            target, lan_transfer.unique_name(name, target))
                        shutil.copy2(path, dest)
                        count += 1
                    except Exception as exc:
                        errors.append("%s: %s" % (path, exc))
            self.root.after(0, lambda: self._folder_copy_done(folder_name, count, errors))

        threading.Thread(target=work, daemon=True).start()

    def _folder_copy_done(self, folder_name, count, errors):
        self._set_busy(False)
        self._refresh_all()
        if errors:
            messagebox.showwarning(tr.t("dlg_partial_failed"), "\n".join(errors[:10]))
        if count:
            messagebox.showinfo(tr.t("dlg_done"),
                                tr.t("folder_done", count=count, name=folder_name))
        elif not errors:
            messagebox.showinfo(tr.t("dlg_notice"), tr.t("folder_empty", name=folder_name))

    def _copy_to_dir(self, paths, target_dir, label):
        """后台逐个复制文件，完成后回主线程刷新。"""
        self._set_busy(True, tr.t("busy_copying", count=len(paths)))

        def work():
            import shutil
            count, errors = 0, []
            for p in paths:
                try:
                    name = os.path.basename(p)
                    dest = os.path.join(target_dir,
                                        lan_transfer.unique_name(name, target_dir))
                    shutil.copy2(p, dest)
                    count += 1
                except Exception as exc:
                    errors.append("%s: %s" % (p, exc))
            self.root.after(0, lambda: self._copy_done(count, errors, label))

        threading.Thread(target=work, daemon=True).start()

    def _copy_done(self, count, errors, label):
        self._set_busy(False)
        self._refresh_all()
        if errors:
            messagebox.showwarning(tr.t("dlg_partial_failed"), "\n".join(errors[:10]))
        if count:
            messagebox.showinfo(tr.t("dlg_done"), tr.t("copy_done", count=count, label=label))

    def _delete_publish_files(self):
        """删除当前发布分区中选中的文件。"""
        space = self._current_space("pub")
        target = self._pub_target_dir()
        if not target:
            return
        selected = self.pub_tree.selection()
        if not selected:
            messagebox.showinfo(tr.t("dlg_notice"), tr.t("select_first"))
            return
        if not messagebox.askyesno(tr.t("dlg_confirm_delete"),
                                   tr.t("confirm_delete_n", count=len(selected))):
            return
        count = 0
        for iid in selected:
            values = self.pub_tree.item(iid, "values")
            name = values[0]
            try:
                os.remove(os.path.join(target, name))
                count += 1
            except OSError as e:
                messagebox.showwarning(tr.t("dlg_delete_failed"), "%s: %s" % (name, e))
        self._refresh_all()
        if count:
            messagebox.showinfo(tr.t("dlg_done"), tr.t("deleted_n", count=count))

    # ----------------------------------------------------------------- 其他
    def _open_browser(self):
        if not self._running:
            messagebox.showwarning(tr.t("dlg_notice"), tr.t("start_first"))
            return
        try:
            webbrowser.open(lan_transfer.build_url())
        except Exception as e:
            messagebox.showerror(tr.t("dlg_error"), str(e))

    def _copy_url(self):
        if not self._running or not self._share_url:
            messagebox.showwarning(tr.t("dlg_notice"), tr.t("start_first"))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._share_url)
        self.root.update()
        messagebox.showinfo(tr.t("dlg_link_copied"),
                            tr.t("link_copied", url=self._share_url))

    def _open_publish_folder(self):
        space = self._current_space("pub")
        self._open_dir(space["path"] if space else lan_transfer.PUBLISH_DIR)

    def _open_receive_folder(self):
        space = self._current_space("recv")
        self._open_dir(space["path"] if space else lan_transfer.RECEIVE_DIR)

    def _open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror(tr.t("dlg_error"), str(e))


def main():
    import traceback

    def handle_exception(exc_type, exc_value, exc_traceback):
        try:
            err_file = os.path.join(lan_transfer.BASE_DIR, "error.log")
            with open(err_file, "a", encoding="utf-8") as f:
                f.write("=" * 50 + "\n")
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
                f.write("\n")
        except Exception:
            pass
        try:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
        except Exception:
            pass

    sys.excepthook = handle_exception

    # 无界面常驻模式（供 CLI / 其他程序 / AI 调用）
    if "--headless" in sys.argv[1:]:
        sys.exit(lan_transfer.headless_main(sys.argv[1:]))

    root = tk.Tk()
    root.configure(bg=BG_BASE)
    app = LanTransferApp(root)

    def on_close():
        app._stop_server()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
