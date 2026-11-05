"""G1: 범용 그래프층. 식별자 형식 추론, 연결 열 자동 감지, 같은 번호·유사도 엣지, 팩의 연결 표 설정 없이 그래프 생성."""

from document_ai.cascade import config as cfg
from document_ai.cascade import generic_graph as GG
from document_ai.cascade import pack as pack_mod
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import locate
from document_ai.cascade.request import parse_request
from document_ai.cascade.run import run_case
from document_ai.cascade.trace_log import TraceLog

from .fixture_docs import build_case
from .fixture_mini import PACK as MINI, build_mini
from .test_m8_loop_gate import GOOD, T1, scripted


def ec_cells(tmp_path, request=None):
    inp = build_case(tmp_path / "c" / "input", request=request)
    return inp, parse_case(inp)


def test_infer_id_formats(tmp_path):
    _, cells = ec_cells(tmp_path)
    fmts = GG.infer_id_formats(cells)
    by = {f["name"]: f for f in fmts}
    assert any(f["example"].startswith("Req.") for f in fmts)
    sec = [f for f in fmts if "IA-05" in f["ids"]]
    assert sec and {"IA-07", "UC-03"} <= set(sec[0]["ids"])
    assert all(len(f["docs"]) >= 2 for f in fmts)
    assert not any("AES" in f["example"] for f in by.values())


def test_detect_link_columns(tmp_path):
    _, cells = ec_cells(tmp_path)
    fmts = GG.infer_id_formats(cells)
    cols = GG.detect_link_columns(cells, fmts, threshold=0.6)
    found = {(c["doc"], c["cell"]) for c in cols}
    assert found == {("MDSR", "소프트웨어 기능 요구사항 번호"), ("XXCS", "해당 요구사항")}
    assert all(c["ratio"] >= 0.6 for c in cols)


def test_graph_without_pack_link_settings_matches_pack_trace(tmp_path):
    _, cells = ec_cells(tmp_path)
    pk = pack_mod.load("ec_sw")
    stripped = pack_mod.DomainPack({**pk.raw, "link_cells": []}, pk.root)
    derived, report = GG.derive_pack(cells, stripped, cfg.from_dict({"graph_mode": "generic"}))
    g_pack = CellGraph(cells, pack=pk)
    g_gen = CellGraph(cells, pack=derived)
    assert dict(g_gen.req_secs) == dict(g_pack.req_secs)
    assert report["link_columns"] and report["id_formats"]
    assert derived.rules_file == ""  # generic: 규칙 없음


def test_generic_rules_keeps_domain_layer(tmp_path):
    _, cells = ec_cells(tmp_path)
    pk = pack_mod.load("ec_sw")
    derived, _ = GG.derive_pack(cells, pk, cfg.from_dict({"graph_mode": "generic_rules"}))
    assert derived.rules_file and derived.decision_cells == pk.decision_cells


def test_mini_domain_formats_and_links(tmp_path):
    inp = build_mini(tmp_path / "m" / "input")
    pk = pack_mod.load(MINI)
    cells = parse_case(inp, pack=pk)
    derived, rep = GG.derive_pack(cells, pack_mod.DomainPack({**pk.raw, "link_cells": []}, pk.root),
                                  cfg.from_dict({"graph_mode": "generic"}))
    assert {(c["doc"], c["cell"]) for c in rep["link_columns"]} == {("TEST", "대상 기능")}
    assert CellGraph(cells, pack=derived).secs_of_req("FR-2") == {"TC-02"}


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 32
        for w in t.split():
            v[hash(w) % 32] += 1.0
        out.append(v)
    return out


def test_similarity_edges_only_without_other_edges(tmp_path):
    _, cells = ec_cells(tmp_path)
    g = CellGraph(cells)
    seeds = g.item_cells("MDSR", "Req. 2")
    edges = GG.similarity_edges(g, seeds, fake_embed, threshold=0.3)
    assert edges
    for a, b, s in edges:
        assert s >= 0.3 and a.doc != b.doc
        assert b not in g.neighbors(a)  # 다른 엣지가 있는 쌍은 빼고


def test_generic_run_no_guard_violation_and_logs_edges(tmp_path):
    inp, _ = ec_cells(tmp_path, T1)
    conf = cfg.from_dict({"graph_mode": "generic", "edit_mode": "free_json",
                          "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}})
    pred, log = run_case(inp, conf, llm=MockLLM(scripted([GOOD])), work_dir=tmp_path / "w", embed_fn=fake_embed)
    assert pred["error"] is None and log["violations"] == []
    gs = log["stages"]["generic_graph"]
    assert gs["link_columns"] == 2 and gs["trace_edges"] > 0


def test_graph_mode_flag_validation():
    assert cfg.CascadeConfig().graph_mode == "pack"
    assert cfg.CascadeConfig().link_threshold == 0.6 and cfg.CascadeConfig().sim_threshold == 0.7
    import pytest
    with pytest.raises(ValueError):
        cfg.from_dict({"graph_mode": "magic"})


def test_generic_t1_adds_similar_candidates(tmp_path):
    inp, cells = ec_cells(tmp_path, T1)
    pk = pack_mod.load("ec_sw")
    conf = cfg.from_dict({"graph_mode": "generic", "relevance": False, "sim_threshold": 0.3})
    derived, _ = GG.derive_pack(cells, pk, conf)
    g = CellGraph(cells, pack=derived)
    g._embed_fn = fake_embed
    res = locate(parse_request(T1, derived), g, conf, None, TraceLog("t"))
    assert any(c.kind == "similar" for c in res.candidates)
