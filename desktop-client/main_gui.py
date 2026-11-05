#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网互传 - 桌面 GUI
"""

import os
import sys
import threading
import time
import webbrowser
import subprocess
import socket

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 导入后端
import lan_transfer


# --------------------------------------------------------------------------- #
# 二维码生成（qrcode + Pillow）
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class LanTransferApp:
    def __init__(self, root):
        self.root = root
        self.root.title("局域网互传 v%s" % lan_transfer.APP_VERSION)
        self.root.geometry("1080x780")
        self.root.minsize(1020, 740)

        self.server = None
        self._running = False
        self._busy = False
        self._share_url = ""
        self._qr_photo = None  # 保持引用防止被 GC

        self._build_ui()
        self._try_start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # 顶部标题栏
        header = tk.Frame(self.root, bg="#1a1f36", height=58)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        tk.Label(header, text="⇄", font=("Segoe UI", 22, "bold"),
                 fg="#5b8cff", bg="#1a1f36").pack(side="left", padx=(20, 8), pady=15)
        tk.Label(header, text="局域网互传", font=("Segoe UI", 16, "bold"),
                 fg="#eaf0ff", bg="#1a1f36").pack(side="left", pady=15)
        tk.Label(header, text="手机与电脑连同一个 Wi-Fi，扫码即可互传",
                 font=("Segoe UI", 9), fg="#6b7a99", bg="#1a1f36").pack(
            side="left", padx=(14, 0), pady=15)

        self.status_indicator = tk.Label(header, text="●  未启动", font=("Segoe UI", 11),
                                         fg="#ff6b6b", bg="#1a1f36")
        self.status_indicator.pack(side="right", padx=20, pady=15)
        tk.Label(header, text="v" + lan_transfer.APP_VERSION, font=("Segoe UI", 9),
                 fg="#6b7a99", bg="#1a1f36").pack(side="right", padx=(0, 6), pady=15)

        # 主体：左列（服务信息 + 二维码），右列（发布 / 接收列表）
        body = tk.Frame(self.root, bg="#0f1425")
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg="#0f1425", width=360)
        left.pack(side="left", fill="y", padx=(14, 7), pady=6)
        left.pack_propagate(False)

        right = tk.Frame(body, bg="#0f1425")
        right.pack(side="left", fill="both", expand=True, padx=(7, 14), pady=6)

        # ---------------- 左：服务信息 ----------------
        info_card = self._create_card(left, "服务信息")
        info_card.pack(fill="x", pady=(0, 10))

        self.info_text = tk.Text(info_card, height=6, bg="#1a1f36", fg="#c8d0e8",
                                 font=("Consolas", 9), relief="flat", wrap="word",
                                 borderwidth=0, padx=10, pady=6)
        self.info_text.pack(fill="x", padx=10, pady=(2, 8))
        self.info_text.insert("1.0", "等待启动...")
        self.info_text.config(state="disabled")

        info_btns = tk.Frame(info_card, bg="#1a1f36")
        info_btns.pack(fill="x", padx=10, pady=(0, 8))

        self.btn_toggle = self._make_btn(info_btns, "启动服务", self._toggle_server, primary=True)
        self.btn_toggle.pack(side="left", padx=(0, 6))
        self.btn_open_browser = self._make_btn(info_btns, "打开网页", self._open_browser)
        self.btn_open_browser.pack(side="left")

        info_btns2 = tk.Frame(info_card, bg="#1a1f36")
        info_btns2.pack(fill="x", padx=10, pady=(0, 6))
        self.btn_copy_url = self._make_btn(info_btns2, "复制链接", self._copy_url)
        self.btn_copy_url.pack(side="left", padx=(0, 6))
        self.btn_open_pub_top = self._make_btn(info_btns2, "打开文件夹",
                                              self._open_publish_folder)
        self.btn_open_pub_top.pack(side="left")

        # ---------------- 左：二维码 ----------------
        qr_card = self._create_card(left, "手机扫码连接")
        qr_card.pack(fill="both", expand=True)

        self.qr_container = tk.Frame(qr_card, bg="#1a1f36")
        self.qr_container.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        self.qr_label = tk.Label(self.qr_container, text="启动服务后显示二维码",
                                 bg="#1a1f36", fg="#6b7a99", font=("Segoe UI", 10))
        self.qr_label.pack(expand=True)

        self.url_label = tk.Label(qr_card, text="", bg="#1a1f36", fg="#31d6b0",
                                  font=("Consolas", 8), wraplength=340,
                                  justify="center")
        self.url_label.pack(fill="x", padx=10, pady=(0, 8))

        # ---------------- 右：发布文件 ----------------
        pub_card = self._create_card(right, "发布文件（电脑 → 手机）")
        pub_card.pack(fill="x", pady=(0, 10))

        tk.Label(pub_card, text="文件用「添加文件」，整目录用「添加文件夹」，两者都支持任意格式",
                 bg="#1a1f36", fg="#8b97b5", font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(2, 4))

        pub_wrap = tk.Frame(pub_card, bg="#1a1f36")
        pub_wrap.pack(fill="x", padx=10, pady=(0, 8))

        columns = ("name", "size")
        self.pub_tree = ttk.Treeview(pub_wrap, columns=columns, show="headings", height=6)
        self._setup_tree(self.pub_tree, columns)
        pub_scroll = ttk.Scrollbar(pub_wrap, orient="vertical", command=self.pub_tree.yview)
        self.pub_tree.configure(yscroll=pub_scroll.set)
        self.pub_tree.pack(side="left", fill="x", expand=True)
        pub_scroll.pack(side="right", fill="y")

        pub_btns = tk.Frame(pub_card, bg="#1a1f36")
        pub_btns.pack(fill="x", padx=10, pady=(0, 10))

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
        recv_card = self._create_card(right, "接收文件（手机 → 电脑）")
        recv_card.pack(fill="both", expand=True)

        recv_wrap = tk.Frame(recv_card, bg="#1a1f36")
        recv_wrap.pack(fill="both", expand=True, padx=10, pady=(2, 8))

        self.recv_tree = ttk.Treeview(recv_wrap, columns=columns, show="headings", height=6)
        self._setup_tree(self.recv_tree, columns)
        recv_scroll = ttk.Scrollbar(recv_wrap, orient="vertical", command=self.recv_tree.yview)
        self.recv_tree.configure(yscroll=recv_scroll.set)
        self.recv_tree.pack(side="left", fill="both", expand=True)
        recv_scroll.pack(side="right", fill="y")

        recv_btns = tk.Frame(recv_card, bg="#1a1f36")
        recv_btns.pack(fill="x", padx=10, pady=(0, 10))

        self.btn_refresh_recv = self._make_btn(recv_btns, "刷新", self._refresh_all)
        self.btn_refresh_recv.pack(side="left", padx=(0, 6))
        self.btn_open_recv = self._make_btn(recv_btns, "打开接收文件夹",
                                            self._open_receive_folder)
        self.btn_open_recv.pack(side="left")

        self.lbl_recv_hint = tk.Label(recv_btns, text="手机上传的文件自动存到这里",
                                       bg="#1a1f36", fg="#8b97b5", font=("Segoe UI", 9),
                                       wraplength=180, justify="right", anchor="e")
        self.lbl_recv_hint.pack(side="right", padx=(8, 0))

        # 定时刷新
        self.root.after(2000, self._auto_refresh)

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

        row_bg = "#1a1f36"
        zebra_bg = "#232c47"
        head_bg = "#232a44"
        head_active_bg = "#2a3354"
        select_bg = "#5b8cff"
        text_fg = "#ffffff"

        style.configure("Dark.Treeview",
                        background=row_bg, foreground=text_fg,
                        fieldbackground=row_bg, borderwidth=0,
                        rowheight=26, font=("Segoe UI", 10))
        style.configure("Dark.Treeview.Heading",
                        background=head_bg, foreground=text_fg,
                        relief="flat", borderwidth=0,
                        font=("Segoe UI", 10, "bold"))
        # 普通 / 选中 / 悬停 / 失焦都显式配色，避免主题默认的灰底浅字
        style.map("Dark.Treeview",
                  foreground=[("selected", text_fg), ("!selected", text_fg)],
                  # 非选中行交给 tag_configure 斑马纹，选中行用蓝色强调色
                  background=[("selected", select_bg)])
        style.map("Dark.Treeview.Heading",
                  foreground=[("active", text_fg), ("!active", text_fg)],
                  background=[("active", head_active_bg), ("!active", head_bg)])

        # 交替行斑马纹：仅换背景，文字始终白色
        tree.tag_configure("even", background=row_bg, foreground=text_fg)
        tree.tag_configure("odd", background=zebra_bg, foreground=text_fg)
        tree.configure(style="Dark.Treeview")

    def _create_card(self, parent, title):
        """创建一个卡片容器（标题 + 内容区）。"""
        card = tk.Frame(parent, bg="#1a1f36", highlightbackground="#2a3354",
                        highlightthickness=1)

        header = tk.Frame(card, bg="#1a1f36")
        header.pack(fill="x", padx=12, pady=(2, 0))
        tk.Label(header, text=title, bg="#1a1f36", fg="#eaf0ff",
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        return card

    def _make_btn(self, parent, text, command, primary=False):
        """创建按钮。"""
        if primary:
            return tk.Button(parent, text=text, command=command,
                             bg="#5b8cff", fg="white", activebackground="#4a6df0",
                             activeforeground="white", relief="flat", bd=0,
                             font=("Segoe UI", 10, "bold"), padx=14, pady=6,
                             cursor="hand2")
        return tk.Button(parent, text=text, command=command,
                         bg="#2a3354", fg="#c8d0e8", activebackground="#3a4570",
                         activeforeground="white", relief="flat", bd=0,
                         font=("Segoe UI", 10), padx=14, pady=6,
                         cursor="hand2")

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
        self.server = None
        self._running = False
        self._share_url = ""

        self.status_indicator.config(text="●  已停止", fg="#ff6b6b")
        self.btn_toggle.config(text="启动服务")
        self._set_info("服务已停止")
        self.url_label.config(text="")
        self._clear_qr()

    def _on_server_started(self):
        """服务启动后更新界面（在主线程中调用）。"""
        try:
            ip = lan_transfer.local_ip()
            port = lan_transfer.PORT
            token = lan_transfer.TOKEN
            tls = getattr(lan_transfer, "USE_TLS", False)
            self._share_url = lan_transfer.build_url(ip)

            self.status_indicator.config(text="●  运行中", fg="#31d6b0")
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
            self._set_info(info_text)

            self.url_label.config(text=self._share_url)
            self._show_qr(self._share_url)
            self._refresh_all()

        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _set_info(self, text):
        self.info_text.config(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.config(state="disabled")

    def _show_qr(self, url):
        """显示二维码图片。"""
        from PIL import ImageTk

        img = gen_qr_image(url, box_size=4, border=3)
        if img is None:
            self.qr_label.config(image="", text="缺少 qrcode / Pillow 库")
            return

        self._qr_photo = ImageTk.PhotoImage(img)

        for w in self.qr_container.winfo_children():
            w.destroy()

        self.qr_label = tk.Label(self.qr_container, image=self._qr_photo,
                                 bg="white", padx=4, pady=4)
        self.qr_label.pack(expand=True, pady=4)

    def _clear_qr(self):
        for w in self.qr_container.winfo_children():
            w.destroy()
        self.qr_label = tk.Label(self.qr_container, text="启动服务后显示二维码",
                                 bg="#1a1f36", fg="#6b7a99", font=("Segoe UI", 10))
        self.qr_label.pack(expand=True)
        self._qr_photo = None

    # --------------------------------------------------------------- 文件操作
    def _refresh_tree(self, tree, items):
        """清空并填充 Treeview。"""
        for item in tree.get_children():
            tree.delete(item)
        for idx, f in enumerate(items):
            tree.insert("", "end", tags=("odd" if idx % 2 else "even",),
                        values=(f["name"], lan_transfer.human_size(f["size"])))

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

    # 无界面常驻模式（供 CLI / 其他程序 / AI 调用）
    if "--headless" in sys.argv[1:]:
        sys.exit(lan_transfer.headless_main(sys.argv[1:]))

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
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_exception

    root = tk.Tk()
    root.configure(bg="#0f1425")

    try:
        icon_path = os.path.join(lan_transfer.get_resource_dir(), "icon.ico")
        if os.path.isfile(icon_path):
            root.iconbitmap(icon_path)
    except Exception:
        pass

    app = LanTransferApp(root)

    def on_close():
        app._stop_server()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
