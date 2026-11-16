"""pytest 全局配置。

把仓库根目录放进 ``sys.path``，让测试可以 ``import main`` / ``import cli``
等顶层模块 —— 否则 pytest 只把 ``tests/`` 目录加入路径，根目录下的模块
（例如 ``main.py``）无法被导入，导致这类代码路径实际上从未被测试覆盖。
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Windows 临时目录清理：默认容忍删除失败
# ---------------------------------------------------------------------------
# WHY：很多用例用 ``tempfile.TemporaryDirectory`` 承载 SQLite 数据库。Windows 上
# 只要还有任何句柄（sqlite3 的 WAL/SHM、杀毒软件扫描等）没释放，``rmtree`` 就会
# 抛 ``PermissionError: WinError 32``，随后 tempfile 会把它当成目录再删一次，
# 最终报出更难理解的 ``NotADirectoryError``。这类失败发生在用例**已经通过之后**
# 的清理阶段，纯属环境噪声，却会让整条测试链随机变红。
#
# ``ignore_cleanup_errors=True`` 是 Python 3.10+ 官方提供的开关，正好用来处理
# 这种"文件被占用导致删不掉"的场景。这里统一给测试里的 TemporaryDirectory 打开它，
# 避免逐个用例修改（调用点有 20+ 处）。
_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory


class _TolerantTemporaryDirectory(_ORIGINAL_TEMPORARY_DIRECTORY):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ignore_cleanup_errors", True)
        super().__init__(*args, **kwargs)


tempfile.TemporaryDirectory = _TolerantTemporaryDirectory


@pytest.fixture(autouse=True)
def _reset_route_caches():
    """每个用例前后清空路由层的复用缓存与熔断状态。

    WHY：``route_to_vendor`` 现在带 60 秒的结果复用缓存与限流熔断，
    它们都是**进程级**状态。如果不清理，上一个用例缓存的成功结果会让
    下一个用例"看起来拿到了数据"（但实际上没有真正调用被 mock 的实现），
    造成难以定位的测试相互污染。
    """
    try:
        from tradingagents.dataflows import interface
    except Exception:  # pragma: no cover - 个别用例可能故意让导入失败
        yield
        return
    interface.reset_route_caches()
    yield
    interface.reset_route_caches()

