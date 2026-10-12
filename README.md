clipboard_app/
├── main.py                    # 入口：单实例保护 + 串起监听、存储、界面、托盘、快捷键、开机自启
├── installer.py               # 安装器入口（图形向导 / --install-silent 静默安装）
├── uninstaller.py             # 卸载器入口（独立 uninstaller.exe，问用户删哪些数据）
├── requirements.txt
├── .gitignore
├── assets/
│   ├── icon.svg               # 托盘/程序图标矢量源文件
│   ├── icon.ico               # 由 icon.svg 生成的多尺寸图标（打包时嵌进 exe）
│   └── file_icons/            # 文件类型图标（svg），列表左侧使用“文件类型-缩略图-*”
├── docs/
│   └── 用户手册.txt            # 用户手册（托盘「帮助（使用手册）」用记事本打开它）
├── core/
│   ├── __init__.py
│   ├── version.py             # 版本号 + 作者/邮箱唯一来源（关于框、手册、安装器、exe 属性）
│   ├── paths.py               # 资源路径（源码运行 / PyInstaller 打包后都能找到）
│   ├── models.py              # ClipItem / ClipType
│   ├── storage.py             # SQLite + 文件目录 + 大文本落盘 + 去重 + 跨进程锁
│   ├── lockfile.py            # 单实例用的文件锁 + 命令文件（不依赖 Qt，卸载器也用它）
│   ├── single_instance.py     # 单实例（Qt 层）：第二个实例唤出第一个；收到 quit 就优雅退出
│   ├── clipboard_io.py        # 读写剪贴板 + 图片/大文本落盘 + 敏感内容判定
│   ├── capture.py             # 采集决策（读 -> 过滤 -> 入库）+ 未记录的原因码
│   ├── clipboard_listener.py  # Win32 监听链
│   ├── hotkey.py              # 全局快捷键（pynput，按虚拟键码认键）
│   ├── applog.py              # 统一日志（打包后没有控制台，出错只能靠它）
│   ├── diag.py                # --diag 自检（路径可写性/托盘/打开文件夹实测）
│   ├── manual.py              # 用户手册：定位 + 用默认程序打开
│   ├── shortcut.py            # 建/删 .lnk（pywin32，失败退 PowerShell）
│   ├── installer.py           # 安装核心逻辑（复制/写配置/快捷方式/卸载项/自启，不依赖 Qt）
│   ├── autostart.py           # 开机自启（打包版=最高权限计划任务，源码版=Run 键）
│   ├── uninstall.py           # 卸载核心逻辑（不依赖 Qt）：关程序/删数据/删目录/删注册表
│   ├── data_clean.py          # 按类型统计并删除文本/图片/文件（卸载时勾选用）
│   └── settings.py            # JSON 配置
├── ui/
│   ├── __init__.py
│   ├── main_window.py         # 主窗口 + 展开/收起 + 贴边（按窗口所在屏）+ 空白处右键
│   ├── history_window.py      # 独立的全量历史窗口（搜索/复制/修改/删除/清空）
│   ├── item_widget.py         # 单条记录 + 右键菜单 + 缩略图/类型图标
│   ├── elided_label.py        # 多行折行 + 最后一行省略号的文本标签（卡片预览用）
│   ├── file_icons.py          # 文件类型 -> svg 图标（判定 + 渲染缓存）
│   ├── edit_dialog.py         # 文本查看 / 修改对话框
│   ├── help.py                # 「帮助（使用手册）」：记事本打开，失败则程序内显示全文
│   ├── settings_form.py       # 设置项表单（设置对话框与安装向导共用一份定义）
│   ├── settings_dialog.py     # 设置对话框（载入 -> 校验 -> 保存）
│   ├── install_wizard.py      # 安装向导四页界面
│   ├── uninstall_tk.py        # 卸载界面（tkinter，**刻意不用 Qt**：卸载器要小、要独立）
│   ├── about_dialog.py        # 关于：版本号、作者、邮箱、运行方式、数据目录、记录条数
│   ├── tray.py                # 托盘 + 右键菜单（托盘与主窗口共用一份菜单定义）
│   ├── utils.py               # 类型名 / 大小 / 时间格式化 + 多屏辅助 + 屏幕居中
│   └── style.qss              # Win11 风格
└── build/
    ├── build_all.py           # 一条命令打全套（主程序 -> 卸载器 -> 安装程序）
    ├── build.spec             # 主程序打包配置（自动带上 exe 版本信息 + 用户手册）
    ├── uninstaller.spec       # 卸载器打包配置（单文件 uninstaller.exe，约 13 MB）
    ├── installer.spec         # 安装程序打包配置（单文件 Setup.exe，内嵌主程序目录）
    ├── make_icon.py           # 由 assets/icon.svg 生成 assets/icon.ico（需要 Pillow）
    └── make_version_info.py   # 由 core/version.py 生成 exe 的版本信息

