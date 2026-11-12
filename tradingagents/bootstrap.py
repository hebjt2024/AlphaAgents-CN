"""在导入读取环境变量的模块之前加载 .env。

Python 在 import 阶段执行模块代码。default_config 会在导入时读取
TRADINGAGENTS_* 环境变量，因此需要保证 .env 已被加载。
"""

from dotenv import load_dotenv

load_dotenv()
