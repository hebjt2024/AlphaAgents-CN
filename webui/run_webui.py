"""
TradingAgents Web UI 启动脚本

用法：
    python webui/run_webui.py              # 默认端口 8000
    python webui/run_webui.py --port 8080  # 指定端口
    python webui/run_webui.py --reload     # 开发模式（热重载）
"""

import argparse
import logging
import os
import sys

# 确保项目根目录在 Python 路径中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

os.makedirs(os.path.join(project_root, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(project_root, "logs", "webui.log"),
            encoding="utf-8",
        ),
    ],
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingAgents Web UI")
    parser.add_argument("--port", type=int, default=8000, help="服务端口（默认 8000）")
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("WEBUI_HOST") or "127.0.0.1",
        help="绑定地址（默认 127.0.0.1；需要局域网访问时显式设为 0.0.0.0）",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()
    # 让启动告警看到真实绑定地址，而不是 .env 里写的值。
    os.environ["WEBUI_HOST"] = str(args.host)
    os.environ["WEBUI_PORT"] = str(args.port)

    import uvicorn

    print("=" * 58)
    print("  TradingAgents - 多智能体金融交易分析系统")
    print("=" * 58)
    print("  Web UI 服务启动中...")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  模式: {'开发模式（热重载）' if args.reload else '生产模式'}")
    print("  停止: Ctrl+C")
    print("=" * 58)

    uvicorn.run(
        "webui.backend.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