运行：
    pip install -r requirements.txt
    python main.py

打包：
    pip install pyinstaller
    python build/make_icon.py          # 首次/换图标后跑一次，生成真正的多尺寸 ico
    python build/build_all.py          # 一条命令打全套（推荐）

    等价于下面三步，顺序不能换：
    pyinstaller build/build.spec       --clean --noconfirm
        产物 dist/ClipboardHistory/ClipboardHistory.exe（约 115 MB，单文件打法见 spec 末尾注释）
    pyinstaller build/uninstaller.spec --clean --noconfirm
        产物 dist/uninstaller.exe（独立卸载器，约 13 MB）
        -> 再把它复制进 dist/ClipboardHistory/（build_all.py 干这一步），
           这样绿色版也自带卸载器
    pyinstaller build/installer.spec   --clean --noconfirm
        产物 dist/ClipboardHistory-Setup.exe（安装程序，把上面那个目录整个嵌进去，
        所以有一百多 MB；它必须在主程序打包之后再打）

    注意：改了 core/version.py 里的版本号或 spec 里的 uac_admin 之后必须加 --clean，
    PyInstaller 的缓存不认这两个改动（曾经出现过「改了没生效」）。

    说明：托盘图标 Qt 会按内容嗅探格式，把 svg 直接改名成 .ico 也能显示；
    但要让 exe 带上图标，assets/icon.ico 必须是真正的 Windows 图标，
    所以图标以 icon.svg 为准，用 make_icon.py 生成 icon.ico。

    版本号只在 core/version.py 里改一处：关于框、托盘菜单、用户手册、安装器、
    exe 属性（右键 exe -> 属性 -> 详细信息）都由它派生，打包时会自动生成版本信息文件。
    作者/邮箱（吴杰 / 1794185873@qq.com）同样只在 core/version.py 里写一次。

单实例：
    双击第二次不会起第二份，而是把已经在跑的那个窗口唤出来。实现是
    「文件锁 + 命令文件」（core/lockfile.py + core/single_instance.py）+
    存储目录上的跨进程锁（core/storage.py），前者拦正常双击，后者保证
    history.db 永远只有一个写入者。
    命令文件里现在有两个命令：show（唤出窗口）和 quit（优雅退出）——
    卸载器卸载前就是往这里写 quit，让主程序走正常的释放流程退出，
    而不是直接杀进程。底层那套只用标准库，所以卸载器也能复用（core/lockfile.py）。

用户手册 / 帮助菜单：
    手册就是 docs/用户手册.txt（UTF-8 with BOM + CRLF，记事本直接打开不乱码），
    打包时一起放进程序目录（onedir 下是 _internal\docs\）。
    托盘右键菜单最后一段是「帮助（使用手册）…」（在「关于」上面一条，主窗口
    右键弹出的同一份菜单里也有）：点击用系统默认程序打开手册；打不开（关联被
    改坏、安全软件拦截、文件缺失）时会**在程序内**显示全文并提供「复制全文」，
    绝不出现「点了没反应」。手册也可以放在 exe 同级目录，程序会优先用那里的那份。

