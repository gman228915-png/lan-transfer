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
CARD_OPACITY_MIN = 60
CARD_OPACITY_MAX = 100
CARD_OPACITY_DEFAULT = 88

CARD_KEYS = ("info", "qr", "pub", "recv")
CARD_TITLE_KEYS = {
    "info": "card_info",
    "qr": "card_qr",
    "pub": "card_pub",
    "recv": "card_recv",
}
COLLAPSED_H = 42


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
        self._place_window()
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
        self._card_opacity = max(CARD_OPACITY_MIN, min(CARD_OPACITY_MAX, opacity))

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

    def _place_window(self):
        """按屏幕尺寸自适应初始窗口，1366×768 也不超出屏幕。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        width = min(1120, max(940, sw - 96))
        height = min(820, max(620, sh - 80))
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - 20)
        self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))
        # 折叠后窗口要能收矮，同时保持不出现滚动条的最小可用尺寸
        self.root.minsize(940, 220)

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
        self.btn_expand_all = self._make_btn(head_right, tr.t("btn_expand_all"),
                                             self._expand_all, small=True)
        self.btn_expand_all.pack(side="right", padx=(10, 0))
        self.btn_collapse_all = self._make_btn(head_right, tr.t("btn_collapse_all"),
                                               self._collapse_all, small=True)
        self.btn_collapse_all.pack(side="right")
        tk.Label(head_right, text="v" + lan_transfer.APP_VERSION, font=("Segoe UI", 9),
                 fg=MUTED, bg=BG_HEADER).pack(side="right", padx=(0, 4))
        self.status_indicator = tk.Label(head_right, text=tr.t("status_stopped"),
                                         font=("Segoe UI", 11), fg=DANGER, bg=BG_HEADER)
        self.status_indicator.pack(side="right", padx=(0, 10))

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
        recv_wrap = tk.Frame(recv_body, bg=PANEL)
        recv_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        self.recv_tree = ttk.Treeview(recv_wrap, columns=columns, show="headings", height=6,
                                      style="Recv.Treeview")
        self._setup_tree(self.recv_tree, columns, "Recv")
        recv_scroll = ttk.Scrollbar(recv_wrap, orient="vertical",
                                    style="Recv.Vertical.TScrollbar",
                                    command=self.recv_tree.yview)
        self.recv_tree.configure(yscroll=recv_scroll.set)
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

        # 布局 & 背景
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.after(80, self._layout)
        self.root.after(80, self._redraw_bg)

        # 定时刷新
        self.root.after(2000, self._auto_refresh)

    def _create_card(self, key):
        """卡片容器：标题栏右侧带折叠按钮，正文放在 body 里。"""
        card = tk.Frame(self.canvas, bg=PANEL, highlightbackground=CARD_LINE,
                        highlightthickness=1)
        header = tk.Frame(card, bg=PANEL)
        header.pack(fill="x", padx=12, pady=(6, 2))

        title = tk.Label(header, text=tr.t(CARD_TITLE_KEYS[key]), bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 11, "bold"), anchor="w")
        title.pack(side="left")

        btn = tk.Button(header, text=tr.t("btn_collapse"),
                        command=lambda k=key: self._toggle_card(k),
                        bg=PANEL, fg=MUTED, activebackground=PANEL_ALT,
                        activeforeground=TEXT, relief="flat", bd=0,
                        font=("Segoe UI", 9), padx=6, pady=1,
                        cursor="hand2", highlightthickness=0)
        btn.pack(side="right")

        body = tk.Frame(card, bg=PANEL)
        body.pack(fill="both", expand=True)

        self._cards[key] = {"card": card, "header": header, "title": title,
                            "btn": btn, "body": body}
        return card, body

    # ------------------------------------------------------------- 折叠控制
    def _card_title_text(self, key):
        return tr.t(CARD_TITLE_KEYS[key])

    def _refresh_card_chrome(self, key):
        c = self._cards[key]
        try:
            c["title"].configure(text=self._card_title_text(key))
            c["btn"].configure(
                text=tr.t("btn_expand") if self._collapsed[key] else tr.t("btn_collapse"),
                fg=ACCENT2 if self._collapsed[key] else MUTED)
        except tk.TclError:
            pass

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
        self._apply_window_height()

    def _collapse_all(self):
        for key in CARD_KEYS:
            if not self._collapsed[key]:
                self._collapsed[key] = True
                self._apply_collapsed(key)
                self._refresh_card_chrome(key)
        self._save_ui_config()
        self._layout()
        self._apply_window_height()

    def _expand_all(self):
        for key in CARD_KEYS:
            if self._collapsed[key]:
                self._collapsed[key] = False
                self._apply_collapsed(key)
                self._refresh_card_chrome(key)
        self._save_ui_config()
        self._layout()
        self._apply_window_height()

    def _needed_height(self):
        """按当前折叠状态算出「刚好放下」的窗口高度。"""
        gap = 14
        self.root.update_idletasks()
        left = 0
        for key in ("info", "qr"):
            if self._collapsed[key]:
                left += COLLAPSED_H
            else:
                left += max(self._cards[key]["card"].winfo_reqheight(), 190)
        right = 0
        for key in ("pub", "recv"):
            if self._collapsed[key]:
                right += COLLAPSED_H
            else:
                right += max(self._cards[key]["card"].winfo_reqheight(), 190)
        content = max(left, right) + gap
        return 12 + 58 + gap + content + 20

    def _apply_window_height(self):
        """折叠/展开后让窗口高度跟随内容，避免留下大片空白。"""
        try:
            need = self._needed_height()
            sh = self.root.winfo_screenheight()
            target = int(max(220, min(need, sh - 80)))
            width = self.root.winfo_width()
            x = self.root.winfo_x()
            y = max(0, min(self.root.winfo_y(), sh - target - 40))
            if abs(self.root.winfo_height() - target) >= 2:
                self.root.geometry("%dx%d+%d+%d" % (width, target, x, y))
        except tk.TclError:
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
        avail = height - content_top - margin

        # 长语言（英/俄/西）文案更长，窄窗口下先隐藏副标题，给按钮留空间
        if self._tagline is not None:
            if width < 1020 and self._tagline_shown:
                self._tagline.pack_forget()
                self._tagline_shown = False
            elif width >= 1020 and not self._tagline_shown:
                self._tagline.pack(side="left", padx=(12, 0), pady=14)
                self._tagline_shown = True

        # 先确定宽度，再量高度（卡片里的换行文字依赖宽度）
        self.canvas.itemconfigure(self._header_win, width=width - margin * 2,
                                  height=header_h)
        self.canvas.coords(self._header_win, margin, top)

        self.canvas.itemconfigure(self._wins["info"], width=left_w)
        self.canvas.itemconfigure(self._wins["qr"], width=left_w)
        self.canvas.itemconfigure(self._wins["pub"], width=right_w)
        self.canvas.itemconfigure(self._wins["recv"], width=right_w)
        self.root.update_idletasks()

        req = {key: self._cards[key]["card"].winfo_reqheight() for key in CARD_KEYS}

        # 左列：服务信息优先按内容高度，二维码吃剩余；折叠后让另一张卡占满
        if self._collapsed["info"]:
            info_h = COLLAPSED_H
            qr_h = max(COLLAPSED_H, avail - gap - info_h)
        elif self._collapsed["qr"]:
            qr_h = COLLAPSED_H
            info_h = max(200, avail - gap - qr_h)
        else:
            info_h = min(max(req["info"], 200), max(200, avail - gap - 150))
            qr_h = max(140, avail - gap - info_h)

        # 右列：发布文件优先按内容高度，接收列表吃剩余
        if self._collapsed["pub"]:
            pub_h = COLLAPSED_H
            recv_h = max(COLLAPSED_H, avail - gap - pub_h)
        elif self._collapsed["recv"]:
            recv_h = COLLAPSED_H
            pub_h = max(220, avail - gap - recv_h)
        else:
            pub_h = min(max(req["pub"], 220), max(220, avail - gap - 150))
            recv_h = max(140, avail - gap - pub_h)

        self.canvas.itemconfigure(self._wins["info"], height=info_h)
        self.canvas.itemconfigure(self._wins["qr"], height=qr_h)
        self.canvas.itemconfigure(self._wins["pub"], height=pub_h)
        self.canvas.itemconfigure(self._wins["recv"], height=recv_h)

        # 窗口变矮时按可用高度缩小二维码模块，保证二维码与下方链接都不被裁掉
        if self._qr_url and self._qr_modules and not self._collapsed["qr"]:
            box = self._qr_box_for_height(qr_h)
            if box != self._qr_box:
                self._render_qr(box)

        self.canvas.coords(self._wins["info"], margin, content_top)
        self.canvas.coords(self._wins["qr"], margin, content_top + info_h + gap)
        self.canvas.coords(self._wins["pub"], margin + left_w + gap, content_top)
        self.canvas.coords(self._wins["recv"],
                           margin + left_w + gap, content_top + pub_h + gap)

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

    def _save_ui_config(self, language=None):
        """折叠状态 / 卡片不透明度 / 语言写入 settings.json。"""
        try:
            cfg = lan_transfer.load_settings()
            ui = cfg.get("ui")
            if not isinstance(ui, dict):
                ui = {}
            ui["collapsed"] = {key: bool(self._collapsed[key]) for key in CARD_KEYS}
            ui["card_opacity"] = int(self._card_opacity)
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

    def _apply_card_tints(self):
        """按「采样平均色 + 卡片不透明度」给每张卡片算出填充色与描边色。

        说明：Tk 子控件不支持真正的 alpha，这里是把背景平均色与卡片色按比例
        混合后作为固定填充色，视觉上模拟半透明，并非真正的透明通道。
        """
        if not self._card_rects:
            return
        alpha = self._card_opacity / 100.0
        style = ttk.Style()
        for key, rect in self._card_rects.items():
            avg = self._sample_avg(rect)
            fill = darken_clamp(blend_rgb(avg, PANEL_RGB, alpha))
            fill_alt = darken_clamp(blend_rgb(avg, PANEL_ALT_RGB, alpha), 104)
            line = darken_clamp(blend_rgb(avg, CARD_LINE_RGB, min(1.0, alpha + 0.2)), 130)
            btn_bg = darken_clamp(blend_rgb(avg, CARD_LINE_RGB, max(0.55, alpha)), 118)
            head_bg = darken_clamp(blend_rgb(avg, (35, 42, 68), alpha), 120)

            c = self._cards[key]
            for widget in (c["card"], c["header"], c["body"], c["title"]):
                try:
                    widget.configure(bg=fill)
                except tk.TclError:
                    pass
            try:
                c["card"].configure(highlightbackground=line, highlightcolor=line)
            except tk.TclError:
                pass

            fill_hex = "#%02X%02X%02X" % fill
            alt_hex = "#%02X%02X%02X" % fill_alt
            btn_hex = "#%02X%02X%02X" % btn_bg
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
        """递归把卡片内仍是旧基准色的控件换成采样色，按钮同步 hover 色。"""
        for child in parent.winfo_children():
            try:
                bg = str(child.cget("bg"))
            except tk.TclError:
                bg = ""
            if bg in mapping:
                try:
                    child.configure(bg=mapping[bg])
                except tk.TclError:
                    pass
            for option in ("activebackground", "selectcolor", "troughcolor"):
                try:
                    value = str(child.cget(option))
                except tk.TclError:
                    continue
                if value in mapping:
                    try:
                        child.configure(**{option: mapping[value]})
                    except tk.TclError:
                        pass

            if isinstance(child, tk.Button) and not getattr(child, "_primary_button", False):
                try:
                    old = str(child.cget("bg"))
                except tk.TclError:
                    old = ""
                target = mapping.get(old, old)
                if target and target.startswith("#"):
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
        tk.Scale(
            wrap, from_=CARD_OPACITY_MIN, to=CARD_OPACITY_MAX, orient="horizontal",
            variable=self._opacity_var, command=self._on_opacity_move, showvalue=False,
            bg=PANEL, fg=TEXT, troughcolor=PANEL_ALT, highlightthickness=0,
            sliderrelief="flat", activebackground=ACCENT, bd=0,
            length=420).pack(fill="x", padx=12, pady=(2, 2))
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
        if getattr(self, "opacity_label", None) is not None:
            self.opacity_label.config(text="%d%%" % self._card_opacity)
        self._save_ui_config()
        self._bg_drawn = None
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

        except Exception as e:
            messagebox.showerror(tr.t("dlg_error"), str(e))

    def _set_info(self, text):
        self.info_text.config(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.config(state="disabled")
        # 行数自适应（长路径会换行，留 3 行余量），避免空间/目录信息被截断
        lines = text.count("\n") + 1
        self.info_text.config(height=min(max(lines + 3, 6), 20))
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

    def _refresh_all(self):
        """同时刷新发布/接收两个列表。"""
        self._refresh_tree(self.pub_tree, lan_transfer.list_files(lan_transfer.PUBLISH_DIR))
        self._refresh_tree(self.recv_tree, lan_transfer.list_files(lan_transfer.RECEIVE_DIR))

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

    def _add_publish_files(self):
        """添加文件到发布目录（后台复制，大文件不卡界面）。"""
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
        self._copy_to_dir(list(paths), lan_transfer.PUBLISH_DIR, tr.t("card_pub"))

    def _add_publish_folder(self):
        """把整个文件夹里的所有文件平铺发布到发布目录（后台复制，不按后缀过滤）。"""
        src = filedialog.askdirectory(title=tr.t("choose_folder_title"))
        if not src:
            return

        folder_name = os.path.basename(src.rstrip(os.sep)) or src
        self._set_busy(True, tr.t("busy_reading", name=folder_name))

        def work():
            import shutil
            count, errors = 0, []
            # 递归遍历，任意后缀都发布；发布目录只支持单层文件，因此平铺
            for root, _dirs, files in os.walk(src):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        dest = os.path.join(
                            lan_transfer.PUBLISH_DIR,
                            lan_transfer.unique_name(name, lan_transfer.PUBLISH_DIR))
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
        """删除发布目录中选中的文件。"""
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
                os.remove(os.path.join(lan_transfer.PUBLISH_DIR, name))
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
        self._open_dir(lan_transfer.PUBLISH_DIR)

    def _open_receive_folder(self):
        self._open_dir(lan_transfer.RECEIVE_DIR)

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
