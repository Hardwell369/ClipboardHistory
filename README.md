# ClipboardHistory

一款面向 Windows 11
的剪贴板历史工具。程序在后台监听剪贴板，将符合规则的文本、图片和复制的文件保存到本地，支持通过托盘菜单和悬浮窗口查看、搜索、复制、编辑及删除历史记录。

> **项目状态：**
> 持续开发中。部分行为、安装包体积和权限要求仍可能随版本调整。使用前请阅读下方的隐私说明、已知限制和排错指南。

## 功能特性

-   **后台采集**：监听剪贴板变化，按规则保存文本、图片和文件。
-   **历史管理**：查看历史记录，搜索、复制、编辑、删除单条记录或清空历史。
-   **快速访问**：系统托盘、悬浮窗口和全局快捷键。
-   **本地存储**：使用 SQLite
    保存索引与元数据，较大的文本、图片和文件内容可单独落盘。
-   **重复内容处理**：支持去重，并通过跨进程锁保护数据库写入。
-   **内容过滤**：可启用密码类内容排除规则；未记录的原因会写入日志，日志不会记录被判定为敏感内容的实际值。
-   **安装与卸载**：提供图形安装向导、静默安装和独立卸载器。
-   **开机启动**：支持配置登录后自动启动。
-   **诊断日志**：提供 `--diag`
    自检入口，帮助定位路径、托盘、文件夹打开等问题。

## 运行环境

-   Windows 10/11（项目主要针对 Windows 11 进行开发和验证）
-   Python 版本以项目依赖和实际测试环境为准
-   图形界面基于 PySide6；部分 Windows 集成功能使用 `pywin32`、`pynput`
    等依赖

## 从源码运行

在项目根目录打开 PowerShell 或终端：

``` powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

如果 PowerShell
不允许执行虚拟环境激活脚本，可以直接使用虚拟环境中的解释器安装依赖并启动程序：

``` powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## 构建 Windows 程序

构建前先安装项目依赖和 PyInstaller：

``` powershell
pip install -r requirements.txt
pip install pyinstaller
```

首次构建或修改图标后，执行：

``` powershell
python build/make_icon.py
python build/build_all.py
```

`build_all.py`
会按顺序构建主程序、独立卸载器和安装程序。也可以分别执行以下命令，但顺序不能调整：

``` powershell
pyinstaller build/build.spec --clean --noconfirm
pyinstaller build/uninstaller.spec --clean --noconfirm
pyinstaller build/installer.spec --clean --noconfirm
```

独立构建卸载器后，需要将 `dist/uninstaller.exe` 复制到
`dist/ClipboardHistory/`，使绿色版目录也包含卸载器。推荐使用
`build/build_all.py` 自动完成这些步骤。

预期产物：

----------------------------------------------------------------------------------
  文件                                           说明
---------------------------------------------- -----------------------------------
  `dist/ClipboardHistory/ClipboardHistory.exe`   主程序（目录分发形式）

  `dist/ClipboardHistory/uninstaller.exe`        独立卸载器

  `dist/ClipboardHistory-Setup.exe`              图形安装程序，内嵌主程序目录
  ----------------------------------------------------------------------------------

> **注意：** 修改 `core/version.py` 中的版本号，或修改打包配置中的 UAC
> 权限选项后，请使用 `--clean` 重新构建，避免 PyInstaller
> 缓存导致修改未生效。

### 程序图标与版本信息

-   `assets/icon.svg` 是图标源文件。
-   `build/make_icon.py` 根据 SVG 生成真正的多尺寸 Windows 图标
    `assets/icon.ico`。
-   版本号、作者信息等元数据集中维护在
    `core/version.py`，打包时会生成可写入 EXE 属性的版本信息。

## 安装与卸载

### 图形安装

推荐将 `ClipboardHistory-Setup.exe`
分发给普通用户。也可以在绿色版目录中运行：

``` powershell
ClipboardHistory.exe --install
```

安装向导会引导用户选择安装目录、快捷方式、启动选项和程序设置。默认数据目录为安装目录下的
`data` 子目录；用户可以在安装时或设置中选择其他位置。

### 静默安装

可用于脚本或批量部署。示例：

``` powershell
ClipboardHistory-Setup.exe --install-silent --dir="D:\Apps\ClipboardHistory" --data-dir="D:\ClipData" --no-desktop --auto-start=1
```

参数含义：

