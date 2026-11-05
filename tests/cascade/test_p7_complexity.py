"""P7: 맥락 예산·복잡도 로그. 케이스 로그에 필드가 모두 있고 값이 일관된다."""

from document_ai.cascade import config as cfg
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.run import run_case

from .fixture_docs import build_case
from .test_m8_loop_gate import GOOD, T1, scripted

FIELDS = ("n_docs", "doc_chars", "doc_tokens", "n_candidates", "n_relevant", "n_retrieved", "context_tokens",
          "llm_calls", "rounds", "tokens_in", "tokens_out", "cost_usd", "seconds", "n_cells")


def test_complexity_fields_present_and_consistent(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T1)
    pred, log = run_case(inp, cfg.from_dict({"edit_mode": "free_json"}), llm=MockLLM(scripted([GOOD])),
                         work_dir=tmp_path / "w")
    cx = log["complexity"]
    assert set(FIELDS) <= set(cx)
    assert cx["n_docs"] == 3 and cx["doc_tokens"] > 0 and cx["doc_chars"] > cx["doc_tokens"] > 0
    assert cx["n_candidates"] >= cx["n_relevant"] > 0
    assert cx["context_tokens"] > 0 and cx["context_tokens"] <= cx["tokens_in"]
    assert cx["llm_calls"] == pred["n_calls"] and cx["rounds"] == len(log["rounds"])
    assert pred["complexity"] == cx


def test_complexity_counts_retrieved_chunks_zero_when_off(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T1)
    _, log = run_case(inp, cfg.from_dict({"edit_mode": "free_json", "retrieval": False}),
                      llm=MockLLM(scripted([GOOD])), work_dir=tmp_path / "w")
    assert log["complexity"]["n_retrieved"] == 0
