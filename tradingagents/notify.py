"""轻量通知模块。

支持企业微信机器人、飞书机器人和通用 JSON Webhook。
仅使用标准库 urllib，避免额外依赖。

可靠性约定
----------
* **重试**：网络异常、5xx、429 会按指数退避 + 抖动重试；其它 4xx 不重试。
* **业务码校验**：企业微信返回 ``{"errcode": 0}``、飞书返回 ``{"code": 0}``
  才算成功。HTTP 200 但业务码非 0（例如企业微信 93000=webhook 无效）
  一律判定为失败。
* **密钥脱敏**：webhook URL 本身就是 Bearer Token，返回值、日志、异常文本里
  只允许出现脱敏后的 URL（见 :func:`redact_url` / :func:`scrub_secrets`）。
* **失败告警**：最终投递失败会写入死信列表并回调注册的失败处理函数，
  避免"webhook 挂了但没人知道"。

环境变量：
  - ``WECOM_WEBHOOK_URL``
  - ``FEISHU_WEBHOOK_URL``
  - ``GENERIC_WEBHOOK_URL``
  - ``NOTIFY_MAX_ATTEMPTS``（默认 3）
  - ``NOTIFY_BACKOFF_SECONDS``（默认 0.5）
  - ``NOTIFY_BACKOFF_MAX_SECONDS``（默认 8.0）
  - ``NOTIFY_TIMEOUT_SECONDS``（默认 10）
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.5
DEFAULT_BACKOFF_MAX = 8.0
DEFAULT_JITTER = 0.5
DEFAULT_MIN_INTERVAL = 0.0

# 业务层面的"临时性"错误码（限流等），可以重试
_TRANSIENT_BUSINESS_CODES = {
    "wecom": {45009},  # 接口调用超限
    "feishu": {9499},  # 请求过于频繁
    "generic": set(),
}

_FAILURE_HANDLERS: list[Callable[[dict[str, Any]], None]] = []
_DEAD_LETTERS: list[dict[str, Any]] = []
_MAX_DEAD_LETTERS = 50
# 每个渠道上一次投递的时间戳（用于限流）
_LAST_SENT: dict[str, float] = {}


# ------------------------------------------------------------------ 脱敏


def redact_url(url: str) -> str:
    """返回可以安全打印的 URL：只保留协议/主机，路径末段与查询参数全部打码。

    >>> redact_url("https://example.com/hook/abc123?key=zzz")
    'https://example.com/hook/***?key=***'
    """
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except Exception:
        return "***"
    if not parts.scheme and not parts.netloc:
        return "***"
    segments = [seg for seg in (parts.path or "").split("/") if seg]
    if segments:
        segments[-1] = "***"
    path = "/" + "/".join(segments) if segments else ""
    query = ""
    if parts.query:
        try:
            names = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        except Exception:
            names = []
        if names:
            query = "?" + "&".join(f"{name}=***" for name, _ in names)
    return f"{parts.scheme}://{parts.netloc}{path}{query}"


def _secret_fragments(url: str) -> list[str]:
    """从 URL 中提取可能构成密钥的片段。"""
    fragments: list[str] = []
    if not url:
        return fragments
    try:
        parts = urllib.parse.urlsplit(str(url))
    except Exception:
        return fragments
    try:
        for _, value in urllib.parse.parse_qsl(
            parts.query, keep_blank_values=True
        ):
            if value and len(value) >= 4:
                fragments.append(value)
    except Exception:
        pass
    segments = [seg for seg in (parts.path or "").split("/") if seg]
    if segments and len(segments[-1]) >= 4:
        fragments.append(segments[-1])
    return fragments


def scrub_secrets(text: str, url: str | None = None) -> str:
    """把文本中的 webhook URL / 密钥片段替换成脱敏形式。"""
    if not text:
        return ""
    out = str(text)
    if url:
        raw = str(url)
        if raw and raw in out:
            out = out.replace(raw, redact_url(raw))
        for fragment in _secret_fragments(raw):
            if fragment and fragment in out:
                out = out.replace(fragment, "***")
    return out


# -------------------------------------------------------------- 业务码解析


def parse_business_code(channel: str, body: str) -> tuple[Any, str | None]:
    """解析渠道的业务返回码。

    返回 ``(code, error_message)``：

    * 企业微信：``errcode`` / ``errmsg``
    * 飞书：``code`` / ``msg``
    * 通用 webhook：若响应是 JSON 且含 ``errcode``/``code`` 则取之，否则 ``None``
    """
    text = (body or "").strip()
    payload: Any = None
    if text:
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        return None, ("响应不是合法 JSON" if text else "响应为空")
    if channel == "wecom":
        if "errcode" not in payload:
            return None, "响应缺少 errcode 字段"
        code = payload.get("errcode")
        message = str(payload.get("errmsg") or "")
        return code, message or None
    if channel == "feishu":
        if "code" not in payload:
            return None, "响应缺少 code 字段"
        code = payload.get("code")
        message = str(payload.get("msg") or payload.get("message") or "")
        return code, message or None
    for key in ("errcode", "code"):
        if key in payload:
            code = payload.get(key)
            message = str(payload.get("errmsg") or payload.get("msg") or "")
            return code, message or None
    return None, None


def _evaluate(
    channel: str, status: int | None, body: str
) -> tuple[bool, Any, str | None]:
    """判断一次投递是否真正成功，返回 ``(ok, code, error)``。"""
    if status is None:
        return False, None, "无 HTTP 响应"
    try:
        status = int(status)
    except Exception:
        return False, None, f"非法 HTTP 状态: {status!r}"
    if not (200 <= status < 300):
        return False, None, f"HTTP {status}"

    code, message = parse_business_code(channel, body)
    if channel in ("wecom", "feishu"):
        if code is None:
            return False, None, message or "响应缺少业务码"
        try:
            numeric = int(code)
        except Exception:
            return False, code, f"业务码无法解析: {code!r}"
        if numeric != 0:
            detail = f"业务码 {numeric}"
            if message:
                detail = f"{detail} {message}"
            return False, numeric, detail
        return True, numeric, None

    # 通用 webhook：HTTP 成功即视为成功，除非 JSON 里带了非零业务码
    if code is not None:
        try:
            numeric = int(code)
        except Exception:
            numeric = None
        if numeric is not None and numeric != 0:
            detail = f"业务码 {numeric}"
            if message:
                detail = f"{detail} {message}"
            return False, numeric, detail
    return True, code, None


def is_delivery_ok(channel: str, status: int | None, body: str) -> bool:
    """HTTP 2xx **且** 业务码为 0 才算投递成功。"""
    ok, _code, _error = _evaluate(channel, status, body)
    return ok


# ------------------------------------------------------------------ 失败告警


def register_failure_handler(handler: Callable[[dict[str, Any]], None]) -> None:
    """注册"投递最终失败"回调（例如发邮件/写告警表通知运维）。"""
    if handler is None or handler in _FAILURE_HANDLERS:
        return
    _FAILURE_HANDLERS.append(handler)


def unregister_failure_handler(handler: Callable[[dict[str, Any]], None]) -> None:
    if handler in _FAILURE_HANDLERS:
        _FAILURE_HANDLERS.remove(handler)


def get_dead_letters() -> list[dict[str, Any]]:
    """返回最近的失败投递记录（内存死信队列）。"""
    return list(_DEAD_LETTERS)


def clear_dead_letters() -> None:
    _DEAD_LETTERS.clear()


def _throttle(channel: str, min_interval: float, sleep: Callable[[float], None]) -> None:
    """渠道级限流：保证两次投递之间至少间隔 ``min_interval`` 秒。"""
    if min_interval <= 0:
        return
    now = time.monotonic()
    last = _LAST_SENT.get(channel)
    if last is not None:
        wait = min_interval - (now - last)
        if wait > 0:
            try:
                sleep(wait)
            except Exception:  # pragma: no cover
                pass
    _LAST_SENT[channel] = time.monotonic()


def _record_failure(channel: str, title: str, result: dict[str, Any]) -> None:
    entry = {
        "channel": channel,
        "title": title,
        "status": result.get("status"),
        "code": result.get("code"),
        "attempts": result.get("attempts"),
        "error": result.get("error"),
        "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _DEAD_LETTERS.append(entry)
    if len(_DEAD_LETTERS) > _MAX_DEAD_LETTERS:
        del _DEAD_LETTERS[: len(_DEAD_LETTERS) - _MAX_DEAD_LETTERS]
    logger.error("通知投递失败: %s", entry)
    for handler in list(_FAILURE_HANDLERS):
        try:
            handler(dict(entry))
        except Exception:  # pragma: no cover - 回调异常不能影响主流程
            logger.exception("通知失败回调执行异常")


# ------------------------------------------------------------------ 投递


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "").strip() or default))
    except Exception:
        return default


def _is_transient_status(status: int | None) -> bool:
    if status is None:
        return True  # 网络层异常
    if status == 429:
        return True
    return status >= 500


def _is_transient_business(channel: str, code: Any) -> bool:
    try:
        numeric = int(code)
    except Exception:
        return False
    return numeric in _TRANSIENT_BUSINESS_CODES.get(channel, set())


def _backoff_delay(
    attempt: int,
    backoff: float,
    backoff_max: float,
    jitter: float,
    jitter_fn: Callable[[float, float], float],
) -> float:
    delay = min(backoff * (2 ** max(0, attempt - 1)), backoff_max)
    if jitter:
        try:
            delay += jitter_fn(0.0, delay * jitter)
        except Exception:  # pragma: no cover
            pass
    return max(0.0, delay)


def _attempt(
    url: str, payload: dict[str, Any], timeout: float
) -> tuple[int | None, str, str | None]:
    """执行一次 HTTP 投递，返回 ``(status, body, error)``。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
        return status, body, None
    except urllib.error.HTTPError as exc:
        status = getattr(exc, "code", None)
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        message = scrub_secrets(f"HTTP {status}", url)
        return status, body, message
    except Exception as exc:
        message = scrub_secrets(f"{type(exc).__name__}: {exc}", url)
        return None, "", message


