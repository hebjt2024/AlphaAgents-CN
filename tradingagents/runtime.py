"""进程级运行时共享对象。"""

import threading

# 分析任务串行锁：TradingAgents 的配置与记忆是进程级全局状态，
# 多个分析并发执行会互相覆盖数据源配置，因此统一串行化。
ANALYSIS_LOCK = threading.Lock()
