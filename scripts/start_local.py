"""Run the local dashboard with a small, safe preflight."""

import argparse
import sqlite3
import socket
import threading
import time
import webbrowser

from app.config import settings
from app.database import check_database
from app.session_storage import SQLiteConversationStore


def preflight() -> tuple[bool, bool, list[str]]:
    messages = []
    storage_ready = True
    database = check_database()
    if database.get("ok"):
        messages.append(f"数据库：已连接（{database['database']}）")
    else:
        # Driver errors may include connection parameters; do not print them.
        messages.append("数据库：未连接，请检查 .env 中的 DATABASE_URL 与 PostgreSQL 服务")
    messages.append("模型密钥：已配置" if settings.dashscope_api_key else
                    "模型密钥：未配置，问数功能需要设置 DASHSCOPE_API_KEY")
    if settings.session_storage == "sqlite":
        try:
            SQLiteConversationStore(settings.session_store_path,
                                    ttl_seconds=settings.session_ttl_seconds,
                                    max_sessions=settings.session_max_saved)
            messages.append("会话记忆：本机持久化已就绪")
        except (OSError, ValueError, sqlite3.Error) as exc:
            storage_ready = False
            messages.append(f"会话记忆：不可用（{type(exc).__name__}），请检查存储目录权限")
    elif settings.session_storage == "memory":
        messages.append("会话记忆：仅内存，服务重启后失效")
    else:
        storage_ready = False
        messages.append("会话记忆：SESSION_STORAGE 只支持 sqlite 或 memory")
    messages.append("运行模式：本机访问（127.0.0.1），单进程")
    return bool(database.get("ok") and settings.dashscope_api_key), storage_ready, messages


def open_when_ready(port: int) -> None:
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
        except OSError:
            time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机 NL2SQL 问数页面")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true", help="仅检查本机配置，不启动服务")
    parser.add_argument("--open-browser", action="store_true", help="就绪后打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    ready, storage_ready, messages = preflight()
    for message in messages:
        print(message)
    if args.check:
        return 0 if ready and storage_ready else 1
    if not storage_ready:
        print("会话存储不可写，服务未启动。请检查 SESSION_STORE_PATH。")
        return 1
    print(f"问数页面：http://127.0.0.1:{args.port}/")
    if not ready:
        print("配置未就绪，管理页面仍可打开；问数可能失败。")
    if args.open_browser:
        threading.Thread(target=open_when_ready, args=(args.port,), daemon=True).start()
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, reload=False, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
