"""HPC Copilot 后端一键启动脚本。

关键：所有 uvicorn WebSocket 相关的坑都在这里固化，别再手动敲 --ws-ping-interval 0 之类的参数。

用法：
    python run.py                # 生产模式（推荐，agent 长循环稳定）
    python run.py --dev          # 开发模式（--reload，改代码自动重启；agent 长任务会被打断）
    python run.py --host 0.0.0.0 --port 8000  # 显式指定监听地址

为什么关掉 uvicorn 内建 ws ping：
    uvicorn 默认每 20s 发一个协议层 PING 帧，20s 内没收到 PONG 就单方面关闭 TCP，
    ASGI 层收到 close code=1005（"无状态码"）。浏览器标签切后台 / Chrome 节流 setInterval /
    事件循环稍微一卡，PONG 就会晚到，触发误杀。表现为「agent 跑到一半 WS 断了」。
    我们前端自己有 25s 一次的应用层 ping，够保活；协议层 ping 直接关掉最省心。
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="HPC Copilot 后端启动器")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="开发模式：启用 --reload（改代码自动重启）。注意：agent 长循环会被重启打断",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="uvicorn 日志级别，默认 info",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="全局关闭 RAG 检索（等价于 RAG_ENABLED=0），用于对比测试速度/质量",
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("❌ 未安装 uvicorn，请先运行: pip install -r requirements.txt", file=sys.stderr)
        return 1

    # 让脚本从任何目录执行都能找到 backend.main:app
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # --no-rag 等价于设置环境变量 RAG_ENABLED=0（main.py 会读取）
    if args.no_rag:
        os.environ["RAG_ENABLED"] = "0"

    config = uvicorn.Config(
        app="backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        log_level=args.log_level,
        # ── WebSocket 关键配置（生产 agent 长循环必需） ──
        ws_ping_interval=None,   # 关掉协议层 ping（uvicorn 用 None 表示禁用）
        ws_ping_timeout=None,    # 关掉协议层 ping 超时判定
        ws_max_size=16 * 1024 * 1024,  # 单帧最大 16MB，够 agent 传大输出
        timeout_keep_alive=75,   # HTTP keep-alive，>60s 常见反向代理阈值
        timeout_graceful_shutdown=10,
        # ── 其他 ──
        access_log=False,        # agent 场景访问日志噪音大，关掉
        server_header="hpc-copilot",
        date_header=True,
    )

    print("=" * 60)
    print(f"  HPC Copilot 后端启动")
    print(f"  地址:      http://{args.host}:{args.port}")
    print(f"  模式:      {'开发（--reload）' if args.dev else '生产（推荐）'}")
    print(f"  WS ping:   已禁用（前端应用层 25s ping 保活）")
    print(f"  RAG:       {'已关闭（--no-rag）' if args.no_rag else '启用'}")
    print(f"  日志级别:  {args.log_level}")
    print("=" * 60)
    if args.dev:
        print("⚠️  开发模式警告：改任何 .py 文件都会重启服务，")
        print("    agent 长循环会被打断（WS 关闭码 1012）。测试 agent 请用生产模式。")
        print()

    server = uvicorn.Server(config)
    return server.run() or 0


if __name__ == "__main__":
    sys.exit(main())