剪贴板采集（记什么 / 为什么不记）：
    读剪贴板是 core/clipboard_io.py 的 read_clipboard()，判断与入库在
    core/capture.py 的 capture_once()，入口是 main.py 的 _collect_clipboard()。
    优先级：**文件 > 纯文本 > 图片 > HTML**。
      * 文本优先于图片：Excel / WPS 表格 / 部分文档编辑器复制时，剪贴板上
        同时有 CF_UNICODETEXT 和整段位图。以前优先取图片，结果是「想要的文本
        在历史里变成了一张图片」；而整页位图很大，一旦超过单条上限，这一次
        复制就整个丢掉（连图片也没有）。
      * 文本优先于 HTML：只要剪贴板里真有纯文本就按文本存，不会把网页源码
        当成记录内容（老行为如此，保留）。

    密码排除（设置里「排除密码类内容」，默认开）= core/clipboard_io.py 的
    sensitive_reason()，只在两种情况下拦：
        1) 来源窗口标题里有 password / 密码 / 1password / bitwarden / keepass…
        2) 正文里出现「凭证赋值」且值本身像密钥：password: xxx、密码=xxx、
           "password": "xxx"。值必须是不含空格和中文的可打印 ASCII，且不含
           括号——pwd = os.getcwd() 这种代码放过。
    **不再**拿关键词在整段正文里做子串匹配：老做法会把「全选复制的一整篇文档」
    整条丢掉，只要正文里出现过「密码判断」「密码：推荐开」这种说明文字
    （本项目自己的 README / 用户手册里就有），用户看到的就是「我明明复制了，
    历史里没有」，而日志里一个字都没有。现在整篇文档照记。

    **每一次没记录都会写进日志**（logger 名 clipboard.capture）：
        未记录：原因=sensitive（命中密码规则，按设置跳过） 正文出现凭证赋值：
                password（值 7 字符）；前台窗口='…' 大小=17 字节 类型=text
               剪贴板格式=text/plain
        已记录：类型=text 大小=20470 字节 剪贴板格式=text/plain,text/html
    原因码：sensitive（密码规则）/ too-large（超单条上限）/ empty（此刻读不到
    内容：对方正在写，或被别的程序占着——按 100/250/500/1000/2000 毫秒各补读
    一次）/ error（读或落盘出错，带堆栈）。日志里只写值的长度，不写值本身。
    采集槽函数整体包在 try/except 里：出异常也留堆栈，不再「复制了却毫无线索」。

主窗口的「−」（最小化）：
    点它 = **收进托盘**，不是系统最小化。本窗口是 Qt::Tool（没有任务栏按钮），
    Windows 会把最小化的这类窗口缩成一条 237x39 的小窗塞到**屏幕左下角**，
    而且 IsWindowVisible 仍然是 True；此时从托盘唤出（show + raise + activate）
    也恢复不了（isMinimized 仍是 True）——用户看到的就是「缩小后屏幕左下角
    残留一个窗口，还唤不回来」。实测（GetWindowPlacement / GetWindowRect）：
        showMinimized() 之后        rect=(0,1489,237,1528) iconic=True visible=True
        show+raise+activateWindow   rect 不变、仍然 iconic=True（唤不回来）
        showNormal() 之后           rect=(600,390,1050,579) 正常
    所以：
      * 「−」直接连 MainWindow.minimize_to_tray()（内部就是 hide + 气泡）；
      * changeEvent 兜住其它最小化路径（Win+D、系统菜单、第三方工具）：
        先解除最小化再隐藏，而且隐藏必须**延后一轮事件循环**——Qt 的
        showMinimized() 返回之后还会把窗口显示一次，同步 hide() 会被它顶掉
        （实测同步 hide() 之后 isVisible 仍然是 True）；
      * main.py 的 _show_window() 先看 isMinimized() 再 showNormal()，免得
        从托盘唤出一个还带着最小化状态的窗口。

