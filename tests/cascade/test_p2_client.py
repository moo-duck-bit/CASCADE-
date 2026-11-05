"""P2: 모델 공통 클라이언트. 세 백엔드(vllm, openai, anthropic)가 같은 인터페이스로 MockTransport에서 돈다."""

import json
import os

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade import llm as llm_mod
from document_ai.cascade.llm import LLMClient, LLMFormatError, MissingKeyError, MockTransport
from document_ai.cascade.run import make_llm, run_case

from .fixture_docs import build_case
from .test_m8_loop_gate import GOOD, T1

SCHEMA = {"type": "object", "required": ["edits"], "properties": {"edits": {"type": "array", "items": {
    "type": "object", "required": ["site", "new_text"],
    "properties": {"site": {"type": "string"}, "new_text": {"type": "string"}}}}}}
OUT = {"edits": [{"site": "[MDSR | Req. 1 | 설명]", "new_text": "x"}]}
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]


def reply(backend, obj, tin=100, tout=20, finish=None):
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    if backend == "anthropic":
        return {"content": [{"type": "text", "text": text}], "stop_reason": finish or "end_turn",
                "usage": {"input_tokens": tin, "output_tokens": tout}}
    return {"choices": [{"message": {"content": text}, "finish_reason": finish or "stop"}],
            "usage": {"prompt_tokens": tin, "completion_tokens": tout}}


def client(backend, responses, **kw):
    t = MockTransport([reply(backend, r) for r in responses])
    model = {"vllm": "qwen3-32b", "openai": "gpt-5", "anthropic": "claude-opus-5-5"}[backend]
    c = LLMClient("http://127.0.0.1:8100/v1", model, temperature=0.0, seed=7, max_tokens=512, backend=backend,
                  transport=t, api_key="test-key", **kw)
    return c, t


@pytest.mark.parametrize("backend", ["vllm", "openai", "anthropic"])
def test_same_interface_on_mock_transport(backend):
    c, t = client(backend, [OUT])
    obj, usage = c.chat_json(MSGS, SCHEMA, "editor")
    assert obj == OUT
    assert usage["calls"] == 1 and usage["tokens_in"] == 100 and usage["tokens_out"] == 20
    assert "cost_usd" in usage and len(t.requests) == 1


def test_vllm_body_passes_seed_temperature_tokens_thinking():
    c, t = client("vllm", [OUT, OUT], thinking=False)
    c.chat_json(MSGS, SCHEMA, "editor")
    url, headers, body = t.requests[0]
    assert url.endswith("/chat/completions")
    assert body["seed"] == 7 and body["temperature"] == 0.0 and body["max_tokens"] == 512
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["json_schema"]["schema"]["required"] == ["edits"]
    c2, t2 = client("vllm", [OUT], thinking=True)
    c2.chat_json(MSGS, SCHEMA, "editor")
    assert t2.requests[0][2]["chat_template_kwargs"] == {"enable_thinking": True}


