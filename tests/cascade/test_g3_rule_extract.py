"""G3: 규칙 자동 추출. 결과가 rules_table 스키마를 통과, 조항 ID가 코퍼스에 실제로 있음, rules_source별 경로."""

import csv
import json
from pathlib import Path

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade import pack as pack_mod
from document_ai.cascade import rule_extract as RX
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.run import run_case

from .fixture_docs import build_case
from .test_p6_retrieval import CORPUS, T2, need_corpus

GOOD = {"rules": [
    {"attr": "stores_personal_info", "attr_ko": "개인식별정보 저장", "equals": False, "secs": ["DC-02"], "result": "N/A",
     "clause_ids": ["첨부3-DC-02-예외"]},
    {"attr": "is_samd", "attr_ko": "소프트웨어 의료기기", "equals": True, "secs": ["SI-10", "SI-11"], "result": "N/A",
     "clause_ids": ["첨부3-SI-10-예외", "첨부3-SI-11-예외"]},
    {"attr": "bad", "attr_ko": "없는 조항", "equals": True, "secs": ["IA-99"], "result": "N/A",
     "clause_ids": ["첨부3-IA-99-예외"]},
    {"attr": "bad2", "attr_ko": "결과 틀림", "equals": True, "secs": ["IA-05"], "result": "maybe",
     "clause_ids": ["첨부3-IA-05-예외"]}]}


@need_corpus
def test_extract_validates_schema_and_clause_ids(tmp_path):
    llm = MockLLM([GOOD])
    doc, report = RX.extract(pack_mod.load("ec_sw"), llm, out_dir=tmp_path)
    assert doc["version"] == "rules_table_extracted"
    assert [r["attr"] for r in doc["rules"]] == ["stores_personal_info", "is_samd"]
    assert {d["attr"] for d in report["dropped"]} == {"bad", "bad2"}
    ids = {json.loads(l)["clause_id"] for l in (CORPUS / "regs.jsonl").read_text(encoding="utf-8").splitlines()}
    for r in doc["rules"]:
        RX.validate_rule(r, ids)  # 예외 없이 통과
        assert r["id"].startswith("X")
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "첨부3-DC-02-예외" in prompt and "첨부4" not in prompt  # 적용 예외 청크만
    written = pack_mod.read_commented_json(tmp_path / "rules_table_extracted.yaml")
    assert written["rules"] == doc["rules"] and written["decision_cells"]
    rows = list(csv.DictReader((tmp_path / "rules_review.csv").open(encoding="utf-8")))
    assert len(rows) == 2 and {"rule", "clause_ids", "clause_text", "review"} <= set(rows[0])


def test_compare_rule_units():
    cur = [{"attr": "stores_pii", "equals": False, "secs": ["DC-02"], "result": "N/A"},
           {"attr": "samd", "equals": True, "secs": ["SI-10", "SI-11"], "result": "N/A"},
           {"attr": "password_auth", "equals": False, "secs": ["IA-05"], "result": "N/A"}]
    ext = [{"attr": "personal", "equals": False, "secs": ["DC-02"], "result": "N/A"},
           {"attr": "samd_x", "equals": True, "secs": ["SI-10"], "result": "N/A"}]
    c = RX.compare(cur, ext)
    assert c["sec_level"]["precision"] == 1.0 and c["sec_level"]["recall"] == pytest.approx(2 / 4)
    assert c["exact_rules"] == 1


def test_rules_source_flag_and_paths(tmp_path):
    assert cfg.CascadeConfig().rules_source == "curated"
    with pytest.raises(ValueError):
        cfg.from_dict({"rules_source": "magic"})
    pk = pack_mod.load("ec_sw")
    assert RX.with_rules_source(pk, "curated").rules_doc()["version"] == "rules_table_v2"
    assert RX.with_rules_source(pk, "retrieval_only").rules_doc()["rules"] == []
    ext = RX.with_rules_source(pk, "extracted")
    assert ext.rules_file == "rules_table_extracted.yaml"


@need_corpus
def test_retrieval_only_runs_llm_path_with_evidence(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T2)
    seen = []

    def fn(m, n):
        seen.append((n, m))
        if n == "locator":
            lines = [l for l in m[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": "DC-02" in l, "reason": "t"} for i, l in enumerate(lines)]}
        return {"edits": []}
    conf = cfg.from_dict({"rules_source": "retrieval_only", "retrieval": True, "retriever": "bm25",
                          "edit_mode": "free_json"})
    pred, log = run_case(inp, conf, llm=MockLLM(fn), work_dir=tmp_path / "w")
    assert log["locator"]["rule_info"]["fallback"] == "no_rule_for_attr"
    assert "관련 규정 근거" in [m for n, m in seen if n == "locator"][0][-1]["content"]
