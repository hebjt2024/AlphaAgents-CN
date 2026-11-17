"""LLM 响应内容规范化：必须保留"无 type 字段"的文本块。

MiniMax 的 Anthropic 兼容端点返回扩展思考时，content 是块列表：
``[{"type": "thinking", ...}, {"text": "正文"}]``，正文块没有 type 字段。
旧实现只认 ``{"type": "text"}``，会把正文丢掉，整份报告变空。
"""

from types import SimpleNamespace

from tradingagents.llm_clients.anthropic_client import _is_reasoning_only
from tradingagents.llm_clients.base_client import normalize_content


def _response(content, stop_reason="end_turn"):
    return SimpleNamespace(
        content=content,
        response_metadata={"stop_reason": stop_reason},
    )


def test_plain_string_is_unchanged():
    response = _response("正常文本")
    assert normalize_content(response).content == "正常文本"


def test_text_block_with_type():
    response = _response([{"type": "text", "text": "第一段"}])
    assert normalize_content(response).content == "第一段"


def test_text_block_without_type_is_kept():
    response = _response([{"text": "没有 type 的正文"}])
    assert normalize_content(response).content == "没有 type 的正文"


def test_thinking_block_is_dropped_and_text_kept():
    response = _response(
        [
            {"type": "thinking", "thinking": "内部推理"},
            {"text": "最终结论"},
        ]
    )
    assert normalize_content(response).content == "最终结论"


def test_object_with_text_attribute_is_kept():
    block = SimpleNamespace(text="对象文本")
    assert normalize_content(_response([block])).content == "对象文本"


def test_reasoning_only_detection():
    thinking_only = _response(
        [{"type": "thinking", "thinking": "想了很久"}], stop_reason="max_tokens"
    )
    assert _is_reasoning_only(thinking_only) is True
    with_text = _response(
        [{"type": "thinking", "thinking": "想"}, {"text": "正文"}]
    )
    assert _is_reasoning_only(with_text) is False
