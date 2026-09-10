"""PyInstaller 打包入口。

启动矩阵：
- 手动双击（无参数）：起服务，2.5s 后自动打开控制台网页；
  若 8765 已有实例在跑则直接打开网页后退出，避免端口冲突双开
- 开机自启（--autostart，由安装包写进注册表 Run 键）：静默起服务，
  不弹浏览器；调度器启动时的补发逻辑自动完成当天欠账

PLAYWRIGHT_BROWSERS_PATH 必须在 playwright 被导入前生效，
指向 exe 同级的 runtime\\browsers（安装包自带内核，目标机器零下载）。
"""
import multiprocessing
import os
import socket
import sys
import threading
import webbrowser

multiprocessing.freeze_support()

if getattr(sys, "frozen", False):
    # 无控台（--noconsole）双击/开机自启时 sys.stdout/stderr 为 None，
    # uvicorn 日志 formatter 调 sys.stdout.isatty() 会直接崩溃
    # （AttributeError: 'NoneType' object has no attribute 'isatty'）。
    # 用 devnull 流兜底，isatty() 返回 False 走无色分支，print 也不再炸。
    # 注意：从终端启动时进程有管道句柄，stdout 不是 None，
    # 这正是打包后终端测试通过、双击却崩的原因
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    _APP_DIR = os.path.dirname(sys.executable)
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", os.path.join(_APP_DIR, "runtime", "browsers")
    )

import uvicorn

from app.main import app

URL = "http://127.0.0.1:8765"
AUTOSTART = "--autostart" in sys.argv


def _already_running() -> bool:
    s = socket.socket()
    try:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", 8765)) == 0
    finally:
        s.close()


if __name__ == "__main__":
    if _already_running():
        if not AUTOSTART:
            webbrowser.open(URL)
        sys.exit(0)
    if getattr(sys, "frozen", False) and not AUTOSTART:
        threading.Timer(2.5, lambda: webbrowser.open(URL)).start()
    # log_config=None：跳过 uvicorn 的 dictConfig，从根上绕开
    # "Unable to configure formatter 'default'" 这类日志配置崩溃，
    # 无控台模式下 uvicorn 终端日志本来也无人可见（控制台网页另有一套日志）
    uvicorn.run(app, host="127.0.0.1", port=8765, log_config=None)