安装器：
    dist/ClipboardHistory-Setup.exe           图形向导（推荐发给用户）
    ClipboardHistory.exe --install            绿色版目录里也能起同一个向导
    ClipboardHistory-Setup.exe --install-silent --dir=D:\Apps\ClipboardHistory ^
        --data-dir=D:\ClipData --no-desktop --auto-start=1        静默安装（批量部署）

    向导分四页：欢迎（权限/覆盖安装提示）-> 安装位置与选项（安装目录、桌面/开始
    菜单快捷方式、装完是否运行）-> 设置（程序「设置」里的**全部**选项，全部预填
    默认值，可一键「恢复默认值」）-> 安装进度与结果。
    安装做的事：复制程序文件 -> 写 %APPDATA%\ClipboardHistory\settings.json
    -> 在安装目录写 install.json（卸载按它删干净）-> 建快捷方式 -> 注册
    HKCU\...\Uninstall\ClipboardHistory（出现在「设置 -> 应用」里）
    -> 按选择创建开机自启计划任务。每一步失败都单独记日志，程序文件装好就算成功，
    快捷方式/自启这类失败只在结果里列出来。

    **数据目录默认是「安装目录\data」**（core/installer.py 的 DATA_SUBDIR）：
    第 2 页选了安装目录，第 3 页的存储路径就跟着填成 <安装目录>\data，
    安装时自动创建，不需要用户操心路径。三条保护规则（见
    ui/settings_form.py 的 path_follows_install_dir）：
        1) 用户在本次会话里自己改过存储路径 -> 不再自动改；
        2) 当前存储路径下已经有 history.db -> 绝不改（改了等于让用户丢历史）;
        3) 当前路径的目录名不是 data（自定义目录名）-> 不动。
    静默安装不给 --data-dir 时同样落到 <安装目录>\data；
    --data-dir 给了就听它的。

    细节：安装目录里已有的程序正在运行时会被占用，覆盖会失败，所以安装前会检查
    exe 是否被锁并提示「请先退出程序」；数据目录里的 settings.json/history.db/
    app.log/files/ 不会被从源目录复制过去（那是本机的运行痕迹，不是程序的一部分）。

    卸载项指向的是安装目录里的 **uninstaller.exe**（见下一节），不再让主程序带参数
    去卸载；万一 payload 里没有卸载器（只打了主程序），自动退回
    「ClipboardHistory.exe --uninstall」的老流程，并在安装结果里说明。

卸载器（uninstaller.exe）：
    「设置 -> 应用 -> 已安装的应用」里的卸载，以及安装目录里的 uninstaller.exe，
    走的都是它。核心目的：**卸载时问清楚哪些本机数据要删**。

    界面（tkinter，见 ui/uninstall_tk.py）列出所有找到的数据目录和每一类的
    条数/体积，可以分别勾选：
        文本记录（含 HTML） / 图片 / 文件（复制进来的文件）
    设置项与日志只有在「三类全勾 + 确实清空了」时才一并删除，否则保留
    （下次安装还能沿用配置）。数据删干净后目录空了就顺手 rmdir，
    但**绝不 rmtree 整个目录**——存储目录可能被用户改成别的用途的目录。

    数据默认就在安装目录里的 data\（<安装目录>\data），所以卸载时有个特殊情况：
    用户选择「保留数据」时，删程序文件会跳过这个数据目录（core/uninstall.py 的
    _has_content 判断它非空），结果就是安装目录里只剩一个 data\，
    报告里写明「数据保留在：…」并仍算卸载成功；三类全勾时才连它一起删掉。

    为什么要有独立 exe：
    - 卸载必须在主程序之外跑（主程序删不掉自己），而且要先把正在运行的主程序请出去；
    - 主程序不用再背着卸载界面；「设置 -> 应用」里的卸载项也更像个正常软件；
    - 它自己也不能待在安装目录里删自己，所以启动后先把自己复制到 TEMP 再干活
      （那一步用的是 core/uninstall.py 的 spawn_job_copy）。

    卸载流程（core/uninstall.py 的 run_uninstall_job，每步单独 try + 记日志）：
        等派生自己的进程退出 -> 请正在运行的主程序优雅退出（写 app.cmd 的 quit 命令；
        6 秒没反应才 taskkill）-> 摘掉 HKCU 卸载项 -> 清 Run 键与计划任务
        -> 删快捷方式 -> 按勾选删数据（core/data_clean.py）-> 清安装目录
        -> 清掉 TEMP 里的副本（正在运行的副本挪成 clip_uninst_leftover_*.exe
           并登记「下次重启删除」）

    两道安全阀（卸载=删目录里的一切，所以必须先验明正身）：
        1) 目录里有 main.py + requirements.txt 却没有主程序 exe -> 判定为项目源码
           目录，拒绝（防止开发时手滑删掉工程）；
        2) 目录里连 ClipboardHistory.exe / uninstaller.exe / install.json 都没有
           -> 拒绝（防止 --install-dir 写错指到无关目录，把别人的文件删了）。
    源码运行（没有 sys.frozen）时，**必须**显式给 --install-dir，否则直接拒绝：
    否则默认值会指向工程目录或 Python 安装目录。

    静默卸载（批量/自动化）：
        uninstaller.exe --silent [--delete=text,image,files|all] [--data-dir=...]
                        [--keep-registry] [--report=结果.json]
        （不写 --delete = 只删程序、保留数据；--delete=all = 三类全删）
        注意它是「派生副本后立刻返回」：父进程不等卸载干完（副本在 TEMP 里干活，
        父进程得先退出才能删掉自己所在的目录）。要拿结果就传 --report=文件，
        副本会写一份 JSON（删了多少、释放多少、有没有出错）。

    卸载器刻意**不依赖 Qt**（只用标准库 tkinter），所以只有十几 MB：
    它会跟着安装包一起发给用户，塞 PySide6 的话 Setup.exe 直接涨 40 MB。
    界面上两处坑也记在 ui/uninstall_tk.py 的注释里：窗口尺寸必须按内容算
    （高 DPI 下写死尺寸会把按钮挤出可见区域）、底部按钮栏要先 pack
    （否则被中间 expand 的区域压成 1 像素高）。

