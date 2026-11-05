"""P4: 정답에서 온 설정 제거. 새 블록 문단 수는 같은 문서의 기존 Req 블록 구조에서 정한다(고정값 없음)."""

import dataclasses

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.locator import locate, template_cells
from document_ai.cascade.request import parse_request
from document_ai.cascade.trace_log import TraceLog

from . import fixture_docs
from .fixture_docs import build_case

ADD = "모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 이메일 인증 후 비밀번호를 재설정한다."


def insert_names(tmp_path, reqs=None, monkeypatch=None):
    if reqs is not None:
        monkeypatch.setattr(fixture_docs, "MDSR_REQS", reqs)
    inp = build_case(tmp_path / "case" / "input", request=ADD)
    g = CellGraph(parse_case(inp))
    res = locate(parse_request(ADD), g, cfg.from_dict({"relevance": False}), None, TraceLog("t"))
    return [c.site for c in res.candidates if c.kind == "insert"]


def test_insert_paras_setting_removed():
    names = {f.name for f in dataclasses.fields(cfg.CascadeConfig)}
    assert "insert_paras" not in names
    with pytest.raises(ValueError):
        cfg.from_dict({"insert_paras": 2})


def test_paragraph_count_from_document_structure_two_fixtures(tmp_path, monkeypatch):
    # 픽스처 1: 모바일 Req 기준 문단 2, 2, 1 → 2문단
    a = insert_names(tmp_path / "a")
    assert "[MDSR | Req. 4 | 기준#p2]" in a and "[MDSR | Req. 4 | 기준#p3]" not in a
    # 픽스처 2: 모든 Req 기준이 3문단 → 3문단
    three = {k: (v[0], v[1], v[2], [f"{v[3][0]} ({i})" for i in range(3)]) for k, v in fixture_docs.MDSR_REQS.items()}
    b = insert_names(tmp_path / "b", three, monkeypatch)
    assert "[MDSR | Req. 4 | 기준#p3]" in b and "[MDSR | Req. 4 | 기준#p4]" not in b


def test_paragraph_count_uses_same_component_only(tmp_path, monkeypatch):
    # 서버 Req(100번대)만 4문단이어도 모바일 새 Req에는 영향 없음
    reqs = dict(fixture_docs.MDSR_REQS)
    t, d, p, crit = reqs["Req. 100"]
    reqs["Req. 100"] = (t, d, p, crit + ["추가 1", "추가 2"])
    names = insert_names(tmp_path, reqs, monkeypatch)
    assert "[MDSR | Req. 4 | 기준#p3]" not in names


def test_template_cells_signature_has_no_fixed_count(tmp_path):
    inp = build_case(tmp_path / "case" / "input")
    g = CellGraph(parse_case(inp))
    assert template_cells(g, "MDDR", "Req. 3", "Req. 4") == ["번호", "설계#p1", "설계#p2"]
