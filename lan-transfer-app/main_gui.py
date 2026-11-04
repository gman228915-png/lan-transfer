#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网互传 - 桌面 GUI
"""

import os
import sys
import threading
import webbrowser
import subprocess
import socket

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 导入后端
import lan_transfer


# --------------------------------------------------------------------------- #
# 二维码生成（纯 Python，基于 qrcode 库）
# --------------------------------------------------------------------------- #
def gen_qr_image(url, box_size=6, border=2):
    """生成二维码 PIL Image 对象。"""
    try:
        import qrcode
        from PIL import ImageTk, Image
        
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                          box_size=box_size, border=border)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        return img
    except ImportError:
        return None


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class LanTransferApp:
    def __init__(self, root):
        self.root = root
        self.root.title("局域网互传")
        self.root.geometry("560x680")
        self.root.minsize(520, 620)
        
        self.server = None
        self._running = False
        self._qr_photo = None  # 保持引用防止被 GC
        
        self._build_ui()
        self._try_start()
    
    def _build_ui(self):
        # 顶部标题栏
        header = tk.Frame(self.root, bg="#1a1f36", height=70)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        
        tk.Label(header, text="⇄", font=("Segoe UI", 22, "bold"),
                 fg="#5b8cff", bg="#1a1f36").pack(side="left", padx=(20, 8), pady=15)
        tk.Label(header, text="局域网互传", font=("Segoe UI", 16, "bold"),
                 fg="#eaf0ff", bg="#1a1f36").pack(side="left", pady=15)
        
        self.status_indicator = tk.Label(header, text="●  未启动", font=("Segoe UI", 11),
                                          fg="#ff6b6b", bg="#1a1f36")
        self.status_indicator.pack(side="right", padx=20, pady=15)
        
        # 主体内容区
        body = tk.Frame(self.root, bg="#0f1425")
        body.pack(fill="both", expand=True)
        
        # 服务信息卡片
        info_frame = self._create_card(body, "服务信息")
        info_frame.pack(fill="x", padx=16, pady=(14, 8))
        
        self.info_text = tk.Text(info_frame, height=5, bg="#1a1f36", fg="#c8d0e8",
                                 font=("Consolas", 10), relief="flat", wrap="word",
                                 borderwidth=0, padx=12, pady=8)
        self.info_text.pack(fill="x", padx=12, pady=(0, 10))
        self.info_text.insert("1.0", "等待启动...")
        self.info_text.config(state="disabled")
        
        # 按钮行
        btn_frame = tk.Frame(info_frame, bg="#1a1f36")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        self.btn_toggle = self._make_btn(btn_frame, "启动服务", self._toggle_server, primary=True)
        self.btn_toggle.pack(side="left", padx=(0, 8))
        
        self.btn_open_browser = self._make_btn(btn_frame, "打开网页", self._open_browser)
        self.btn_open_browser.pack(side="left", padx=(0, 8))
        
        self.btn_open_folder = self._make_btn(btn_frame, "打开发布文件夹", self._open_publish_folder)
        self.btn_open_folder.pack(side="left")
        
        # 二维码卡片
        qr_frame = self._create_card(body, "手机扫码连接")
        qr_frame.pack(fill="x", padx=16, pady=8)
        
        self.qr_container = tk.Frame(qr_frame, bg="#1a1f36")
        self.qr_container.pack(padx=12, pady=12)
        
        self.qr_label = tk.Label(self.qr_container, text="启动服务后显示二维码",
                                 bg="#1a1f36", fg="#6b7a99", font=("Segoe UI", 10))
        self.qr_label.pack(pady=20)
        
        self.url_label = tk.Label(qr_frame, text="", bg="#1a1f36", fg="#31d6b0",
                                   font=("Consolas", 9), wraplength=480, justify="center")
        self.url_label.pack(pady=(0, 12))
        
        # 发布文件卡片（电脑 → 手机）
        pub_frame = self._create_card(body, "发布文件（电脑 → 手机）")
        pub_frame.pack(fill="x", padx=16, pady=(8, 8))
        
        pub_list_wrap = tk.Frame(pub_frame, bg="#1a1f36")
        pub_list_wrap.pack(fill="x", padx=12, pady=(4, 8))
        
        columns = ("name", "size")
        self.pub_tree = ttk.Treeview(pub_list_wrap, columns=columns, show="headings", height=4)
        self.pub_tree.heading("name", text="文件名")
        self.pub_tree.heading("size", text="大小")
        self.pub_tree.column("name", width=340, anchor="w")
        self.pub_tree.column("size", width=100, anchor="e")
        
        pub_scroll = ttk.Scrollbar(pub_list_wrap, orient="vertical", command=self.pub_tree.yview)
        self.pub_tree.configure(yscroll=pub_scroll.set)
        self.pub_tree.pack(side="left", fill="x", expand=True)
        pub_scroll.pack(side="right", fill="y")
        self._style_treeview(self.pub_tree)
        
        pub_btn_frame = tk.Frame(pub_frame, bg="#1a1f36")
        pub_btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        self.btn_add_pub = self._make_btn(pub_btn_frame, "📂 添加文件", self._add_publish_files, primary=True)
        self.btn_add_pub.pack(side="left", padx=(0, 6))
        
        self.btn_add_pub_dir = self._make_btn(pub_btn_frame, "📁 添加文件夹", self._add_publish_folder)
        self.btn_add_pub_dir.pack(side="left", padx=(0, 6))
        
        self.btn_open_pub = self._make_btn(pub_btn_frame, "打开发布文件夹", self._open_publish_folder)
        self.btn_open_pub.pack(side="left", padx=(0, 6))
        
        self.btn_delete_pub = self._make_btn(pub_btn_frame, "🗑 删除所选", self._delete_publish_files)
        self.btn_delete_pub.pack(side="right")
        
        # 接收文件卡片（手机 → 电脑）
        recv_frame = self._create_card(body, "接收文件（手机 → 电脑）")
        recv_frame.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        
        recv_list_wrap = tk.Frame(recv_frame, bg="#1a1f36")
        recv_list_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        
        self.recv_tree = ttk.Treeview(recv_list_wrap, columns=columns, show="headings", height=6)
        self.recv_tree.heading("name", text="文件名")
        self.recv_tree.heading("size", text="大小")
        self.recv_tree.column("name", width=340, anchor="w")
        self.recv_tree.column("size", width=100, anchor="e")
        
        recv_scroll = ttk.Scrollbar(recv_list_wrap, orient="vertical", command=self.recv_tree.yview)
        self.recv_tree.configure(yscroll=recv_scroll.set)
        self.recv_tree.pack(side="left", fill="both", expand=True)
        recv_scroll.pack(side="right", fill="y")
        self._style_treeview(self.recv_tree)
        
        recv_btn_frame = tk.Frame(recv_frame, bg="#1a1f36")
        recv_btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        self.btn_refresh_recv = self._make_btn(recv_btn_frame, "🔄 刷新", self._refresh_all)
        self.btn_refresh_recv.pack(side="left", padx=(0, 6))
        
        self.btn_open_recv = self._make_btn(recv_btn_frame, "📁 打开接收文件夹", self._open_receive_folder)
        self.btn_open_recv.pack(side="left")
        
        self.lbl_recv_hint = tk.Label(recv_btn_frame,
                                       text="← 手机上传的文件自动到这里",
                                       bg="#1a1f36", fg="#6b7a99",
                                       font=("Segoe UI", 9))
        self.lbl_recv_hint.pack(side="right")
        
        # 定时刷新
        self.root.after(2000, self._auto_refresh)
    
    def _style_treeview(self, tree=None):
        """配置 Treeview 深色样式。"""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except:
            pass
        style.configure("Dark.Treeview",
                        background="#1a1f36",
                        foreground="#c8d0e8",
                        fieldbackground="#1a1f36",
                        borderwidth=0,
                        rowheight=26,
                        font=("Segoe UI", 10))
        style.configure("Dark.Treeview.Heading",
                        background="#232a44",
                        foreground="#eaf0ff",
                        font=("Segoe UI", 10, "bold"),
                        borderwidth=0)
        style.map("Dark.Treeview", background=[("selected", "#5b8cff")])
        (tree or self.pub_tree).configure(style="Dark.Treeview")
    
    def _create_card(self, parent, title):
        """创建一个卡片容器。"""
        card = tk.Frame(parent, bg="#1a1f36", highlightbackground="#2a3354",
                        highlightthickness=1)
        card.pack_propagate(True)
        
        header = tk.Frame(card, bg="#1a1f36")
        header.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(header, text=title, bg="#1a1f36", fg="#eaf0ff",
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        
        return card
    
    def _make_btn(self, parent, text, command, primary=False):
        """创建按钮。"""
        if primary:
            btn = tk.Button(parent, text=text, command=command,
                           bg="#5b8cff", fg="white", activebackground="#4a6df0",
                           activeforeground="white", relief="flat", bd=0,
                           font=("Segoe UI", 10, "bold"), padx=16, pady=6,
                           cursor="hand2")
        else:
            btn = tk.Button(parent, text=text, command=command,
                           bg="#2a3354", fg="#c8d0e8", activebackground="#3a4570",
                           activeforeground="white", relief="flat", bd=0,
                           font=("Segoe UI", 10), padx=16, pady=6,
                           cursor="hand2")
        return btn
    
    # --------------------------------------------------------------------------- #
    # 服务控制
    # --------------------------------------------------------------------------- #
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
            
            # 生成 Token
            import secrets
            lan_transfer.TOKEN = "".join(
                secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
            
            # 启动服务
            self.server = lan_transfer.run(port=8765, open_browser=False, bind="0.0.0.0")
            self._running = True
            
            # 更新 UI
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
        
        # 更新 UI
        self.status_indicator.config(text="●  已停止", fg="#ff6b6b")
        self.btn_toggle.config(text="启动服务")
        self._set_info("服务已停止")
        self.url_label.config(text="")
        self._clear_qr()
    
    def _on_server_started(self):
        """服务启动后更新界面（在主线程中调用）。"""
        try:
            # 手动获取信息
            ip = lan_transfer.local_ip()
            port = lan_transfer.PORT
            token = lan_transfer.TOKEN
            tls = getattr(lan_transfer, "USE_TLS", False)
            scheme = "https" if tls else "http"
            share_url = "%s://%s:%d/?k=%s" % (scheme, ip, port, token)

            self.status_indicator.config(text="●  运行中", fg="#31d6b0")
            self.btn_toggle.config(text="停止服务")

            info_text = (
                f"主机名:  {socket.gethostname()}\n"
                f"本机 IP:  {ip}\n"
                f"端口:    {port}\n"
                f"访问口令: {token}    ← 手机端需输入此口令\n"
                f"加密传输: {'HTTPS 已启用' if tls else '未启用（HTTP 明文）'}\n"
                f"发布目录: {lan_transfer.PUBLISH_DIR}\n"
                f"接收目录: {lan_transfer.RECEIVE_DIR}"
            )
            if tls:
                fp = lan_transfer.cert_fingerprint()
                info_text += f"\n证书指纹: {fp[:29]}…" if len(fp) > 29 else f"\n证书指纹: {fp}"
            self._set_info(info_text)

            # 显示 URL
            self.url_label.config(text=share_url)
            
            # 生成二维码
            self._show_qr(share_url)
            
            # 刷新文件列表
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
        
        img = gen_qr_image(url, box_size=7, border=3)
        if img is None:
            self.qr_label.config(image="", text="请安装 qrcode 和 Pillow 库")
            return
        
        self._qr_photo = ImageTk.PhotoImage(img)
        
        # 清空容器
        for w in self.qr_container.winfo_children():
            w.destroy()
        
        self.qr_label = tk.Label(self.qr_container, image=self._qr_photo,
                                 bg="white", padx=6, pady=6)
        self.qr_label.pack(padx=10, pady=10)
    
    def _clear_qr(self):
        for w in self.qr_container.winfo_children():
            w.destroy()
        self.qr_label = tk.Label(self.qr_container, text="启动服务后显示二维码",
                                 bg="#1a1f36", fg="#6b7a99", font=("Segoe UI", 10))
        self.qr_label.pack(pady=20)
        self._qr_photo = None
    
    # --------------------------------------------------------------------------- #
    # 文件操作
    # --------------------------------------------------------------------------- #
    def _refresh_tree(self, tree, items):
        """清空并填充 Treeview。"""
        for item in tree.get_children():
            tree.delete(item)
        for f in items:
            tree.insert("", "end", values=(f["name"], lan_transfer.human_size(f["size"])))

    def _refresh_all(self):
        """同时刷新发布/接收两个列表。"""
        self._refresh_tree(self.pub_tree, lan_transfer.list_files(lan_transfer.PUBLISH_DIR))
        self._refresh_tree(self.recv_tree, lan_transfer.list_files(lan_transfer.RECEIVE_DIR))

    def _auto_refresh(self):
        """定时刷新（接收目录可能有手机上传的新文件）。"""
        if self._running:
            self._refresh_all()
        self.root.after(3000, self._auto_refresh)

    def _add_publish_files(self):
        """添加文件到发布目录。"""
        paths = filedialog.askopenfilenames(title="选择要发布的文件")
        if not paths:
            return
        self._copy_to_dir(paths, lan_transfer.PUBLISH_DIR, "发布文件")

    def _add_publish_folder(self):
        """把整个文件夹拷进发布目录。"""
        src = filedialog.askdirectory(title="选择要发布的文件夹")
        if not src:
            return
        import shutil
        parent, folder_name = os.path.split(src)
        if not folder_name:
            folder_name = "未命名文件夹"
        dest_root = os.path.join(lan_transfer.PUBLISH_DIR, lan_transfer.unique_name(folder_name))
        try:
            shutil.copytree(src, dest_root)
            messagebox.showinfo("完成", f"已发布文件夹：{folder_name}\n\n注意：手机端会看到文件夹下的每个文件单独列出。")
        except Exception as e:
            messagebox.showerror("复制失败", str(e))
        self._refresh_all()

    def _copy_to_dir(self, paths, target_dir, label):
        import shutil
        count, errors = 0, []
        for p in paths:
            try:
                name = os.path.basename(p)
                dest = os.path.join(target_dir, lan_transfer.unique_name(name, target_dir))
                shutil.copy2(p, dest)
                count += 1
            except Exception as e:
                errors.append(f"{p}: {e}")
        self._refresh_all()
        if errors:
            messagebox.showwarning("部分失败", "\n".join(errors[:10]))
        if count:
            messagebox.showinfo("完成", f"已添加 {count} 个文件到【{label}】")

    def _delete_publish_files(self):
        """删除发布目录中选中的文件。"""
        selected = self.pub_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先在发布文件列表中选中要删除的文件")
            return
        if not messagebox.askyesno("确认删除", f"确定删除选中的 {len(selected)} 个发布文件吗？"):
            return
        count = 0
        for iid in selected:
            values = self.pub_tree.item(iid, "values")
            name = values[0]
            try:
                os.remove(os.path.join(lan_transfer.PUBLISH_DIR, name))
                count += 1
            except OSError as e:
                messagebox.showwarning("删除失败", f"{name}: {e}")
        self._refresh_all()
        if count:
            messagebox.showinfo("完成", f"已删除 {count} 个文件")
    
    # --------------------------------------------------------------------------- #
    # 其他操作
    # --------------------------------------------------------------------------- #
    def _open_browser(self):
        if not self._running:
            messagebox.showwarning("提示", "请先启动服务")
            return
        try:
            port = lan_transfer.PORT
            token = lan_transfer.TOKEN
            scheme = "https" if getattr(lan_transfer, "USE_TLS", False) else "http"
            url = f"{scheme}://127.0.0.1:{port}/?k={token}"
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("错误", str(e))
    
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
    # 全局异常处理 - 写入日志
    log_dir = lan_transfer.BASE_DIR
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        try:
            err_file = os.path.join(log_dir, "error.log")
            with open(err_file, "a", encoding="utf-8") as f:
                f.write("=" * 50 + "\n")
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
                f.write("\n")
        except Exception:
            pass
        # 也让默认处理器工作
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    
    sys.excepthook = handle_exception
    
    root = tk.Tk()
    root.configure(bg="#0f1425")
    
    # 设置窗口图标（如果存在）
    try:
        icon_path = os.path.join(lan_transfer.get_resource_dir(), "icon.ico")
        if os.path.isfile(icon_path):
            root.iconbitmap(icon_path)
    except Exception:
        pass
    
    app = LanTransferApp(root)
    
    # 关闭时停止服务
    def on_close():
        app._stop_server()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    import time
    main()
