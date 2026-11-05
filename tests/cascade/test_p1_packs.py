"""P1: 도메인 팩 분리. EC-SW 팩으로 돈 결과는 회귀 기준과 같고, 팩 설정만 바꾼 미니 도메인에서도 그래프·후보가 만들어진다."""

import os
import ast
import json
import re
from pathlib import Path

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade import pack as pack_mod
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import locate
from document_ai.cascade.request import parse_request
from document_ai.cascade.run import run_case
from document_ai.cascade.trace_log import TraceLog

from .fixture_mini import PACK as MINI, build_mini
from .test_p0_recheck import CASES, REG, need_pilot

CODE = Path(pack_mod.__file__).resolve().parent


def test_ec_sw_pack_loads_from_config_file():
    p = pack_mod.load("ec_sw")
    assert p.docs == ["MDSR", "MDDR", "XXCS"]
    assert p.is_req("Req. 3") and p.norm_req("Req.12") == "Req. 12"
    assert p.sec_full_re.match("IA-05") and not p.sec_full_re.match("Req. 1")
    assert ("MDSR", "소프트웨어 기능 요구사항 번호") in p.link_cells
    assert p.rules_doc()["rules"] and p.values.key_of("15분") == "15|min"
    env = os.environ.get("CASCADE_CORPUS_DIR")  # 공개 저장소에서는 코퍼스 위치를 환경변수로 준다
    assert (p.corpus_dir() == Path(env).resolve()) if env else p.corpus_dir().parts[-2:] == ("pilot_data", "corpus")


def test_no_ec_sw_names_hardcoded_in_code():
    """코드의 문자열 상수(독스트링 제외)에 EC-SW 문서 이름·번호 형식이 없어야 한다."""
    pat = re.compile(r"^(MDSR|MDDR|XXCS)$|IA\|UC|Req\\?\.|^Req\. |^\[(MDSR|MDDR|XXCS)")
    hits = []
    for f in sorted(CODE.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and pat.search(n.value):
                hits.append(f"{f.name}:{n.lineno}: {n.value[:60]}")
    assert hits == []


@need_pilot
@pytest.mark.parametrize("cid", ["T1_05", "T2_06", "T3_05"])
def test_ec_sw_pack_matches_regression(cid, tmp_path):
    from .test_p0_recheck import regression_config
    fx = json.loads((REG / f"{cid}.json").read_text(encoding="utf-8"))
    conf = cfg.from_dict({**regression_config().to_dict(), "domain_pack": "ec_sw"})
    pred, _ = run_case(CASES / cid / "input", conf, llm=MockLLM(list(fx["responses"])), work_dir=tmp_path / "w")
    key = lambda e: (e["site"], e["new_text"], e["op"])
    assert sorted(map(key, pred["edits"])) == sorted(map(key, fx["expected_edits"]))


def mini_graph(tmp_path, request=None):
    p = pack_mod.load(MINI)
    inp = build_mini(tmp_path / "mini" / "input", request)
    return p, inp, CellGraph(parse_case(inp, pack=p), pack=p)


def test_mini_domain_cells_and_graph(tmp_path):
    p, _, g = mini_graph(tmp_path)
    sites = {c.site for c in g.cells}
    assert "[SPEC | FR-1 | 설명]" in sites and "[MAN | FR-2 | 안내]" in sites
    assert "[TEST | TC-01 | 대상 기능]" in sites
    assert g.secs_of_req("FR-1") == {"TC-01"} and g.n_edges("trace") == 3
    assert g.same_id("SPEC", "FR-1") == {("MAN", "FR-1")}
    assert len(g.value_cells("30초")) >= 4  # SPEC 설명·기준, MAN 안내, TEST 시험 내용, FR-3


def test_mini_domain_t1_candidates(tmp_path):
    req = "FR-1 '화면 꺼짐'의 화면 꺼짐 시간을 30초에서 60초로 변경한다."
    p, inp, g = mini_graph(tmp_path, req)
    ch = parse_request(req, p)
    assert ch.kind == "T1" and ch.origin_req == "FR-1"
    res = locate(ch, g, cfg.from_dict({"relevance": False, "domain_pack": str(MINI)}), None, TraceLog("m"))
    kinds = {c.site: c.kind for c in res.candidates}
    assert kinds["[SPEC | FR-1 | 설명]"] == "origin"
    assert kinds["[MAN | FR-1 | 안내]"] == "same_id"
    assert kinds["[TEST | TC-01 | 시험 내용]"] == "trace"
    assert kinds["[SPEC | FR-3 | 설명]"] == "value"


def test_mini_domain_t3_delete_and_add(tmp_path):
    p, inp, g = mini_graph(tmp_path)
    ch = parse_request("FR-2 '경고음' 기능을 삭제한다. 번호는 유지하고 '삭제'로 표기한다.", p)
    res = locate(ch, g, cfg.from_dict({"domain_pack": str(MINI)}), None, TraceLog("m"))
    kinds = {c.site: c.kind for c in res.relevant}
    assert kinds["[TEST | TC-02 | 대상 기능]"] == "unlink" and kinds["[SPEC | FR-2 | 설명]"] == "delete"
    ch = parse_request("장치 기능 FR-4 '밝기 조절'을(를) 신규 추가한다. 내용: 주변 밝기에 따라 화면 밝기를 바꾼다.", p)
    res = locate(ch, g, cfg.from_dict({"relevance": False, "domain_pack": str(MINI)}), None, TraceLog("m"))
    ins = [c.site for c in res.candidates if c.kind == "insert"]
    assert "[SPEC | FR-4 | 번호]" in ins and "[MAN | FR-4 | 안내]" in ins
    assert res.anchors == {"SPEC": "FR-3", "MAN": "FR-3"}


def test_mini_domain_full_run_with_mock(tmp_path):
    req = "FR-1 '화면 꺼짐'의 화면 꺼짐 시간을 30초에서 60초로 변경한다."
    inp = build_mini(tmp_path / "mini" / "input", req)

    def fn(messages, name):
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": "FR-3" not in l, "reason": "t"} for i, l in enumerate(lines)]}
        tl = messages[-1]["content"].split("## 고칠 칸")[1].split("##")[0]
        sites = re.findall(r"^(\[.+?\])", tl, re.M)
        return {"edits": [{"site": s, "new_text": "60초"} for s in sites]}
    pred, log = run_case(inp, cfg.from_dict({"domain_pack": str(MINI), "edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=MockLLM(fn), work_dir=tmp_path / "w")
    assert pred["error"] is None and pred["edits"]
    assert all(not e["site"].startswith("[SPEC | FR-3") for e in pred["edits"])
    assert log["violations"] == []
