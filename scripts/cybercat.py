#!/usr/bin/env python3
"""cybercat 启动器：启动 SFTP Manager 并自动打开浏览器。

行为：
- 若 9000 端口已被本服务占用 → 仅打开浏览器，不重复启动。
- 否则启动 uvicorn（前台阻塞，Ctrl+C 停止），延迟打开浏览器。
"""
import os
import socket
import sys
import time
import webbrowser

HOST = "127.0.0.1"
PORT = int(os.environ.get("CYBERCAT_PORT", "9000"))
URL = f"http://{HOST}:{PORT}"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _open_browser_delayed():
    time.sleep(1.2)
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def main():
    # 确保在项目目录运行，以便 import app
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    os.chdir(PROJECT_DIR)

    if _port_in_use(HOST, PORT):
        print(f"SFTP Manager 已在运行: {URL}")
        webbrowser.open(URL)
        return 0

    import threading
    threading.Thread(target=_open_browser_delayed, daemon=True).start()

    import uvicorn
    from app.main import app
    print(f"启动 SFTP Manager ...\n  地址: {URL}\n  停止: 按 Ctrl+C")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