def _post_json(
    url: str,
    payload: dict[str, Any],
    channel: str = "generic",
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    jitter: float = DEFAULT_JITTER,
    sleep: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
) -> dict[str, Any]:
    """投递一次消息（带重试），返回结构化结果。

    结果结构：``{"ok", "status", "code", "attempts", "error"}``，不含任何密钥。
    """
    attempts = 0
    last_status: int | None = None
    last_code: Any = None
    last_error: str | None = None

    while attempts < max(1, int(max_attempts)):
        attempts += 1
        status, body, error = _attempt(url, payload, timeout)
        if error is None:
            ok, code, biz_error = _evaluate(channel, status, body)
            if ok:
                return {
                    "ok": True,
                    "status": status,
                    "code": code,
                    "attempts": attempts,
                    "error": None,
                }
            last_status, last_code, last_error = status, code, biz_error
            if not _is_transient_business(channel, code):
                break
        else:
            last_status, last_error = status, error
            if not _is_transient_status(status):
                break
        if attempts < max(1, int(max_attempts)):
            delay = _backoff_delay(attempts, backoff, backoff_max, jitter, jitter_fn)
            try:
                sleep(delay)
            except Exception:  # pragma: no cover
                pass

    return {
        "ok": False,
        "status": last_status,
        "code": last_code,
        "attempts": attempts,
        "error": scrub_secrets(last_error or "投递失败", url),
    }


