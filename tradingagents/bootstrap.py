"""在导入读取环境变量的模块之前加载 .env。

Python 在 import 阶段执行模块代码。default_config 会在导入时读取
TRADINGAGENTS_* 环境变量，因此需要保证 .env 已被加载。

同时在这里统一关掉第三方库的进度条输出
--------------------------------------
AkShare 内部大量使用 tqdm，每次取数都会往 stderr 打一行进度条。
对本项目来说这些进度条毫无价值 —— 调用是后台批量执行的，用户看不到
"进度"，只会让服务端日志被 `0%| | 0/16 [00:00<?, ?it/s]` 刷屏，
把真正的告警淹没掉。

如需临时查看进度条，设置环境变量 ``TA_SHOW_PROGRESS=1``。
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _silence_tqdm() -> None:
    """把 tqdm 替换成静默实现。

    必须在 akshare 首次导入**之前**执行：akshare 在模块级执行
    ``from tqdm import tqdm``，之后绑定就固定了，再改模块属性无效。
    ``tradingagents/dataflows/akshare_adapter._require_akshare()`` 是
    延迟导入的，因此本函数在包初始化阶段运行即可覆盖它。
    """
    if os.getenv("TA_SHOW_PROGRESS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return

    class _SilentTqdm:
        """最小可用的 tqdm 替身：可迭代、可作上下文、方法全部空转。"""

        def __init__(self, iterable=None, *args, **kwargs):
            self.iterable = iterable

        def __iter__(self):
            return iter(self.iterable) if self.iterable is not None else iter(())

        def __len__(self):
            try:
                return len(self.iterable)  # type: ignore[arg-type]
            except Exception:
                return 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __getattr__(self, name):
            # update / close / set_description / write / refresh …
            # 一律返回空操作，避免第三方库调用不存在的方法时报错。
            return lambda *a, **k: None

        def update(self, *args, **kwargs):
            return None

        def close(self):
            return None

        def write(self, *args, **kwargs):
            return None

    try:
        import tqdm as _tqdm

        _tqdm.tqdm = _SilentTqdm
        try:
            import tqdm.auto as _tqdm_auto

            _tqdm_auto.tqdm = _SilentTqdm
        except Exception:
            pass
        try:
            import tqdm.std as _tqdm_std

            _tqdm_std.tqdm = _SilentTqdm
        except Exception:
            pass
    except Exception:  # tqdm 未安装时忽略
        logger.debug("tqdm 静默失败（可能未安装）", exc_info=True)


_silence_tqdm()