-   `--dir`：安装目录。
-   `--data-dir`：数据目录；省略时默认使用安装目录下的 `data` 子目录。
-   `--no-desktop`：不创建桌面快捷方式。
-   `--auto-start=1`：启用开机自启。具体可用参数以当前版本的命令行实现为准。

### 卸载与数据保留

卸载程序会先尝试让正在运行的主程序正常退出，再执行卸载。交互式卸载支持分别选择是否删除：

-   文本记录（包括 HTML 内容）
-   图片
-   复制进来的文件

只有在所有内容类型都被选中并且确实完成清理时，才会一并删除设置和日志。若选择保留数据，卸载器会尽可能保留数据目录，并报告保留位置。

静默卸载示例：

``` powershell
uninstaller.exe --silent --delete=text,image,files --report="D:\Temp\uninstall-report.json"
```

-   不指定 `--delete`：只卸载程序，保留数据。
-   `--delete=all`：删除所有受支持的记录内容。
-   `--data-dir`：指定要处理的数据目录。
-   `--keep-registry`：按程序当前实现保留相关注册信息。
-   `--report`：输出 JSON 结果报告。

**请在卸载前确认数据删除选项。**
如果数据目录中保存了其他用途的文件，不要选择删除数据。

## 数据与隐私

-   数据默认保存在本机，不会因为使用本工具而自动上传到云端。
-   数据目录包含 SQLite 数据库（例如
    `history.db`）以及保存的文件内容目录（例如
    `files/`）。请妥善保护该目录。
-   日志文件用于诊断问题。日志会记录采集成功或失败的原因、内容类型、大小等信息；敏感内容过滤日志只记录匹配规则和长度，不记录被判定为敏感的实际值。
-   "排除密码类内容"是启发式过滤，不是密码管理器，也不能保证识别所有密码、令牌或密钥。请勿将它视为绝对的敏感信息保护机制。
-   如需彻底清除历史记录，请使用程序提供的清理功能或卸载器，并确认数据库、文件目录及备份均已按需处理。

## 数据采集规则概览

当前实现会按优先级选择剪贴板内容：**文件 \> 纯文本 \> 图片 \> HTML**。

-   当剪贴板同时包含文本和图片时，通常保存文本，不会另行保存该图片。这是为了避免复制表格或文档时，把文本内容错误地记录为整页位图。
-   当存在纯文本时，优先保存纯文本，而不是把 HTML 源内容作为记录。
-   密码类内容排除规则默认启用（具体默认值以当前版本设置为准）。规则主要检查来源窗口标题，以及正文中类似
    `password: value`、`密码=值` 的凭证赋值形式。
-   若内容未被记录，日志会记录原因码，例如
    `sensitive`、`too-large`、`empty` 或 `error`。其中 `empty`
    可能表示读取时剪贴板内容尚未准备好，程序会按实现进行补读。

以上规则是当前实现的行为说明，后续版本可能会调整。

## 项目结构

``` text
clipboard_app/
├── main.py                  # 程序入口
├── installer.py             # 安装器入口
├── uninstaller.py           # 卸载器入口
├── requirements.txt
├── assets/
│   ├── icon.svg              # 图标源文件
│   ├── icon.ico              # Windows 图标
│   └── file_icons/           # 文件类型图标
├── docs/
│   └── 用户手册.txt
├── core/                     # 存储、采集、日志、路径、安装/卸载等核心逻辑
├── ui/                       # 主窗口、历史窗口、设置、托盘和对话框
└── build/                    # PyInstaller 配置与构建脚本
```

核心模块包括：

-   `core/storage.py`：SQLite、文件内容存储、去重与跨进程锁。
-   `core/clipboard_io.py`、`core/capture.py`：读取剪贴板、过滤内容并保存记录。
-   `core/clipboard_listener.py`：Windows 剪贴板监听。
-   `core/lockfile.py`、`core/single_instance.py`：单实例协调与进程间命令。
-   `core/settings.py`：JSON 配置。
-   `core/installer.py`、`core/uninstall.py`：安装和卸载逻辑。
-   `ui/`：用户界面与交互组件。
-   `build/`：图标、版本信息及 PyInstaller 构建配置。

## 单实例与数据一致性

程序使用文件锁和命令文件协调实例：再次启动时会尝试唤起已运行的窗口，而不是正常情况下再启动一个独立实例。存储层另有跨进程锁，以避免多个进程同时写入同一数据库。卸载器会优先请求主程序正常退出，并在超时后采用兜底处理。

## 权限与开机自启

