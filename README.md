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
├── android-app/          # 安卓端源码（Kotlin）
│   ├── app/src/main/     # 应用主代码
│   └── build_apk.ps1     # 一键构建脚本
├── lan-transfer-app/     # 电脑端源码（Python）
│   ├── main_gui.py       # GUI 主程序
│   ├── lan_transfer.py   # 核心传输逻辑
│   └── static/           # Web 界面资源
└── lan-transfer/         # 旧版静态资源
```

## 使用方法

### 电脑端

1. 运行 `lan-transfer-app/main_gui.py` 或打包后的 exe
2. 启动服务后会显示二维码和连接信息
3. 可发布文件到共享目录，或查看接收的文件

### 安卓端

1. 安装 `android-app/lan-transfer.apk`
2. 打开 App，扫描电脑端二维码
3. 连接成功后即可浏览、下载、上传文件

## 构建说明

### 电脑端打包

```bash
cd lan-transfer-app
pip install pyinstaller
pyinstaller 局域网互传.spec
```

### 安卓端构建

```bash
cd android-app
.\build_apk.ps1
```

需要本地已配置 JDK 17、Android SDK、Gradle。

## 技术栈

| 端 | 技术 |
|---|---|
| 电脑端 | Python 3、Tkinter、ssl（mTLS） |
| 安卓端 | Kotlin、ZXing（扫码）、FileProvider |
| 传输 | HTTPS + mTLS 双向认证 |

## 截图

（待补充）

## License

MIT
