"""run_case(case_input_dir, config) → (예측, 로그). 케이스 input/ 폴더만 읽는다(가드로 강제).

변경이 여러 개인 복합 요청('… 또한 …')은 변경마다 검색·위치 찾기·편집·검사 고리를 원본 문서 기준으로 따로 돌리고
(작업 폴더 part0, part1 …), 칸 단위 롤백까지 마친 편집을 요청 순서대로 합친다. 같은 칸을 두 변경이 고치면 앞 변경의
편집을 남기고 log의 merge.conflicts에 적는다. 속성 여러 개를 함께 바꾸는 T2 요청은 변경 하나로 다룬다.

사용: python -m document_ai.cascade.run <input_dir> <work_dir> [--preset full] [--case-id T1_01]
"""

import argparse
import json
import tempfile
import time
from pathlib import Path

from . import config as cfg
from . import pack as pack_mod
from .apply import apply_edits
from .cells import parse_case, present_docs
from .editor import edit
from .gate import approve, final_metrics, fingerprint, rollback
from .graph import CellGraph
from .guard import ReadGuard, default_corpus_dir
from .llm import LLMClient, LLMFormatError
from .locator import locate
from .loop import run_loop
from .intent import IntentError, parse_natural_all
from .request import PROPAGATION, RequestError, attr_changes, parse_requests
from .generic_graph import derive_pack
from .rule_extract import with_rules_source
from .retrieval import CorpusHashError, http_embed, make_retriever
from .trace_log import TraceLog
from .verifier import verify

CASE_TYPE = {"T1": "T1", "T2": "T2", "T3_add": "T3", "T3_delete": "T3"}
PUBLIC_EDIT_KEYS = ("site", "doc", "item", "field", "new_text", "op")


def make_llm(config):
    return LLMClient(config.base_url, config.model, config.temperature, config.seed, config.max_tokens,
                     backend=config.backend, thinking=config.thinking)


def _lines(g, e):
    """텍스트 형식 칸은 원본 줄 범위를 함께 낸다(저장소 문서 채점용)."""
    c = g.cell(e["site"]) if g is not None else None
    return {"file": c.doc, "lines": list(c.lines)} if c is not None and getattr(c, "lines", None) else {}


def retrieval_queries(ch):
    """검색 질의 목록. T2는 바뀐 속성마다 하나, T3 추가는 새 기능 설명 하나, 그 밖에는 없음."""
    if ch.kind == "T2":
        return [f"{ko} {a} 적용 예외사항 적용하지 않는다" for a, ko, _, _ in attr_changes(ch)]
    if ch.kind == "T3_add":
        return [f"{ch.title} {ch.content}"]
    return []


def retrieval_query(ch):
    qs = retrieval_queries(ch)
    return qs[0] if qs else ""


def retrieve(ch, config, retriever, log):
    """T2: 바뀐 속성으로, T3 추가: 새 기능 설명으로 고정 코퍼스를 검색한다. 다른 경우와 retrieval off는 호출 0.
    질의가 여럿이면(속성 여러 개) 질의마다 k개를 찾아 조항 ID로 중복을 빼고 질의 순서대로 잇는다."""
    qs = retrieval_queries(ch) if retriever is not None else []
    hits, seen = [], set()
    for q in qs:
        for h in retriever.search(q, config.retrieval_k):
            if len(qs) == 1 or h["clause_id"] not in seen:
                seen.add(h["clause_id"])
                hits.append(h)
    log.stage("retrieval", calls=getattr(retriever, "calls", 0), chunks=len(hits),
              retriever=getattr(retriever, "name", None), k=config.retrieval_k if retriever else 0,
              query=" | ".join(qs) if hits else "", clause_ids=[h["clause_id"] for h in hits])
    return hits


PART_STAGES = ("retrieval", "locator", "editor")
SUM_FIELDS = ("calls", "chunks", "candidates", "units", "relevant", "targets", "structural", "edits",
              "context_chars", "exec_errors")


def run_change(ch, g, config, llm, log, retriever, inp, work, reader, case_id):
    """변경 하나: 검색 → 위치 찾기 → 편집 → 검사 고리 → 칸 단위 롤백. (Locator 결과, 고리 상태, 남긴 편집, 되돌린 칸)"""
    evidence = retrieve(ch, config, retriever, log)
    res = locate(ch, g, config, llm, log, evidence)
    edits = edit(ch, res, g, config, llm, log)
    state = run_loop(ch, res, g, config, llm, log, edits, inp, work, reader, case_id)
    # 게이트: 마지막 상태에서 위치 없음·범위 밖 편집을 칸 단위로 되돌린다(검사를 끈 경우 쓰기 실패만)
    issues = state["issues"]
    if not config.verifier and config.mode != "ledger_like":
        issues = [{"type": "V1", "site": f["site"], "detail": f["reason"]} for f in state["apply"].failed]
    kept, rolled = rollback(state["edits"], issues)
    return res, state, kept, rolled


