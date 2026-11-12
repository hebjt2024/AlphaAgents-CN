"""轻量通知模块。

支持企业微信机器人、飞书机器人和通用 JSON Webhook。
仅使用标准库 urllib，避免额外依赖。
"""

from __future__ import annotations

import json
import os
import urllib.request


def _post_json(url: str, payload: dict) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8", errors="replace")
    return {"ok": True, "status": response.status, "body": body[:500]}


def send_notification(title: str, text: str) -> dict[str, object]:
    """
    按配置发送通知。返回每个渠道的发送结果。

    环境变量：
      - WECOM_WEBHOOK_URL
      - FEISHU_WEBHOOK_URL
      - GENERIC_WEBHOOK_URL
    """
    results: dict[str, object] = {}
    content = f"{title}\n\n{text}"

    wecom = os.getenv("WECOM_WEBHOOK_URL")
    if wecom:
        try:
            results["wecom"] = _post_json(
                wecom,
                {"msgtype": "markdown", "markdown": {"content": content}},
            )
        except Exception as exc:
            results["wecom"] = {"ok": False, "error": str(exc)}

    feishu = os.getenv("FEISHU_WEBHOOK_URL")
    if feishu:
        try:
            results["feishu"] = _post_json(
                feishu,
                {"msg_type": "text", "content": {"text": content}},
            )
        except Exception as exc:
            results["feishu"] = {"ok": False, "error": str(exc)}

    generic = os.getenv("GENERIC_WEBHOOK_URL")
    if generic:
        try:
            results["generic"] = _post_json(
                generic, {"title": title, "text": text}
            )
        except Exception as exc:
            results["generic"] = {"ok": False, "error": str(exc)}

    if not results:
        results["skipped"] = "未配置任何通知渠道"
    return results


def build_report_notification(report: dict) -> tuple[str, str]:
    title = f"{report.get('ticker', '')} {report.get('trade_date', '')} 分析完成"
    decision = report.get("decision") or "未知"
    text = f"决策：**{decision}**\n\n{report.get('decision_text', '')[:1500]}"
    return title, text
