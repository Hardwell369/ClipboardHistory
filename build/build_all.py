# build/build_all.py
"""
一条命令打齐全套产物（顺序很重要）：

    python build/build_all.py            # 等价于下面三步 + 拷贝
    python build/build_all.py --no-clean # 复用 PyInstaller 缓存（快，但改版本号/清单后别用）

步骤：
    1. pyinstaller build/build.spec         -> dist/ClipboardHistory/（绿色版 + payload）
    2. pyinstaller build/uninstaller.spec   -> dist/uninstaller.exe
    3. 把 uninstaller.exe 放进 dist/ClipboardHistory/（绿色版也是「能卸载」的）
    4. pyinstaller build/installer.spec     -> dist/ClipboardHistory-Setup.exe（内嵌 payload）

为什么第 4 步必须在第 2、3 步之后：Setup.exe 是把整个 dist/ClipboardHistory
目录嵌进去的，卸载器不在里面的话，装出来的程序就没有独立卸载器
（core/installer.py 会退回「主程序 --uninstall」的老流程）。

改了 core/version.py 的版本号或 spec 里的 uac_admin 之后必须加 --clean：
PyInstaller 的缓存不认这两个改动（历史上出现过「改了没生效」）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
PAYLOAD = os.path.join(DIST, "ClipboardHistory")
UNINSTALLER = os.path.join(DIST, "uninstaller.exe")
SETUP = os.path.join(DIST, "ClipboardHistory-Setup.exe")


def run(step: str, spec: str, clean: bool) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", spec, "--noconfirm"]
    if clean:
        cmd.append("--clean")
    print(f"\n===== [{step}] {' '.join(cmd)} =====", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"[{step}] 打包失败（退出码 {proc.returncode}）")


def size_of(path: str) -> str:
    if os.path.isfile(path):
        return f"{os.path.getsize(path) / (1024 * 1024):.1f} MB"
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(base, name))
    return f"{total / (1024 * 1024):.1f} MB"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    clean = "--no-clean" not in argv

    run("1/4 主程序", os.path.join("build", "build.spec"), clean)
    if not os.path.isfile(os.path.join(PAYLOAD, "ClipboardHistory.exe")):
        raise SystemExit(f"没有找到主程序产物：{PAYLOAD}\\ClipboardHistory.exe")

    run("2/4 卸载器", os.path.join("build", "uninstaller.spec"), clean)
    if not os.path.isfile(UNINSTALLER):
        raise SystemExit(f"没有找到卸载器产物：{UNINSTALLER}")

    print("\n===== [3/4] 把 uninstaller.exe 放进程序目录 =====", flush=True)
    target = os.path.join(PAYLOAD, "uninstaller.exe")
    shutil.copy2(UNINSTALLER, target)
    print(f"已放入：{target}（{size_of(target)}）")

    run("4/4 安装程序", os.path.join("build", "installer.spec"), clean)
    if not os.path.isfile(SETUP):
        raise SystemExit(f"没有找到安装程序产物：{SETUP}")

    print("\n===== 完成 =====")
    print(f"  绿色版目录：{PAYLOAD}（{size_of(PAYLOAD)}）")
    print(f"  卸载器：    {UNINSTALLER}（{size_of(UNINSTALLER)}）")
    print(f"  安装程序：  {SETUP}（{size_of(SETUP)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
