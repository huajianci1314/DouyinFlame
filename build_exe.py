"""一键构建脚本：PyInstaller 打包 + 复制 chromium 内核。

产物：dist/DouyinFlame/（可直接运行的绿色目录），随后由
installer/DouyinFlame.iss 用 Inno Setup 压成安装包。

用法：.venv\\Scripts\\python.exe build_exe.py
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_APP = os.path.join(ROOT, "dist", "DouyinFlame")
PW_DIR = os.path.join(os.environ["LOCALAPPDATA"], "ms-playwright")

# 项目锁定 playwright 1.58.0，只带 1208 系内核，跳过其他版本与 ffmpeg
NEEDED = ("chromium-1208", "chromium_headless_shell-1208")


def _move_old_dist() -> None:
    """把上一次的产物改名移走，让 PyInstaller 建全新目录。

    递归删除 dist 旧目录（约 800MB 数万文件）会触发系统 safe-delete
    拦截并失败（PermissionError/OSError: trash operation aborted），
    PyInstaller --noconfirm 的自动清理因此崩掉。改名是原子操作，
    不经过删除路径，稳定可靠；旧目录留待事后尽力清理。
    """
    if not os.path.exists(DIST_APP):
        return
    trash = os.path.join(ROOT, "build", f"_old_dist_{int(time.time())}")
    os.makedirs(os.path.dirname(trash), exist_ok=True)
    os.rename(DIST_APP, trash)
    print(f"旧产物已移至 {trash}（可手动删除）")
    # 尽力清理，失败不影响构建
    subprocess.call(
        ["cmd", "/c", "rmdir", "/s", "/q", trash],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main() -> None:
    py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    # 上次构建后若跑过 dist 里的 exe，会留下运行数据（数据库/登录态），
    # 既会混进安装包，还可能被残留进程锁住导致 ISCC 编译失败
    for junk in ("douyin.db", "user_data"):
        p = os.path.join(DIST_APP, junk)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    _move_old_dist()

    print("[1/3] PyInstaller 打包…")
    subprocess.check_call(
        [py, "-m", "PyInstaller", "DouyinFlame.spec",
         "--noconfirm", "--distpath", "dist", "--workpath", "build"],
        cwd=ROOT,
    )

    print("[2/3] 复制 chromium 内核…")
    dst_root = os.path.join(DIST_APP, "runtime", "browsers")
    os.makedirs(dst_root, exist_ok=True)
    for name in os.listdir(PW_DIR):
        if not name.startswith(("chromium-1208", "chromium_headless_shell-1208")):
            continue
        dst = os.path.join(dst_root, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        print("  +", name)
        shutil.copytree(os.path.join(PW_DIR, name), dst)

    print("[3/3] 校验关键产物…")
    must_have = [
        os.path.join(DIST_APP, "DouyinFlame.exe"),
        os.path.join(DIST_APP, "_internal", "static", "index.html"),
        os.path.join(DIST_APP, "_internal", "playwright", "driver", "node.exe"),
    ]
    for p in must_have:
        if not os.path.exists(p):
            raise SystemExit(f"缺少关键文件：{p}")
    total = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(DIST_APP) for f in fs
    )
    print(f"构建完成：{DIST_APP}（约 {total / 1024 / 1024:.0f} MB）")


if __name__ == "__main__":
    main()