def test_openai_body_strict_schema_and_key_header():
    c, t = client("openai", [OUT])
    c.chat_json(MSGS, SCHEMA, "editor")
    url, headers, body = t.requests[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert body["seed"] == 7 and body["temperature"] == 0.0 and body["max_completion_tokens"] == 512
    js = body["response_format"]["json_schema"]
    assert js["strict"] is True and js["schema"]["additionalProperties"] is False
    assert js["schema"]["properties"]["edits"]["items"]["additionalProperties"] is False
    assert "reasoning_effort" not in body


def test_anthropic_body_output_config_and_thinking():
    c, t = client("anthropic", [OUT])
    c.chat_json(MSGS, SCHEMA, "editor")
    url, headers, body = t.requests[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "test-key" and headers["anthropic-version"]
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "u"}]
    assert body["max_tokens"] == 512
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    # 샘플링 파라미터는 2026-10-21부터 400이라 보내지 않고, seed는 API에 없다(기록만)
    assert "temperature" not in body and "seed" not in body
    # Opus 5.5는 생각을 끌 수 없다: thinking을 빼고 effort를 낮춘다
    assert "thinking" not in body and body["output_config"]["effort"] == "low"
    for model, want in (("claude-sonnet-5-5", {"type": "between_tools"}), ("claude-haiku-5-5", {"type": "disabled"})):
        c2 = LLMClient("", model, backend="anthropic", transport=MockTransport([reply("anthropic", OUT)]),
                       api_key="k", max_tokens=512)
        c2.chat_json(MSGS, SCHEMA, "editor")
        assert c2.transport.requests[0][2]["thinking"] == want
    c3, t3 = client("anthropic", [OUT], thinking=True)
    c3.chat_json(MSGS, SCHEMA, "editor")
    assert t3.requests[0][2]["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize("backend", ["vllm", "openai", "anthropic"])
def test_schema_enforced_with_one_format_retry(backend):
    c, t = client(backend, ["not json", OUT])
    obj, usage = c.chat_json(MSGS, SCHEMA, "editor")
    assert obj == OUT and usage["format_retry"] and usage["calls"] == 2
    c, t = client(backend, [{"edits": [{"site": 3}]}, {"wrong": 1}])
    with pytest.raises(LLMFormatError):
        c.chat_json(MSGS, SCHEMA, "editor")


def test_cost_from_price_table():
    prices = llm_mod.load_prices()
    assert prices["anthropic"]["claude-opus-5-5"] == [4.0, 20.0]
    c, _ = client("anthropic", [OUT])
    _, usage = c.chat_json(MSGS, SCHEMA, "editor")
    assert usage["cost_usd"] == pytest.approx(100 * 4.0 / 1e6 + 20 * 20.0 / 1e6)
    c, _ = client("vllm", [OUT])
    assert c.chat_json(MSGS, SCHEMA, "editor")[1]["cost_usd"] == 0.0


def test_truncation_flag_per_backend():
    t = MockTransport([reply("anthropic", OUT, finish="max_tokens")])
    c = LLMClient("", "claude-opus-5-5", backend="anthropic", transport=t, api_key="k")
    assert c.chat_json(MSGS, SCHEMA, "editor")[1]["truncated"] is True


def test_missing_key_skips_real_call(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not llm_mod.backend_available("openai") and not llm_mod.backend_available("anthropic")
    assert llm_mod.backend_available("vllm")
    with pytest.raises(MissingKeyError):
        LLMClient("", "gpt-5", backend="openai")
    with pytest.raises(ValueError):
        cfg.from_dict({"backend": "magic"})


def test_config_backend_builds_client_and_run_logs_cost(tmp_path):
    conf = cfg.from_dict({"backend": "vllm", "thinking": False})
    c = make_llm(conf)
    assert c.backend == "vllm" and c.thinking is False
    from .test_m8_loop_gate import scripted
    from document_ai.cascade.llm import MockLLM
    inp = build_case(tmp_path / "c" / "input", request=T1)
    pred, log = run_case(inp, cfg.CascadeConfig(), llm=MockLLM(scripted([GOOD])), work_dir=tmp_path / "w")
    assert "cost_usd" in pred and "cost_usd" in log and all("cost_usd" in c for c in log["calls"])


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY 없음: 실제 호출 건너뜀")
def test_anthropic_live_smoke():
    c = LLMClient("", "claude-haiku-5-5", backend="anthropic", max_tokens=256)
    obj, usage = c.chat_json([{"role": "user", "content": 'JSON {"edits": []} 하나만 출력'}], SCHEMA, "smoke")
    assert "edits" in obj and usage["cost_usd"] > 0


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY 없음: 실제 호출 건너뜀")
def test_openai_live_smoke():
    c = LLMClient("", "gpt-5-mini", backend="openai", max_tokens=256)
    obj, usage = c.chat_json([{"role": "user", "content": 'JSON {"edits": []} 하나만 출력'}], SCHEMA, "smoke")
    assert "edits" in obj
