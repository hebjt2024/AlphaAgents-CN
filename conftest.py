"""pytest 全局配置。

把仓库根目录放进 ``sys.path``，让测试可以 ``import main`` / ``import cli``
等顶层模块 —— 否则 pytest 只把 ``tests/`` 目录加入路径，根目录下的模块
（例如 ``main.py``）无法被导入，导致这类代码路径实际上从未被测试覆盖。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