权限与开机自启（重要）：
    打包版的 exe 清单里写了 requireAdministrator，**每次启动会弹一次 UAC**，
    需要管理员账户。原因见下面「安全软件把未签名 exe 当不可信程序」：
    实测非提权运行时 %APPDATA% 写入、HKCU\Run 写入、启动 explorer.exe、
    托盘图标全被拦；提权后一切正常。安装程序和卸载器同样是 requireAdministrator
    （要写 Program Files、快捷方式、卸载项和计划任务）。

    因此开机自启**不能**再用 HKCU\...\Run（Run 键拉起的进程不带提权，起来
    就是残废实例），打包版改用「登录时运行 + 以最高权限运行」的计划任务：
        查看：schtasks /Query /TN ClipboardHistory /V /FO LIST
        删除：schtasks /Delete /TN ClipboardHistory /F
        自检：ClipboardHistory.exe --diag 的「开机自启」一节会列出任务状态
    （源码运行仍用 Run 键，因为 python.exe 不挑权限。切换开关时两种方式都会
    被同步清理，不会起两份；卸载时也会把这个任务删掉。）

    不想每次过 UAC 的话：把 build/build.spec（和 installer.spec）里的
    uac_admin=True 改回 False，加 --clean 重新打包，然后把 exe 加进杀软白名单 /
    做代码签名即可。


环境变量（一般用不到，调试/测试用）：
    CLIPBOARD_DATA_DIR    指定数据目录（优先于设置里的存储路径）
    CLIPBOARD_LOCK_DIR    指定单实例锁/命令文件所在目录
    CLIPBOARD_LOG_FILE    指定日志文件（默认 %APPDATA%\ClipboardHistory\app.log）

