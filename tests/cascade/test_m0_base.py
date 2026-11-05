import json
from pathlib import Path

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.guard import GuardViolation, ReadGuard
from document_ai.cascade.llm import LLMFormatError, MockLLM
from document_ai.cascade.trace_log import TraceLog


# 설정

def test_default_config_all_on():
    c = cfg.CascadeConfig()
    assert c.relevance and c.value_index and c.rules and c.verifier
    assert all(c.verifier_checks[k] for k in ("V1", "V2", "V3", "V4", "V5", "V6"))
    assert c.loop_k == 2 and c.mode == "full"


def test_presets_switch_one_component():
    assert cfg.preset("no_verifier").verifier is False
    assert cfg.preset("no_relevance").relevance is False
    assert cfg.preset("no_value_index").value_index is False
    assert cfg.preset("no_rules").rules is False
    assert cfg.preset("fake_feedback").mode == "fake_feedback"
    assert cfg.preset("ledger_like").mode == "ledger_like"
    assert cfg.preset("full") == cfg.from_dict(cfg.BASE)  # 프리셋 바탕: 편집 연산 + 규정 검색 hybrid k=10


def test_config_roundtrip_and_hash(tmp_path):
    c = cfg.preset("no_rules")
    p = tmp_path / "c.json"
    p.write_text(json.dumps(c.to_dict()), encoding="utf-8")
    c2 = cfg.load(p)
    assert c2 == c and c2.hash() == c.hash()
    assert c.hash() != cfg.CascadeConfig().hash()


def test_config_rejects_unknown_key_and_bad_mode():
    with pytest.raises(ValueError):
        cfg.from_dict({"relevanse": False})
    with pytest.raises(ValueError):
        cfg.from_dict({"mode": "magic"})
    with pytest.raises(ValueError):
        cfg.from_dict({"verifier_checks": {"V9": True}})


# 가드

def _tree(tmp_path):
    case = tmp_path / "pilot_data" / "v02" / "cases" / "T1_01"
    (case / "input").mkdir(parents=True)
    (case / "gold").mkdir()
    (case / "input" / "request.txt").write_text("요청", encoding="utf-8")
    (case / "gold" / "impact.json").write_text("{}", encoding="utf-8")
    (case / "meta.json").write_text("{}", encoding="utf-8")
    sites = tmp_path / "pilot_data" / "v02" / "docs"
    sites.mkdir(parents=True)
    (sites / "sites.json").write_text("{}", encoding="utf-8")
    return case


def test_guard_allows_input_and_logs(tmp_path):
    case = _tree(tmp_path)
    g = ReadGuard([case / "input"])
    assert g.read_text(case / "input" / "request.txt") == "요청"
    assert g.files_read() == [str((case / "input" / "request.txt").resolve())]


def test_guard_blocks_gold_meta_and_sites(tmp_path):
    case = _tree(tmp_path)
    g = ReadGuard([case / "input"])
    for p in (case / "gold" / "impact.json", case / "meta.json",
              tmp_path / "pilot_data" / "v02" / "docs" / "sites.json"):
        with pytest.raises(GuardViolation):
            g.read_text(p)
    assert len(g.violations()) == 3


def test_guard_denylist_wins_even_inside_allowed_root(tmp_path):
    case = _tree(tmp_path)
    g = ReadGuard([case])  # 케이스 폴더 전체를 허용해도 gold는 막는다
    with pytest.raises(GuardViolation):
        g.read_text(case / "gold" / "impact.json")
    with pytest.raises(GuardViolation):
        g.read_text(case / "input" / ".." / "gold" / "impact.json")


def test_guard_audit_hook_catches_plain_open(tmp_path):
    case = _tree(tmp_path)
    g = ReadGuard([case / "input"])
    with g.active():
        with pytest.raises(GuardViolation):
            open(case / "gold" / "impact.json", encoding="utf-8").read()
        with open(case / "input" / "request.txt", encoding="utf-8") as f:
            assert f.read() == "요청"
    # 비활성 상태에서는 간섭하지 않는다(채점 코드 등)
    assert open(case / "gold" / "impact.json", encoding="utf-8").read() == "{}"
    assert str((case / "input" / "request.txt").resolve()) in g.files_read()


def test_guard_denies_legacy_and_new_pilot_paths():
    g = ReadGuard([])
    for rel in ("pilot_data/gen/facts.py", "pilot_data/verifier/v0.py", "pilot_data/v03/docs/gold_spec.md",
                "pilot_data/v02/product/facts.json", "pilot_data/experiments/verifier.py",
                "pilot_data/v02/build/make_cases.py", "pilot_data/lib/sites.py", "pilot_data/v03/spec.md",
                "pilot_data/v03/products/P1/docs/sites.json", "pilot_data/v02/cases/index.json",
                "pilot_data/results/run1/per_case.jsonl"):
        assert g.denied(Path("/x") / rel), rel


# LLM

def test_mock_llm_returns_fixed_and_counts():
    m = MockLLM([{"edits": []}])
    out, usage = m.chat_json([{"role": "user", "content": "x"}], {"type": "object"}, name="t")
    assert out == {"edits": []} and usage["calls"] == 1
    assert m.calls[0]["name"] == "t"


def test_mock_llm_format_retry_once_then_fail():
    schema = {"type": "object", "required": ["edits"], "properties": {"edits": {"type": "array"}}}
    m = MockLLM(["not json", {"edits": [1]}])
    out, usage = m.chat_json([{"role": "user", "content": "x"}], schema, name="t")
    assert out == {"edits": [1]} and usage["format_retry"] is True and usage["calls"] == 2
    m2 = MockLLM(["bad", "still bad"])
    with pytest.raises(LLMFormatError):
        m2.chat_json([{"role": "user", "content": "x"}], schema, name="t")


def test_llm_request_body_fields():
    from document_ai.cascade.llm import LLMClient
    c = LLMClient(base_url="http://127.0.0.1:1/v1", model="m", temperature=0.0, seed=7, max_tokens=99)
    body = c.request_body([{"role": "user", "content": "x"}], {"type": "object"}, "n")
    assert body["seed"] == 7 and body["temperature"] == 0.0 and body["max_tokens"] == 99
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["type"] == "json_schema"


# 로그

def test_trace_log_format(tmp_path):
    t = TraceLog("T1_01", config_hash="abc")
    t.stage("locator", candidates=12, relevant=5)
    t.llm("locator", tokens_in=100, tokens_out=20, seconds=1.5)
    t.llm("editor", tokens_in=300, tokens_out=80, seconds=2.0)
    t.round(0, issues=3)
    t.round(1, issues=0)
    t.files(["/a/input/MDSR.docx"])
    d = t.to_dict()
    for k in ("case_id", "config_hash", "stages", "llm_calls", "tokens_in", "tokens_out", "rounds", "files_read",
              "violations", "seconds"):
        assert k in d
    assert d["llm_calls"] == 2 and d["tokens_in"] == 400 and d["rounds"][1]["issues"] == 0
    p = t.write(tmp_path / "log.json")
    assert json.loads(p.read_text(encoding="utf-8"))["case_id"] == "T1_01"