当前打包配置可能启用 Windows 管理员权限（UAC）。如果构建配置设置为
`uac_admin=True`，启动程序时会请求管理员权限；安装程序和卸载器也可能需要该权限来写入安装目录、快捷方式、卸载信息和计划任务。

在启用提权的打包版本中，开机自启使用"登录时运行、以最高权限运行"的计划任务，避免通过普通
`Run`
注册表项启动出权限不匹配的实例。源码运行时则可使用当前实现提供的注册表启动方式。

如果不希望每次启动时出现 UAC 提示，请检查 `build/build.spec` 和
`build/installer.spec` 中的 `uac_admin`
设置并重新构建。关闭提权可能改变某些系统集成功能的行为；发布前应在目标
Windows
环境中充分测试。未签名程序也可能被安全软件拦截，必要时应使用可信的代码签名证书并遵守组织的安全策略。

## 日志与故障排查

### 日志位置

程序会尝试在可用位置写入日志，常见位置包括：

1.  `CLIPBOARD_LOG_FILE` 环境变量指定的位置
2.  `%APPDATA%\ClipboardHistory\app.log`
3.  `%LOCALAPPDATA%\ClipboardHistory\app.log`
4.  打包程序目录下的 `app.log`
5.  `%TEMP%\ClipboardHistory-app.log`

实际使用路径以程序"关于"窗口或启动日志为准。反馈问题时，请附上日志，并先检查是否包含个人路径或其他隐私信息。

### 运行自检

``` powershell
ClipboardHistory.exe --diag
```

自检模式会检查日志路径、配置与存储目录、单实例锁、托盘状态，以及打开文件夹等系统集成功能，并生成可查看和复制的诊断结果。源码运行时请根据当前版本的入口参数支持情况执行相应命令。

### 常见问题

**托盘图标没有显示**

Windows 11
可能会把新程序的托盘图标放在任务栏的隐藏图标区域。请打开任务栏的隐藏图标区域，或在"设置
→ 个性化 → 任务栏 → 其他系统托盘图标"中启用该程序。也可以先运行 `--diag`
检查托盘注册状态。

**点击"打开存储文件夹"没有反应**

先运行
`--diag`，然后查看日志中的文件夹打开结果。如果系统未能启动文件管理器，程序应提供可见错误提示；可以复制显示的路径，再粘贴到资源管理器地址栏中。

**复制内容后历史记录里没有出现**

检查日志中 `clipboard.capture` 相关记录及原因码：

-   `sensitive`：命中敏感内容过滤规则。
-   `too-large`：超过单条内容大小限制。
-   `empty`：读取时没有获得有效内容。
-   `error`：读取或保存时发生异常。

**程序无法写入配置或日志**

检查目录权限、安全软件拦截情况以及磁盘空间。未签名的可执行文件可能受到安全软件的额外限制。不要在不了解影响的情况下关闭系统安全功能；可优先查看日志记录的实际路径和错误堆栈。

**退出或重启无响应**

查看日志中的资源释放或退出错误，并尝试使用 `--diag`
收集环境信息。若问题持续存在，请提交 Issue，并附上复现步骤、Windows
版本、程序版本和脱敏后的日志。

## 已知限制

-   将 HTML 记录写回剪贴板时，目前只写入纯文本，不会还原原始富文本格式。
-   剪贴板同时包含文本和图片时，当前策略优先记录文本；若需要图片，请使用截图工具单独复制。
-   敏感内容识别基于启发式规则，可能漏掉不包含明显密码字段的内容，也可能随规则变化产生误判。
-   当前版本没有内置导出/备份功能。数据库及 `files/`
    目录是主要数据来源，建议自行定期备份。
-   多屏贴边行为以窗口所在屏幕为准；跨屏移动后，需要靠近目标屏幕边缘才能触发贴边。
-   安装包包含完整程序目录和独立卸载器，因此体积可能较大。具体大小会随依赖和构建配置变化。

## 参与贡献

欢迎提交 Issue 和 Pull Request。提交问题时建议包含：

-   操作系统版本和程序版本
-   复现步骤与预期行为、实际行为
-   相关日志或 `--diag` 输出（请先脱敏）
-   如果涉及打包问题，说明使用的 Python、PyInstaller 版本及构建命令

提交代码前请尽量确认源码运行和打包运行的行为没有回归，并避免提交个人配置、数据库、日志、构建产物或其他本机数据。

## 许可证

此项目使用 MIT 许可证，您可以在遵守许可证条款的前提下自由使用、修改和分发此项目的代码。
