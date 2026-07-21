"""
TradingAgents Web UI 启动脚本

用法：
    python webui/run_webui.py              # 默认端口 8000
    python webui/run_webui.py --port 8080  # 指定端口
    python webui/run_webui.py --reload     # 开发模式（热重载）
"""

import sys
import os
import argparse

# 确保项目根目录在 Python 路径中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingAgents Web UI")
    parser.add_argument("--port", type=int, default=8000, help="服务端口（默认 8000）")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    import uvicorn

    print("=" * 58)
    print("  TradingAgents - 多智能体金融交易分析系统")
    print("=" * 58)
    print(f"  Web UI 服务启动中...")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  模式: {'开发模式（热重载）' if args.reload else '生产模式'}")
    print(f"  停止: Ctrl+C")
    print("=" * 58)

    uvicorn.run(
        "webui.backend.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )