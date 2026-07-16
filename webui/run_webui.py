"""
TradingAgents Web UI 启动入口

启动方式：
  python webui/run_webui.py

访问地址：
  http://localhost:8000

功能：
  - 左侧面板：配置分析参数（股票代码、日期、模型等）
  - 右侧面板：实时显示分析进度和日志
  - 底部结果面板：显示最终决策和各项报告
"""

import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("  TradingAgents Web UI")
    print("  多智能体金融交易分析系统")
    print("=" * 60)
    print()
    print("  启动服务器: http://localhost:8000")
    print("  按 Ctrl+C 停止服务")
    print()
    uvicorn.run(
        "webui.backend.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )