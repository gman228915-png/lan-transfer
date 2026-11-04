# 局域网互传工具

一款简单的局域网文件互传工具，支持电脑端与安卓端双向传输文件。

## 功能特性

- **扫码连接**：手机端扫描电脑端二维码即可建立连接
- **双向传输**：电脑 → 手机、手机 → 电脑
- **文件管理**：手机端支持下载、打开、分享、移动、删除文件
- **安全加密**：mTLS 双向认证，数据传输加密
- **免权限**：无需申请存储权限，使用应用专属目录

## 项目结构

```
.
├── android-client/       # 安卓端源码（Kotlin）
│   ├── app/src/main/     # 应用主代码
│   └── build_apk.ps1     # 一键构建脚本
├── desktop-client/       # 电脑端源码（Python）
│   ├── main_gui.py       # GUI 主程序
│   ├── lan_transfer.py   # 核心传输逻辑
│   └── static/           # Web 界面资源
├── web-client/           # 旧版网页端资源
└── release/              # 发布产物
    ├── lan-transfer-desktop.exe    # Windows 电脑端
    ├── lan-transfer-android.apk    # 安卓安装包
    ├── shared-files/               # 电脑端共享目录
    └── received-files/             # 手机端接收目录
```

## 快速开始

### 方式一：直接运行（推荐）

1. **电脑端**：双击运行 `release/lan-transfer-desktop.exe`
2. **手机端**：安装 `release/lan-transfer-android.apk`
3. **连接**：手机 App 扫描电脑端二维码即可互传文件

### 方式二：从源码运行

**电脑端：**

```bash
cd desktop-client
pip install -r requirements.txt  # 如有依赖文件
python main_gui.py
```

**安卓端：**

```bash
cd android-client
.\build_apk.ps1
```

需要本地已配置 JDK 17、Android SDK、Gradle。

## 使用方法

### 电脑端操作

| 功能 | 操作 |
|------|------|
| 启动服务 | 运行 exe 后自动启动 |
| 查看二维码 | 主界面左侧显示 |
| 发布文件 | 点击「添加文件」或「添加文件夹」 |
| 打开共享目录 | 点击「打开共享文件夹」 |
| 查看接收文件 | 点击「打开接收文件夹」 |

### 手机端操作

| 功能 | 操作 |
|------|------|
| 扫码连接 | 点击「📷 扫码」扫描电脑端二维码 |
| 手动连接 | 输入服务器地址和口令，点击「连接」 |
| 下载文件 | 点击文件列表中的文件 |
| 上传文件 | 点击「上传」选择文件 |
| 管理已下载 | 点击「📁 已下载」进行打开/分享/移动/删除 |

## 技术栈

| 端 | 技术 |
|---|---|
| 电脑端 | Python 3、Tkinter、ssl（mTLS） |
| 安卓端 | Kotlin、ZXing（扫码）、FileProvider |
| 传输 | HTTPS + mTLS 双向认证 |

## 目录说明

- `android-client/`：安卓应用源码，使用 Kotlin 编写
- `desktop-client/`：Windows 桌面端源码，使用 Python + Tkinter
- `web-client/`：旧版网页端静态资源（已废弃）
- `release/`：可直接使用的发布产物
  - `shared-files/`：电脑端共享的文件目录
  - `received-files/`：手机端下载的文件默认保存位置

## 注意事项

- 电脑和手机需连接**同一局域网**
- 首次连接需在手机端确认证书指纹（TOFU）
- 手机端文件保存在 App 专属目录，系统文件管理器不可见
- 通过「移动/另存」可将文件导出到公共目录

## License

MIT
