"""P0: 필수 항목을 테스트로 다시 확인한다. 기능 추가 없이 빠진 것만 보완한다."""

import os
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade import guard as guard_mod
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.guard import GuardViolation, ReadGuard
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.run import run_case
from document_ai.cascade.verifier import fake_issues

from .fixture_docs import build_case
from .test_m8_loop_gate import GOOD, STALE, T1, scripted

PILOT = Path(__file__).resolve().parents[2]
CASES = PILOT / "v02" / "cases"
REG = Path(__file__).resolve().parent / "data" / "regression"
FREE = cfg.from_dict({"edit_mode": "free_json"})  # 모의 Editor 응답이 칸마다 글 형식
need_pilot = pytest.mark.skipif(not CASES.exists(), reason="pilot_data not present")


# 그래프는 docx에서만

@need_pilot
def test_graph_built_from_docx_only_in_temp_case(tmp_path):
    inp = tmp_path / "case" / "input"
    shutil.copytree(CASES / "T1_01" / "input", inp)
    assert not list(tmp_path.rglob("links.json")) and not list(tmp_path.rglob("sites.json"))
    g = ReadGuard([inp])
    with g.active():
        graph = CellGraph(parse_case(inp, reader=g.read_bytes))
    assert graph.n_edges("trace") > 0 and graph.n_edges("value") > 0
    assert g.violations() == []
    pkg = Path(cfg.__file__).resolve().parent
    assert all(Path(p).parent == inp.resolve() or pkg in Path(p).parents for p in g.files_read())


# 가드

def test_guard_allows_corpus_and_logs(tmp_path):
    corpus = tmp_path / "pilot_data" / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "regs.jsonl").write_text("{}\n", encoding="utf-8")
    g = ReadGuard([], corpus_dir=corpus)
    assert g.read_text(corpus / "regs.jsonl") == "{}\n"
    assert str((corpus / "regs.jsonl").resolve()) in g.files_read()


def test_guard_corpus_does_not_open_answer_paths(tmp_path):
    root = tmp_path / "pilot_data"
    corpus = root / "corpus"
    corpus.mkdir(parents=True)
    gold = root / "v02" / "cases" / "T1_01" / "gold"
    gold.mkdir(parents=True)
    (gold / "impact.json").write_text("{}", encoding="utf-8")
    g = ReadGuard([], corpus_dir=corpus)
    with pytest.raises(GuardViolation):
        g.read_text(corpus / ".." / "v02" / "cases" / "T1_01" / "gold" / "impact.json")


def test_guard_denies_v03_product_dirs():
    g = ReadGuard([])
    for rel in ("pilot_data/v03/products/P1/product/attrs.yaml", "pilot_data/v03/products/P2/product/facts.csv",
                "pilot_data/experiments/verifier.py", "pilot_data/verifier/v0.py"):
        assert g.denied(Path("/x") / rel), rel


def test_default_corpus_dir_is_pilot_data_corpus():
    env = os.environ.get("CASCADE_CORPUS_DIR")  # 공개 저장소에서는 코퍼스 위치를 환경변수로 준다
    assert (guard_mod.default_corpus_dir() == Path(env).resolve()) if env else \
        guard_mod.default_corpus_dir().parts[-2:] == ("pilot_data", "corpus")


def test_run_log_lists_files_read(tmp_path):
    inp = build_case(tmp_path / "case" / "input", request=T1)
    _, log = run_case(inp, FREE, llm=MockLLM(scripted([GOOD])), work_dir=tmp_path / "w")
    assert str((inp / "request.txt").resolve()) in log["files_read"]
    assert log["violations"] == []


# Verifier 규격은 버전 관리

def test_verifier_spec_is_tracked_in_git():
    pkg = Path(cfg.__file__).resolve().parent
    out = subprocess.run(["git", "ls-files", "verifier_spec.md"], cwd=pkg, capture_output=True, text=True)
    assert out.stdout.strip() == "verifier_spec.md"


# 고리: 라운드별 편집 위치·토큰 로그

def test_loop_logs_sites_and_tokens_per_round(tmp_path):
    inp = build_case(tmp_path / "case" / "input", request=T1)
    _, log = run_case(inp, FREE, llm=MockLLM(scripted([STALE, GOOD])), work_dir=tmp_path / "w")
    assert len(log["rounds"]) == 2
    for r in log["rounds"]:
        assert r["sites"] and isinstance(r["tokens"], int)
    assert log["rounds"][1]["tokens"] > log["rounds"][0]["tokens"]


def test_loop_k_is_upper_bound(tmp_path):
    inp = build_case(tmp_path / "case" / "input", request=T1)
    conf = cfg.from_dict({"loop_k": 1, "edit_mode": "free_json"})
    pred, log = run_case(inp, conf, llm=MockLLM(scripted([STALE, {"edits": STALE["edits"][::-1]}, GOOD])),
                         work_dir=tmp_path / "w")
    assert len(log["rounds"]) <= 2 and pred["n_calls"] <= 3


# fake_feedback: 같은 형식·개수, 시드 고정

def test_fake_feedback_deterministic_with_seed(tmp_path):
    inp = build_case(tmp_path / "case" / "input")
    cells = parse_case(inp)
    real = [{"type": "V2", "site": cells[3].site, "detail": "x"}, {"type": "V6", "site": cells[4].site, "detail": "y"}]
    a = fake_issues(real, cells, 7, "c1", 0)
    b = fake_issues(real, cells, 7, "c1", 0)
    c = fake_issues(real, cells, 8, "c1", 0)
    assert a == b and a != c
    assert [i["type"] for i in a] == ["V2", "V6"] and set(a[0]) == set(real[0])


# 플래그

def test_flags_exist_including_retrieval_placeholder():
    c = cfg.CascadeConfig()
    for name in ("verifier", "relevance", "value_index", "rules", "retrieval", "mode"):
        assert hasattr(c, name), name
    assert c.retriever in ("bm25", "dense", "hybrid") and c.retrieval_k > 0
    with pytest.raises(ValueError):
        cfg.from_dict({"retriever": "magic"})


# 기준 출력 회귀(녹화 응답 3케이스)

def regression_config():
    """회귀 기준은 cascade_dev_v1 설정의 동작이다. 이후 추가한 구성요소는 끄고, 의도한 변경은 여기서 맞춘다.

    - P3: 녹화 응답이 칸마다 글 형식이라 edit_mode=free_json으로 돌린다(ops 기본값 도입).
    """
    return cfg.from_dict({"edit_mode": "free_json"})


@need_pilot
@pytest.mark.parametrize("cid", ["T1_05", "T2_06", "T3_05"])
def test_regression_recorded_cases(cid, tmp_path):
    fx = json.loads((REG / f"{cid}.json").read_text(encoding="utf-8"))
    llm = MockLLM(list(fx["responses"]))
    pred, log = run_case(CASES / cid / "input", regression_config(), llm=llm, work_dir=tmp_path / "w", case_id=cid)
    assert pred["error"] is None
    assert [c["name"] for c in llm.calls] == fx["calls"]
    key = lambda e: (e["site"], e["new_text"], e["op"], e["source"])
    assert sorted(map(key, pred["edits"])) == sorted(map(key, fx["expected_edits"]))
    assert log["violations"] == []