def merge_parts(parts):
    """부분 변경의 편집을 요청 순서대로 합친다. 같은 칸은 앞 변경의 편집을 남긴다. (합친 편집, 겹친 칸 기록)"""
    merged, owner, conflicts = [], {}, []
    for i, kept in enumerate(parts):
        for e in kept:
            if owner.get(e["site"], i) != i:
                conflicts.append({"site": e["site"], "kept_part": owner[e["site"]], "dropped_part": i})
                continue
            owner[e["site"]] = i
            merged.append(e)
    return merged, conflicts


def _sum_stages(per_part):
    out = {}
    for name in PART_STAGES:
        agg = {}
        for st in per_part:
            for k, v in st.get(name, {}).items():
                if k in SUM_FIELDS and isinstance(v, (int, float)):
                    agg[k] = agg.get(k, 0) + v
        if agg:
            out[name] = agg
    return out


def run_case(case_input_dir, config, llm=None, work_dir=None, case_id=None, method=None, embed_fn=None):
    inp = Path(case_input_dir).resolve()
    work = Path(work_dir or tempfile.mkdtemp(prefix="cascade_")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    case_id = case_id or inp.parent.name
    llm = llm or make_llm(config)
    log = TraceLog(case_id, config.hash())
    pk = with_rules_source(pack_mod.load(config.domain_pack), config.rules_source)
    guard = ReadGuard([inp, pk.root], write_dirs=[work], corpus_dir=pk.corpus_dir() or default_corpus_dir())
    t0 = time.time()
    pred = {"case_id": case_id, "case_type": None, "method": method or f"cascade_{config.mode}",
            "model": getattr(llm, "model", config.model), "config_hash": config.hash(), "seed": config.seed,
            "propagation_type": None, "edits": [], "parse_ok": True, "truncated": False, "error": None, "stop_reason": None}
    final_edits = []
    g_ref = None
    try:
        with guard.active():
            fp0 = fingerprint(inp, guard.read_bytes, pk)
            if pk.is_text:
                pk = pk.with_docs(present_docs(inp, pk))
            cells = parse_case(inp, reader=guard.read_bytes, pack=pk)
            if config.graph_mode != "pack":
                pk, rep = derive_pack(cells, pk, config)
            g = g_ref = CellGraph(cells, pack=pk)
            if config.request_mode == "natural":
                nl = inp / "request_nl.txt"
                chs = parse_natural_all(guard.read_text(nl if nl.exists() else inp / "request.txt"), g, llm, log)
                intents = [{k: v for k, v in vars(c).items() if k != "text"} for c in chs]
                log.extra["intent"] = intents[0] if len(intents) == 1 else intents
            else:
                chs = parse_requests(guard.read_text(inp / "request.txt"), pk)
            ch = chs[0]
            pred["case_type"] = CASE_TYPE[ch.kind]
            pred["propagation_type"] = "+".join(PROPAGATION[c.kind] for c in chs)
            if len(chs) > 1:
                pred["changes"] = [c.kind for c in chs]
            if config.graph_mode != "pack":
                g._embed_fn = embed_fn or http_embed(config.embed_url)
                log.stage("generic_graph", id_formats=len(rep["id_formats"]), link_columns=len(rep["link_columns"]),
                          trace_edges=g.n_edges("trace"), same_id_edges=g.n_edges("same_id"), report=rep)
            log.stage("graph", cells=len(g.cells), trace_edges=g.n_edges("trace"), value_keys=len(g.value_index),
                      n_docs=len(g.docs), doc_chars=sum(len(c.text) for c in g.cells))
            retriever = make_retriever(config, pk, guard.read_bytes, embed_fn)
            runs, per_part = [], []
            for i, c in enumerate(chs):
                if len(chs) > 1:
                    log.part = i
                    keep = {k: v for k, v in log.stages.items() if k not in PART_STAGES}
                    log.stages = keep
                runs.append(run_change(c, g, config, llm, log, retriever, inp, work / f"part{i}" if len(chs) > 1 else work,
                                       guard.read_bytes, case_id))
                if len(chs) > 1:
                    per_part.append({k: v for k, v in log.stages.items() if k in PART_STAGES})
            log.part = None
            stops = [state["stop"] for _, state, _, _ in runs]
            pred["stop_reason"] = stops[0] if len(set(stops)) == 1 else "+".join(stops)
            final_edits, conflicts = merge_parts([kept for _, _, kept, _ in runs])
            rolled = [s for _, _, _, r in runs for s in r]
            approved = approve(final_edits, config.auto_approve)
            final_dir = work / "final"
            ar = apply_edits(inp, final_dir, final_edits, guard.read_bytes, pk)
            all_checks = cfg.from_dict({**config.to_dict(), "verifier": True,
                                        "verifier_checks": {k: True for k in cfg.CHECKS}})
            final_cells = parse_case(final_dir, pack=pk)
            final_issues = []
            for i, (c, (res, _, kept, _)) in enumerate(zip(chs, runs)):
                # 마지막 검사도 변경마다: 합친 편집 중 그 변경의 것만 보고, 합친 사본을 읽는다
                dropped = {x["site"] for x in conflicts if x["dropped_part"] == i}
                own = [e for e in kept if e["site"] not in dropped]
                final_issues += verify(c, res, own, g, final_cells, all_checks, ar)
            fp_ok = fingerprint(inp, guard.read_bytes, pk) == fp0
            log.extra["gate"] = {"rolled_back": rolled, "approved": approved, "fingerprint_ok": fp_ok,
                                 "final_dir": str(final_dir), **final_metrics(final_issues)}
            locs = [{"judgments": res.judgments, "anchors": res.anchors, "rule_info": res.rule_info}
                    for res, _, _, _ in runs]
            if len(chs) == 1:
                log.extra["locator"] = locs[0]
            else:
                log.stages.update(_sum_stages(per_part))
                log.extra["parts"] = [{"kind": c.kind, "stop_reason": st, "stages": ps, **loc}
                                      for c, st, ps, loc in zip(chs, stops, per_part, locs)]
                log.extra["merge"] = {"conflicts": conflicts}
                log.extra["locator"] = {
                    "judgments": [{**j, "part": i} for i, loc in enumerate(locs) for j in loc["judgments"]],
                    "anchors": {k: v for loc in locs for k, v in loc["anchors"].items()},
                    "rule_info": next((loc["rule_info"] for c, loc in zip(chs, locs) if c.kind == "T2"),
                                      locs[0]["rule_info"])}
    except (LLMFormatError, RequestError, CorpusHashError, IntentError) as err:
        pred["parse_ok"] = False
        pred["error"] = f"{type(err).__name__}: {err}"
    except Exception as err:  # 케이스 하나의 실패가 전체 실행을 멈추지 않게 기록만 한다
        pred["error"] = f"{type(err).__name__}: {err}"
    log.files(guard.files_read(), guard.violations())
    log.extra["stop_reason"] = pred["stop_reason"]
    log_path = log.write(work / "log.json")
    pred["edits"] = [{k: e[k] for k in PUBLIC_EDIT_KEYS} | {"source": e.get("source", "")} | _lines(g_ref, e)
                     for e in final_edits if e.get("report", True)]
    st = log.stages
    # 맥락 토큰: Locator·Editor 첫 호출의 입력 토큰(재수정 호출 제외). 문서 토큰은 글자 수/2 추정(한국어 기준).
    ctx = sum(c["tokens_in"] for c in log.calls if c["name"] in ("locator", "editor"))
    log.extra["complexity"] = {
        "n_docs": st.get("graph", {}).get("n_docs", 0), "n_cells": st.get("graph", {}).get("cells", 0),
        "doc_chars": st.get("graph", {}).get("doc_chars", 0), "doc_tokens": st.get("graph", {}).get("doc_chars", 0) // 2,
        "n_candidates": st.get("locator", {}).get("candidates", 0), "n_relevant": st.get("locator", {}).get("relevant", 0),
        "n_retrieved": st.get("retrieval", {}).get("chunks", 0), "context_tokens": ctx,
        "llm_calls": log.n_calls, "rounds": len(log.rounds), "tokens_in": log.tokens_in, "tokens_out": log.tokens_out,
        "cost_usd": round(log.cost_usd, 6), "seconds": round(time.time() - t0, 2)}
    pred["complexity"] = log.extra["complexity"]
    pred.update({"n_pred": len(pred["edits"]), "input_tokens": log.tokens_in, "output_tokens": log.tokens_out,
                 "n_calls": log.n_calls,
                 "exec_errors": sum(r.get("exec_errors", 0) for r in log.rounds), "cost_usd": round(log.cost_usd, 6), "seconds": round(time.time() - t0, 2), "log_path": str(log_path),
                 "rounds": len(log.rounds),
                 "truncated": any(c.get("truncated") for c in log.calls),
                 "format_retry": any(c.get("format_retry") for c in log.calls)})
    return pred, log.to_dict()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir")
    ap.add_argument("work_dir")
    ap.add_argument("--preset", default="full")
    ap.add_argument("--config", default="")
    ap.add_argument("--case-id", default=None)
    a = ap.parse_args()
    config = cfg.load(a.config) if a.config else cfg.preset(a.preset)
    pred, _ = run_case(a.input_dir, config, work_dir=a.work_dir, case_id=a.case_id)
    print(json.dumps(pred, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
