#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网互传 - 桌面 GUI

v1.6 起界面支持自定义背景（本地图片 + 显示方式 + 遮罩浓度），
背景与外观参数保存在 settings.json 的 background 字段里。
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

# 导入后端
import lan_transfer

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

BG_MODES = ("fill", "fit", "tile", "center")
BG_MODE_LABELS = {"fill": "铺满", "fit": "适应", "tile": "平铺", "center": "居中"}
BG_DEFAULT_DIM = 45


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
        self.root.title("文件传输 · 局域网互传 v%s" % lan_transfer.APP_VERSION)
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
        self._bg_drawn = None      # 上次绘制的 (尺寸, 配置)
        self._bg_job = None
        self._layout_job = None
        self._bg_dialog = None
        self._dirty = True

        # 读取上次保存的「局域网可见」设置（默认开启）
        try:
            lan_transfer.DISCOVERY_ENABLED = bool(
                lan_transfer.load_settings().get("discovery", True))
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
        self.root.minsize(940, 620)

    # ------------------------------------------------------------------ 图标
    def _apply_icons(self):
        """标题栏 / 任务栏 / 窗口图标统一使用新品牌图标。"""
        ico = os.path.join(lan_transfer.get_resource_dir(), "icon.ico")
        if not os.path.isfile(ico):
            ico = os.path.join(lan_transfer.BASE_DIR, "icon.ico")

        # 大图标（任务栏 / Alt+Tab）用 256px PNG，小图标（标题栏）用 ICO 里的 16px
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

        tk.Label(header, text="局域网互传", font=("Segoe UI", 16, "bold"),
                 fg=TEXT, bg=BG_HEADER).pack(side="left", pady=14)
        tk.Label(header, text="手机与电脑连同一个 Wi-Fi，扫码即可互传",
                 font=("Segoe UI", 9), fg=MUTED, bg=BG_HEADER).pack(
            side="left", padx=(12, 0), pady=14)

        head_right = tk.Frame(header, bg=BG_HEADER)
        head_right.pack(side="right", padx=14, pady=11)

        self.btn_bg = self._make_btn(head_right, "更换背景", self._open_bg_dialog,
                                     small=True)
        self.btn_bg.pack(side="right", padx=(12, 0))
        tk.Label(head_right, text="v" + lan_transfer.APP_VERSION, font=("Segoe UI", 9),
                 fg=MUTED, bg=BG_HEADER).pack(side="right", padx=(0, 4))
        self.status_indicator = tk.Label(head_right, text="●  未启动",
                                         font=("Segoe UI", 11), fg=DANGER, bg=BG_HEADER)
        self.status_indicator.pack(side="right", padx=(0, 10))

        # ---------------- 四张卡片（直接浮在画布上，靠 _layout 定位） ----------------
        self.info_card = self._create_card(self.canvas, "服务信息")
        self.qr_card = self._create_card(self.canvas, "手机扫码连接")
        self.pub_card = self._create_card(self.canvas, "发布文件（电脑 → 手机）")
        self.recv_card = self._create_card(self.canvas, "接收文件（手机 → 电脑）")
        self._wins = {
            "info": self.canvas.create_window(0, 0, window=self.info_card, anchor="nw"),
            "qr": self.canvas.create_window(0, 0, window=self.qr_card, anchor="nw"),
            "pub": self.canvas.create_window(0, 0, window=self.pub_card, anchor="nw"),
            "recv": self.canvas.create_window(0, 0, window=self.recv_card, anchor="nw"),
        }

        # ---------------- 左：服务信息 ----------------
        self.info_text = tk.Text(self.info_card, height=6, bg=PANEL_ALT, fg=TEXT_SOFT,
                                 font=("Consolas", 9), relief="flat", wrap="word",
                                 borderwidth=0, padx=10, pady=7, highlightthickness=0,
                                 insertbackground=TEXT)
        self.info_text.pack(fill="x", padx=12, pady=(4, 8))
        self.info_text.insert("1.0", "等待启动...")
        self.info_text.config(state="disabled")

        info_btns = tk.Frame(self.info_card, bg=PANEL)
        info_btns.pack(fill="x", padx=12, pady=(0, 8))

        self.btn_toggle = self._make_btn(info_btns, "启动服务", self._toggle_server, primary=True)
        self.btn_toggle.pack(side="left", padx=(0, 6))
        self.btn_open_browser = self._make_btn(info_btns, "打开网页", self._open_browser)
        self.btn_open_browser.pack(side="left")

        info_btns2 = tk.Frame(self.info_card, bg=PANEL)
        info_btns2.pack(fill="x", padx=12, pady=(0, 8))
        self.btn_copy_url = self._make_btn(info_btns2, "复制链接", self._copy_url)
        self.btn_copy_url.pack(side="left", padx=(0, 6))
        self.btn_open_pub_top = self._make_btn(info_btns2, "打开文件夹",
                                              self._open_publish_folder)
        self.btn_open_pub_top.pack(side="left")

        disc_row = tk.Frame(self.info_card, bg=PANEL)
        disc_row.pack(fill="x", padx=12, pady=(0, 10))
        self.discovery_var = tk.BooleanVar(value=lan_transfer.DISCOVERY_ENABLED)
        self.chk_discovery = tk.Checkbutton(
            disc_row, text="在局域网内可见（UDP 广播发现）", variable=self.discovery_var,
            command=self._toggle_discovery, bg=PANEL, fg=TEXT_SOFT,
            selectcolor=PANEL_ALT, activebackground=PANEL,
            activeforeground="#ffffff", font=("Segoe UI", 9),
            borderwidth=0, highlightthickness=0)
        self.chk_discovery.pack(side="left")
        self.lbl_discovery = tk.Label(disc_row, text="", bg=PANEL,
                                      fg=MUTED, font=("Segoe UI", 8))
        self.lbl_discovery.pack(side="right")

        # ---------------- 左：二维码 ----------------
        self.qr_container = tk.Frame(self.qr_card, bg=PANEL)
        self.qr_container.pack(fill="both", expand=True, padx=12, pady=(2, 2))

        self.qr_label = tk.Label(self.qr_container, text="启动服务后显示二维码",
                                 bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        self.qr_label.pack(expand=True)

        self.url_label = tk.Label(self.qr_card, text="", bg=PANEL, fg=ACCENT2,
                                  font=("Consolas", 8), wraplength=340,
                                  justify="center")
        self.url_label.pack(fill="x", padx=12, pady=(0, 8))

        # ---------------- 右：发布文件 ----------------
        tk.Label(self.pub_card, text="文件用「添加文件」，整目录用「添加文件夹」，两者都支持任意格式",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(2, 4))

        pub_wrap = tk.Frame(self.pub_card, bg=PANEL)
        pub_wrap.pack(fill="x", padx=12, pady=(0, 8))

        columns = ("name", "size")
        self.pub_tree = ttk.Treeview(pub_wrap, columns=columns, show="headings", height=6)
        self._setup_tree(self.pub_tree, columns)
        pub_scroll = ttk.Scrollbar(pub_wrap, orient="vertical",
                                   style="Dark.Vertical.TScrollbar",
                                   command=self.pub_tree.yview)
        self.pub_tree.configure(yscroll=pub_scroll.set)
        self.pub_tree.pack(side="left", fill="x", expand=True)
        pub_scroll.pack(side="right", fill="y")

        pub_btns = tk.Frame(self.pub_card, bg=PANEL)
        pub_btns.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_add_pub = self._make_btn(pub_btns, "添加文件", self._add_publish_files, primary=True)
        self.btn_add_pub.pack(side="left", padx=(0, 6))
        self.btn_add_pub_dir = self._make_btn(pub_btns, "添加文件夹", self._add_publish_folder)
        self.btn_add_pub_dir.pack(side="left", padx=(0, 6))
        self.btn_delete_pub = self._make_btn(pub_btns, "删除所选", self._delete_publish_files)
        self.btn_delete_pub.pack(side="left", padx=(0, 6))
        self.btn_open_pub = self._make_btn(pub_btns, "打开文件夹",
                                           self._open_publish_folder)
        self.btn_open_pub.pack(side="right")

        # ---------------- 右：接收文件 ----------------
        recv_wrap = tk.Frame(self.recv_card, bg=PANEL)
        recv_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        self.recv_tree = ttk.Treeview(recv_wrap, columns=columns, show="headings", height=6)
        self._setup_tree(self.recv_tree, columns)
        recv_scroll = ttk.Scrollbar(recv_wrap, orient="vertical",
                                    style="Dark.Vertical.TScrollbar",
                                    command=self.recv_tree.yview)
        self.recv_tree.configure(yscroll=recv_scroll.set)
        self.recv_tree.pack(side="left", fill="both", expand=True)
        recv_scroll.pack(side="right", fill="y")

        recv_btns = tk.Frame(self.recv_card, bg=PANEL)
        recv_btns.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_refresh_recv = self._make_btn(recv_btns, "刷新", self._refresh_all)
        self.btn_refresh_recv.pack(side="left", padx=(0, 6))
        self.btn_open_recv = self._make_btn(recv_btns, "打开接收文件夹",
                                            self._open_receive_folder)
        self.btn_open_recv.pack(side="left")

        self.lbl_recv_hint = tk.Label(recv_btns, text="手机上传的文件自动存到这里",
                                       bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                                       wraplength=180, justify="right", anchor="e")
        self.lbl_recv_hint.pack(side="right", padx=(8, 0))

        # 布局 & 背景
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.after(80, self._layout)
        self.root.after(80, self._redraw_bg)

        # 定时刷新
        self.root.after(2000, self._auto_refresh)

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

        # 先确定宽度，再量高度（卡片里的换行文字依赖宽度）
        self.canvas.itemconfigure(self._header_win, width=width - margin * 2,
                                  height=header_h)
        self.canvas.coords(self._header_win, margin, top)

        self.canvas.itemconfigure(self._wins["info"], width=left_w)
        self.canvas.itemconfigure(self._wins["qr"], width=left_w)
        self.canvas.itemconfigure(self._wins["pub"], width=right_w)
        self.canvas.itemconfigure(self._wins["recv"], width=right_w)
        self.root.update_idletasks()

        info_req = self.info_card.winfo_reqheight()
        pub_req = self.pub_card.winfo_reqheight()

        # 信息卡/发布卡按内容高度优先，剩下的高度给二维码和接收列表
        info_h = min(max(info_req, 200), max(200, avail - gap - 150))
        qr_h = max(140, avail - gap - info_h)
        pub_h = min(max(pub_req, 220), max(220, avail - gap - 150))
        recv_h = max(140, avail - gap - pub_h)

        self.canvas.itemconfigure(self._wins["info"], height=info_h)
        self.canvas.itemconfigure(self._wins["qr"], height=qr_h)
        self.canvas.itemconfigure(self._wins["pub"], height=pub_h)
        self.canvas.itemconfigure(self._wins["recv"], height=recv_h)

        # 窗口变矮时按可用高度缩小二维码模块，保证二维码与下方链接都不被裁掉
        if self._qr_url and self._qr_modules:
            box = self._qr_box_for_height(qr_h)
            if box != self._qr_box:
                self._render_qr(box)

        self.canvas.coords(self._wins["info"], margin, content_top)
        self.canvas.coords(self._wins["qr"], margin, content_top + info_h + gap)
        self.canvas.coords(self._wins["pub"], margin + left_w + gap, content_top)
        self.canvas.coords(self._wins["recv"],
                           margin + left_w + gap, content_top + pub_h + gap)

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
                     self._bg_src_key)
        if signature == self._bg_drawn and not self._dirty:
            return

        # 文件可能刚被换过：先取一次缓存键
        self._load_bg_source()
        signature = (width, height, self._bg_path, self._bg_mode, int(self._bg_dim),
                     self._bg_src_key)
        try:
            composed = self._compose_bg(width, height)
            if composed is None:
                self.canvas.itemconfigure(self._bg_item, image="")
                self._bg_photo = None
            else:
                from PIL import ImageTk
                self._bg_photo = ImageTk.PhotoImage(composed)
                self.canvas.itemconfigure(self._bg_item, image=self._bg_photo)
        except Exception:
            self.canvas.itemconfigure(self._bg_item, image="")
            self._bg_photo = None
        self._bg_drawn = signature
        self._dirty = False

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
        win.title("外观 · 背景设置")
        win.configure(bg=BG_BASE)
        win.resizable(False, False)
        win.transient(self.root)

        wrap = tk.Frame(win, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        tk.Label(wrap, text="自定义背景", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(wrap, text="支持 JPG / PNG / WebP / BMP，图片保存在本机，不会上传。",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12)

        self.bg_file_label = tk.Label(
            wrap, text=self._bg_summary(), bg=PANEL, fg=TEXT_SOFT,
            font=("Segoe UI", 9), wraplength=380, justify="left")
        self.bg_file_label.pack(anchor="w", padx=12, pady=(10, 8))

        row = tk.Frame(wrap, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_pick_bg = self._make_btn(row, "选择图片…", self._choose_bg, primary=True)
        self.btn_pick_bg.pack(side="left", padx=(0, 6))
        self.btn_reset_bg = self._make_btn(row, "恢复默认背景", self._reset_bg)
        self.btn_reset_bg.pack(side="left")

        tk.Label(wrap, text="显示方式", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(4, 2))
        mode_row = tk.Frame(wrap, bg=PANEL)
        mode_row.pack(fill="x", padx=8, pady=(0, 8))
        self._bg_mode_var = tk.StringVar(value=self._bg_mode)
        for mode in BG_MODES:
            tk.Radiobutton(
                mode_row, text=BG_MODE_LABELS[mode], variable=self._bg_mode_var,
                value=mode, command=self._on_mode_change,
                bg=PANEL, fg=TEXT_SOFT, activebackground=PANEL, activeforeground=TEXT,
                selectcolor=PANEL_ALT, highlightthickness=0,
                font=("Segoe UI", 9), borderwidth=0, padx=2).pack(
                side="left", padx=(4, 10))

        dim_head = tk.Frame(wrap, bg=PANEL)
        dim_head.pack(fill="x", padx=12, pady=(4, 0))
        tk.Label(dim_head, text="背景遮罩浓度", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.bg_dim_label = tk.Label(dim_head, text="%d%%" % self._bg_dim,
                                     bg=PANEL, fg=ACCENT2, font=("Segoe UI", 9))
        self.bg_dim_label.pack(side="right")

        self._bg_dim_var = tk.IntVar(value=int(self._bg_dim))
        scale = tk.Scale(
            wrap, from_=0, to=100, orient="horizontal", variable=self._bg_dim_var,
            command=self._on_dim_move, showvalue=False,
            bg=PANEL, fg=TEXT, troughcolor=PANEL_ALT, highlightthickness=0,
            sliderrelief="flat", activebackground=ACCENT, bd=0,
            length=380)
        scale.pack(fill="x", padx=12, pady=(2, 2))
        tk.Label(wrap, text="浓度越高，背景越暗、文字越清楚；0% 为原图。",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=12)

        close_row = tk.Frame(wrap, bg=PANEL)
        close_row.pack(fill="x", padx=12, pady=(12, 10))
        tk.Label(close_row, text="设置会写入 settings.json，重启后仍然生效。",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side="left")
        self._make_btn(close_row, "关闭", win.destroy).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        win.update_idletasks()
        px = self.root.winfo_rootx() + max(20, (self.root.winfo_width() - win.winfo_width()) // 2)
        py = self.root.winfo_rooty() + max(20, (self.root.winfo_height() - win.winfo_height()) // 3)
        win.geometry("+%d+%d" % (px, py))

    def _bg_summary(self):
        if self._bg_path and os.path.isfile(self._bg_path):
            return "当前背景：%s" % os.path.basename(self._bg_path)
        return "当前背景：默认（无背景图）"

    def _choose_bg(self):
        path = filedialog.askopenfilename(
            title="选择背景图片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.webp *.bmp"),
                       ("所有文件", "*.*")])
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

    def _apply_bg_change(self):
        if getattr(self, "bg_file_label", None) is not None:
            try:
                self.bg_file_label.config(text=self._bg_summary())
            except tk.TclError:
                pass
        self._bg_src_key = None       # 换图后强制重新读盘
        self._save_bg_config()
        self._schedule_bg()

    def _setup_tree(self, tree, columns):
        """统一两个文件列表的列宽和深色样式（白字 + 斑马纹 + 蓝色选中）。"""
        tree.heading("name", text="文件名")
        tree.heading("size", text="大小")
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

        style.configure("Dark.Treeview",
                        background=row_bg, foreground=text_fg,
                        fieldbackground=row_bg, borderwidth=0,
                        rowheight=26, font=("Segoe UI", 10))
        style.configure("Dark.Treeview.Heading",
                        background=head_bg, foreground=text_fg,
                        relief="flat", borderwidth=0,
                        font=("Segoe UI", 10, "bold"))
        style.map("Dark.Treeview",
                  foreground=[("selected", text_fg), ("!selected", text_fg)],
                  background=[("selected", select_bg)])
        style.map("Dark.Treeview.Heading",
                  foreground=[("active", text_fg), ("!active", text_fg)],
                  background=[("active", head_active_bg), ("!active", head_bg)])

        # 滚动条也用深色，避免白色系统滚动条破坏整体观感
        style.configure("Dark.Vertical.TScrollbar",
                        background=PANEL_ALT, troughcolor=PANEL,
                        bordercolor=PANEL, arrowcolor=MUTED,
                        darkcolor=PANEL_ALT, lightcolor=PANEL_ALT,
                        relief="flat", borderwidth=0)
        style.map("Dark.Vertical.TScrollbar",
                  background=[("active", "#34406B"), ("pressed", ACCENT)],
                  arrowcolor=[("active", TEXT_SOFT)])

        tree.tag_configure("even", background=row_bg, foreground=text_fg)
        tree.tag_configure("odd", background=zebra_bg, foreground=text_fg)
        tree.configure(style="Dark.Treeview")

    def _create_card(self, parent, title):
        """卡片容器：统一内边距、标题字号与描边。"""
        card = tk.Frame(parent, bg=PANEL, highlightbackground=CARD_LINE,
                        highlightthickness=1)
        header = tk.Frame(card, bg=PANEL)
        header.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(header, text=title, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        return card

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

        def on_enter(_event):
            if str(btn.cget("state")) == "normal":
                btn.configure(bg=hover)

        def on_leave(_event):
            if str(btn.cget("state")) == "normal":
                btn.configure(bg=normal)

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
            messagebox.showerror("启动失败", str(e))
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

        self.status_indicator.config(text="●  已停止", fg=DANGER)
        self.btn_toggle.config(text="启动服务")
        self._set_info("服务已停止")
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
            self.lbl_discovery.config(text="服务未启动", fg=MUTED)
        elif lan_transfer.DISCOVERY_ENABLED:
            self.lbl_discovery.config(text="已广播", fg=ACCENT2)
        else:
            self.lbl_discovery.config(text="已关闭", fg=MUTED)

    def _on_server_started(self):
        """服务启动后更新界面（在主线程中调用）。"""
        try:
            ip = lan_transfer.local_ip()
            port = lan_transfer.PORT
            token = lan_transfer.TOKEN
            tls = getattr(lan_transfer, "USE_TLS", False)
            self._share_url = lan_transfer.build_url(ip)

            self.status_indicator.config(text="●  运行中", fg=ACCENT2)
            self.btn_toggle.config(text="停止服务")

            bind_line = ("仅监听局域网 IP：%s" % lan_transfer.BIND_ADDR
                         if lan_transfer.BIND_LAN_ONLY else
                         "监听所有网卡（局域网 IP：%s）" % ip)
            if lan_transfer.PORT_IS_FALLBACK:
                bind_line += ("\n端口提示：默认端口 %d 被占用，已自动改用 %d"
                              % (lan_transfer.PORT_REQUESTED, port))

            info_text = (
                "主机名：  %s\n"
                "局域网 IP：%s\n"
                "端口：    %d\n"
                "访问口令：%s  ← 手机端需输入此口令\n"
                "加密：    %s\n"
                "%s\n"
                "发布目录：%s\n"
                "接收目录：%s"
            ) % (
                socket.gethostname(), ip, port, token,
                "HTTPS 已启用" if tls else "未启用（HTTP 明文）",
                bind_line,
                lan_transfer.PUBLISH_DIR,
                lan_transfer.RECEIVE_DIR,
            )
            if lan_transfer.SPACES:
                space_lines = ["空间（%s）：" % lan_transfer.SPACES_FILE]
                for name, space in lan_transfer.SPACES.items():
                    space_lines.append("  %s [%s] %s" % (
                        name, space["mode"], space["path"]))
                info_text += "\n" + "\n".join(space_lines)
            info_text += "\n局域网发现：%s（UDP %d）" % (
                "已广播，局域网内可发现" if lan_transfer.DISCOVERY_ENABLED else "已关闭",
                lan_transfer.DISCOVERY_PORT)
            self._set_info(info_text)

            self.url_label.config(text=self._share_url)
            self._show_qr(self._share_url)
            self._refresh_all()
            self._update_discovery_label()

        except Exception as e:
            messagebox.showerror("错误", str(e))

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
            self.qr_label.config(image="", text="缺少 qrcode / Pillow 库")
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
        self.qr_label = tk.Label(self.qr_container, text="启动服务后显示二维码",
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
                        values=(f["name"], lan_transfer.human_size(f["size"])),)

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
        self.lbl_recv_hint.config(text=text or "手机上传的文件自动到这里")

    def _add_publish_files(self):
        """添加文件到发布目录（后台复制，大文件不卡界面）。"""
        paths = filedialog.askopenfilenames(
            title="选择要发布的文件（可多选）",
            # 第一项即默认筛选项：「所有文件」，不按后缀过滤；其余仅为快捷筛选
            filetypes=[
                ("所有文件", "*.*"),
                ("常用文档", "*.txt *.md *.pdf *.doc *.docx *.ppt *.pptx "
                            "*.xls *.xlsx *.csv *.json"),
                ("图片", "*.jpg *.jpeg *.png *.gif *.webp *.bmp *.heic"),
                ("音视频", "*.mp3 *.wav *.m4a *.mp4 *.mov *.mkv *.avi *.webm"),
                ("压缩包", "*.zip *.rar *.7z *.tar *.gz"),
            ])
        if not paths:
            return
        self._copy_to_dir(list(paths), lan_transfer.PUBLISH_DIR, "发布文件")

    def _add_publish_folder(self):
        """把整个文件夹里的所有文件平铺发布到发布目录（后台复制，不按后缀过滤）。"""
        src = filedialog.askdirectory(title="选择要发布的文件夹（整个目录一起发布）")
        if not src:
            return

        folder_name = os.path.basename(src.rstrip(os.sep)) or src
        self._set_busy(True, "正在读取文件夹：%s …" % folder_name)

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
            messagebox.showwarning("部分失败", "\n".join(errors[:10]))
        if count:
            messagebox.showinfo(
                "完成",
                "已发布 %d 个文件\n\n来源文件夹：%s\n"
                "目录内所有格式的文件都已加入发布列表，手机端可直接下载。"
                % (count, folder_name))
        elif not errors:
            messagebox.showinfo("提示", "文件夹「%s」里没有文件。" % folder_name)

    def _copy_to_dir(self, paths, target_dir, label):
        """后台逐个复制文件，完成后回主线程刷新。"""
        self._set_busy(True, "正在复制 %d 个文件…" % len(paths))

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
            messagebox.showwarning("部分失败", "\n".join(errors[:10]))
        if count:
            messagebox.showinfo("完成", "已添加 %d 个文件到【%s】" % (count, label))

    def _delete_publish_files(self):
        """删除发布目录中选中的文件。"""
        selected = self.pub_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先在发布文件列表中选中要删除的文件")
            return
        if not messagebox.askyesno("确认删除", "确定删除选中的 %d 个发布文件吗？" % len(selected)):
            return
        count = 0
        for iid in selected:
            values = self.pub_tree.item(iid, "values")
            name = values[0]
            try:
                os.remove(os.path.join(lan_transfer.PUBLISH_DIR, name))
                count += 1
            except OSError as e:
                messagebox.showwarning("删除失败", "%s: %s" % (name, e))
        self._refresh_all()
        if count:
            messagebox.showinfo("完成", "已删除 %d 个文件" % count)

    # ----------------------------------------------------------------- 其他
    def _open_browser(self):
        if not self._running:
            messagebox.showwarning("提示", "请先启动服务")
            return
        try:
            webbrowser.open(lan_transfer.build_url())
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _copy_url(self):
        if not self._running or not self._share_url:
            messagebox.showwarning("提示", "请先启动服务")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._share_url)
        self.root.update()
        messagebox.showinfo("已复制", "访问链接已复制到剪贴板：\n%s" % self._share_url)

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
            messagebox.showerror("错误", str(e))


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