排错（打包版最常遇到的几件事）：
    日志：按这个顺序挑第一个能写的位置，所以「一定」会有一份日志：
        CLIPBOARD_LOG_FILE（环境变量指定）
        -> %APPDATA%\ClipboardHistory\app.log        （默认）
        -> %LOCALAPPDATA%\ClipboardHistory\app.log
        -> exe 同目录\app.log                        （打包版；用户配置目录被拦时的救命位）
        -> %TEMP%\ClipboardHistory-app.log
        自动轮转，保留 3 个文件；关于框里显示实际使用的路径。
        启动时会记下运行方式、资源目录、配置/存储路径、托盘状态、单实例锁；
        退出/重启/打开存储文件夹等操作也会留痕。反馈问题请直接带上这个文件。

    自检：ClipboardHistory.exe --diag
        不建主窗口，只做检查：逐个日志候选路径的写测试、配置/存储目录、
        单实例锁、托盘（含一条测试气泡）、以及四种打开文件夹方式各自的实测结果
        （explorer.exe 会记录退出码）。报告写文件 + 弹窗 + 可复制。
        凡是「图标不见 / 打不开文件夹 / 没有日志」这类问题，先跑它。

    退出 / 重启点了没反应：这两个动作会先释放资源（配置写盘、关数据库、停监听、
        放锁）再退出。历史上只要其中任一步抛异常（例如安全软件拦住了
        settings.json 的写入），app.quit() 就永远执行不到——现在每一步都单独
        try/except 并写日志，另有看门狗线程（哪一步卡住也能在 4/6 秒内真的退出），
        重启还多了一条 subprocess 兜底和「重启失败」提示框。

    打开存储文件夹没反应：依次尝试 explorer.exe（绝对路径，并用退出码判断是否
        真的起来了）-> QDesktopServices -> os.startfile，三条都不行会弹出
        **看得见的**错误框（带「复制路径」按钮，可直接粘到资源管理器地址栏），
        同时写日志、发气泡（气泡是延后发送的，不会卡住界面）。

    托盘里看不到图标：Windows 11 默认把新程序的托盘图标放进「隐藏的图标」
    （任务栏上的 ^ 折叠区）。到 设置 -> 个性化 -> 任务栏 -> 其他系统托盘图标
    里把「剪贴板历史」打开，或从折叠区把图标拖到任务栏上即可。
    程序启动时会检查托盘是否可用、图标是否为空，并在壳（explorer）没准备好时
    重试注册，这些都会写进日志；--diag 会发一条测试气泡：能看到气泡就说明
    图标已经注册成功，问题只在 Windows 的折叠设置。

    安全软件把未签名 exe 当「不可信程序」时（实测复现过）：
        - 写 %APPDATA% / %LOCALAPPDATA% / %TEMP% 全部被拒（日志因此落到了 exe 同目录）
        - 写 HKCU\...\Run（开机自启）被拒
        - 启动 explorer.exe 直接失败：退出码 0xC0000142，Windows 弹
          「应用程序无法正常启动(0xc000042x)」——所以「打开存储文件夹」会失败
        - 同一个用户用其它程序（python.exe / powershell.exe）做同样的事却都正常
        这类限制是**按 exe 镜像**判定的，程序自己绕不过去，需要二者之一：
        把 exe（或其所在目录）加入杀毒/防护软件的信任区，或给 exe 做代码签名。
        程序这边已经做到：日志一定留得下、失败一定看得见、退出/重启照常、
        配置文件写不进 %APPDATA% 时自动改存到数据目录（settings.json）。

已知限制：
    - HTML 记录写回剪贴板时只写纯文本，不还原富文本格式
    - 同时带文本和图片的复制（表格、部分文档）统一按**文本**记录，想要那张
      位图的话请用截图工具（这是刻意的取舍，见上面「剪贴板采集」一节）
    - 敏感判定是关键词+赋值形态的启发式：浏览器密码框里那种「只有一个值、
      正文里没有 password 字样」的剪贴板内容识别不出来（Windows 的
      ExcludeClipboardContentFromMonitorProcessing 标记本程序也没有读）
    - 没有导出/备份功能；存储目录里的 history.db 与 files/ 就是全部数据
    - 多屏贴边按「窗口所在的那块屏」算；跨屏拖动后需要贴着目标屏的边缘才会贴边
    - 安装程序是自包含的单文件（内嵌整个程序目录 + 卸载器），所以比程序目录还大
      （当前：payload 136 MB / Setup.exe 130 MB，其中卸载器 13 MB）。
      想要更小的安装包：一是用 Inno Setup / NSIS 再包一层，
      二是把安装向导自己那份 PySide6 里的死重量删掉（现在还被 qpdf /
      virtualkeyboard 两个插件拖着 Qt6Quick、Qt6Qml、Qt6Pdf、多语言翻译等
      约 30 MB，只影响向导本身，不影响装出来的程序）

new feature
1. 右键记录窗口，多一个操作：pin，该记录的位置将被固定