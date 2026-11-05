"""P12: FROZEN.json 범위(v3부터 코드 해시 포함). 설정·프롬프트·규칙 표(curated, extracted)·검사 명세·도메인 팩·연산 스키마·
검색 설정·코퍼스 해시·패키지 코드 해시."""

import json

import pytest

from document_ai.cascade import freeze

from .test_p6_retrieval import need_corpus


@need_corpus
def test_snapshot_v2_scope():
    s = freeze.snapshot()
    assert s["version"] == "frozen_v3" and len(s["code_sha256"]) == 64
    files = set(s["files"])
    assert {"domain_packs/ec_sw/pack.yaml", "domain_packs/ec_sw/rules_table.yaml",
            "domain_packs/ec_sw/rules_table_extracted.yaml", "domain_packs/generic_text/pack.yaml",
            "verifier_spec.md", "prices.yaml"} <= files
    for p in ("locator_v2", "editor_v1", "editor_ops_v1", "intent_v1", "intent_v2", "rule_extract_v1"):
        assert f"prompts/{p}.txt" in files
    c = s["config"]
    for k in ("graph_mode", "request_mode", "rules_source", "link_threshold", "sim_threshold", "retriever",
              "retrieval_k", "edit_mode"):
        assert k in c
    assert s["ops_schema_sha256"] and s["retrieval"]["embed_model"] == "BAAI/bge-m3"
    assert len(s["retrieval"]["embed_revision"]) == 40
    assert len(s["corpus"]["sha256"]) == 64 and s["corpus"]["sha256"] == s["corpus"]["fixed"]


@need_corpus
def test_check_detects_corpus_change(monkeypatch):
    s = freeze.snapshot()
    monkeypatch.setattr(freeze, "snapshot", lambda note="": {**s, "corpus": {**s["corpus"], "sha256": "0" * 64}})
    monkeypatch.setattr(freeze, "_frozen", lambda: s)
    assert any("corpus" in d for d in freeze.check())


@need_corpus
def test_check_detects_code_change(monkeypatch):
    s = freeze.snapshot()
    monkeypatch.setattr(freeze, "snapshot", lambda note="": {**s, "code_sha256": "0" * 64})
    monkeypatch.setattr(freeze, "_frozen", lambda: s)
    assert "code_sha256 changed" in freeze.check()
