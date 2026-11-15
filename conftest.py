"""pytest 全局配置。

把仓库根目录放进 ``sys.path``，让测试可以 ``import main`` / ``import cli``
等顶层模块 —— 否则 pytest 只把 ``tests/`` 目录加入路径，根目录下的模块
（例如 ``main.py``）无法被导入，导致这类代码路径实际上从未被测试覆盖。
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试强制关闭本地行情缓存：很多用例会用合成 CSV 反复请求同一标的/日期，
# 若写入真实 data/market.db 会互相污染。需要验证缓存的用例自行显式开启，
# 并把 TA_MARKET_DB 指向临时文件。这里强制覆盖，而不是 setdefault ——
# 否则开发者 shell 里的 TA_MARKET_CACHE=1 会让测试写真实缓存。
os.environ["TA_MARKET_CACHE"] = "0"
os.environ["TA_MARKET_DB"] = str(
    Path(tempfile.mkdtemp(prefix="alphaagents-test-market-")) / "market.db"
)