def _channel_payloads(title: str, text: str) -> dict[str, tuple[str, dict[str, Any]]]:
    content = f"{title}\n\n{text}"
    return {
        "wecom": (
            "WECOM_WEBHOOK_URL",
            {"msgtype": "markdown", "markdown": {"content": content}},
        ),
        "feishu": (
            "FEISHU_WEBHOOK_URL",
            {"msg_type": "text", "content": {"text": content}},
        ),
        "generic": (
            "GENERIC_WEBHOOK_URL",
            {"title": title, "text": text},
        ),
    }


def send_notification(
    title: str,
    text: str,
    *,
    max_attempts: int | None = None,
    backoff: float | None = None,
    backoff_max: float | None = None,
    jitter: float | None = None,
    timeout: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
) -> dict[str, Any]:
    """按配置发送通知，返回每个渠道的发送结果。

    返回结构保持向后兼容：顶层 key 仍然是渠道名（``wecom``/``feishu``/``generic``），
    没有配置任何渠道时返回 ``{"skipped": ...}``。每个渠道的值包含
    ``ok`` / ``status`` / ``code`` / ``attempts`` / ``error``，**不包含**原始响应体
    与 webhook URL。
    """
    results: dict[str, Any] = {}
    min_interval = _env_float("NOTIFY_MIN_INTERVAL_SECONDS", DEFAULT_MIN_INTERVAL)
    options = {
        "timeout": (
            timeout
            if timeout is not None
            else _env_float("NOTIFY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
        ),
        "max_attempts": (
            max_attempts
            if max_attempts is not None
            else _env_int("NOTIFY_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
        ),
        "backoff": (
            backoff
            if backoff is not None
            else _env_float("NOTIFY_BACKOFF_SECONDS", DEFAULT_BACKOFF)
        ),
        "backoff_max": (
            backoff_max
            if backoff_max is not None
            else _env_float("NOTIFY_BACKOFF_MAX_SECONDS", DEFAULT_BACKOFF_MAX)
        ),
        "jitter": (
            jitter if jitter is not None else _env_float("NOTIFY_JITTER", DEFAULT_JITTER)
        ),
        "sleep": sleep,
        "jitter_fn": jitter_fn,
    }

    for channel, (env_name, payload) in _channel_payloads(title, text).items():
        url = (os.getenv(env_name) or "").strip()
        if not url:
            continue
        _throttle(channel, min_interval, sleep)
        result = _post_json(url, payload, channel, **options)
        results[channel] = result
        if not result.get("ok"):
            _record_failure(channel, title, result)
        else:
            logger.info(
                "通知投递成功: %s (status=%s, attempts=%s)",
                channel,
                result.get("status"),
                result.get("attempts"),
            )

    if not results:
        results["skipped"] = "未配置任何通知渠道"
    return results


def build_report_notification(report: dict) -> tuple[str, str]:
    """把分析结果拼成通知标题与正文。

    WHY 优先用人话结论：
    通知（钉钉/企微/邮件）的读者多半不是专业读者，直接推 1500 字行话报告
    等于没人看。这里先给"结论 + 该做什么"，再附上人话理由与风险，
    最后才是原文摘录。拿不到人话结论时退回原来的纯文本行为。
    """
    title = f"{report.get('ticker', '')} {report.get('trade_date', '')} 分析完成"
    decision = report.get("decision") or "未知"
    plain = report.get("plain_summary") or {}

    if plain:
        lines = [f"**{plain.get('headline') or f'决策：{decision}'}**", ""]
        if plain.get("action"):
            lines.append(f"该怎么做：{plain['action']}")
        for item in (plain.get("do_list") or [])[:3]:
            lines.append(f"- {item}")
        for item in (plain.get("why") or [])[:3]:
            lines.append(f"· {item}")
        for item in (plain.get("risks") or [])[:2]:
            lines.append(f"⚠ {item}")
        original = str(report.get("decision_text", "") or "")
        if original:
            lines.extend(["", "完整报告：", original[:1200]])
        return title, "\n".join(lines)

    text = f"决策：**{decision}**\n\n{report.get('decision_text', '')[:1500]}"
    return title, text
