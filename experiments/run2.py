"""run2 실행: B2에 한 턴 더(Verifier·자기 점검), B2 반복(온도 0.6·seed 1~3), 자연어 요청(B1·B2),
분할 방식(기능별·문서별), 두 번째 모델(B2·B2+Verifier). 결과 파일이 있는 케이스는 건너뛴다.

stage: b2v | b2sc | seeds | nl | split | m14 (14B 서버로 B2, B2+Verifier) | all (32B 단계를 우선순위대로, 시간 예산 12시간)
사용: python experiments/run2.py <stage> [--cases T1_01,T2_01] [--model qwen3-32b]
"""
import argparse
import json
import random
import threading
import time
import traceback

from baselines import (MAX_TOKENS, MODEL, OUTPUT_FMT, PARSE_ERRORS, PROMPTS, ROOT, SEED, append_log,
                       build_messages, format_retry_messages, load_cases, parse, post, read_input, run_timed)
from verifier import feedback_text, verify

RESULTS = ROOT / "results" / "run2"
OUT = RESULTS / "predictions"
RUN1_B2 = ROOT / "results" / "run1" / "predictions" / "B2"
LOG = RESULTS / "eta.log"
SELF_CHECK = ("방금 답한 수정 목록을 다시 검토하라. 빠진 위치나 불필요한 위치가 있으면 고친 최종 JSON만 출력하라. "
              "바꿀 것이 없으면 같은 JSON을 그대로 출력하라.")
CALL_CAP = 4
TOKEN_CAP = 150_000  # 분할 방식 케이스당 총 토큰 상한(입력+출력), 두 방식 같음
LOC_SCHEMA = {"type": "object", "required": ["candidates"], "additionalProperties": False,
              "properties": {"candidates": {"type": "array", "items": {
                  "type": "object", "required": ["doc", "item", "field", "reason"], "additionalProperties": False,
                  "properties": {k: {"type": "string"} for k in ("doc", "item", "field", "reason")}}}}}

ALL_STAGES = ("b2v", "b2sc", "seeds", "nl", "split")
LIMIT_H = 12.0
# run1 실측(B2 23분/36, B1 47분/36)으로 잡은 단계별 예상(시간)
EST = {"b2v": 0.5, "b2sc": 0.5, "seeds": 1.3, "nl": 1.3, "split": 0.8}
M14_EST = 1.5
_lock = threading.Lock()


def log(msg):
    append_log(LOG, msg)


class Call:
    """한 케이스의 호출들을 모아 토큰·시간·잘림을 누적한다."""

    def __init__(self, model):
        self.model, self.n, self.inp, self.out, self.trunc, self.secs = model, 0, 0, 0, False, 0.0

    def chat(self, messages, seed=SEED, **kw):
        t0 = time.perf_counter()
        r = post(messages, model=self.model, seed=seed, schema_name="out", **kw)
        self.secs += time.perf_counter() - t0
        self.n += 1
        self.inp += r["usage"]["prompt_tokens"]
        self.out += r["usage"]["completion_tokens"]
        self.trunc = self.trunc or r["choices"][0].get("finish_reason") == "length"
        return r["choices"][0]["message"]["content"] or ""

    def chat_edits(self, messages, seed=SEED, **kw):
        """(edits 또는 None, 응답들, 형식 재요청 여부). JSON을 못 읽으면 형식만 고치라고 한 번 더 묻는다."""
        text = self.chat(messages, seed=seed, **kw)
        try:
            return parse(text), [text], False
        except PARSE_ERRORS:
            text2 = self.chat(format_retry_messages(messages, text), seed=seed + 1, **kw)
            try:
                return parse(text2), [text, text2], True
            except PARSE_ERRORS:
                return None, [text, text2], True


def save(tag, cid, meta, extra, call, edits, responses, error=None, base=(0, 0, 0.0)):
    """extra의 prompt_version·seed는 공통 필드로 옮기고 나머지는 기록 뒤에 붙인다. base는 첫 턴 (입력, 출력, 초)."""
    extra = dict(extra)
    rec = {"case_id": cid, "case_type": meta["case_type"], "method": tag, "model": call.model if call else None,
           "prompt_version": extra.pop("prompt_version"), "seed": extra.pop("seed", SEED),
           "input_tokens": (call.inp if call else 0) + base[0],
           "output_tokens": (call.out if call else 0) + base[1],
           "seconds": round((call.secs if call else 0.0) + base[2], 2), "parse_ok": edits is not None,
           "n_pred": len(edits or []), "truncated": bool(call and call.trunc), "error": error,
           "n_calls": call.n if call else 0}
    out = OUT / tag / f"{cid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {**rec, **extra, "edits": edits or [], "responses": responses}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with _lock, (RESULTS / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{tag} {cid} in={rec['input_tokens']} out={rec['output_tokens']} {rec['seconds']}s calls={rec['n_calls']} "
          f"parse={rec['parse_ok']} n={rec['n_pred']} trunc={rec['truncated']} err={error}", flush=True)


def followup(tag, cid, meta, kind, model, base_dir):
    """첫 턴 대화 뒤에 Verifier 지적(kind="verifier") 또는 자기 점검 문구(kind="selfcheck")를 한 턴 더 보낸다."""
    if (OUT / tag / f"{cid}.json").exists():
        return
    base = json.loads((base_dir / f"{cid}.json").read_text(encoding="utf-8"))
    base_cost = (base["input_tokens"], base["output_tokens"], base["seconds"])
    messages, _ = build_messages("B2", cid)
    extra = {"prompt_version": f"run2-b2-{kind}-v1", "base_edits": base["edits"]}
    if kind == "verifier":
        findings = verify(read_input(cid, "site_index.md"), read_input(cid, "request.txt").strip(), base["edits"])
        extra["findings"] = findings
        if not findings:  # 지적이 없으면 추가 호출 없이 B2 답을 최종으로 쓴다
            extra["no_findings"] = True
            return save(tag, cid, meta, extra, None, base["edits"], [], base=base_cost)
        turn = feedback_text(findings)
    else:
        turn = SELF_CHECK
    convo = messages + [{"role": "assistant", "content": base["responses"][-1]}, {"role": "user", "content": turn}]
    call = Call(model)
    try:
        edits, responses, retried = call.chat_edits(convo)
        extra["format_retry"] = retried
        save(tag, cid, meta, extra, call, edits, responses, None if edits is not None else "parse failed", base_cost)
    except Exception as err:
        print("ERROR", tag, cid, err, flush=True)


def single(tag, cid, meta, method, model, temperature, seed, nl):
    """B1/B2 프롬프트로 한 번 호출. nl이면 요청만 request_nl.txt로 바꾼다."""
    if (OUT / tag / f"{cid}.json").exists():
        return
    messages, _ = build_messages(method, cid)
    if nl:
        request = read_input(cid, "request.txt").strip()
        u = messages[1]["content"]
        assert u.count(request) == 1
        messages[1]["content"] = u.replace(request, read_input(cid, "request_nl.txt").strip())
    call = Call(model)
    extra = {"prompt_version": f"run2-{method.lower()}{'-nl' if nl else ''}-v1", "seed": seed,
             "temperature": temperature, "request": "nl" if nl else "structured"}
    try:
        edits, responses, retried = call.chat_edits(messages, temperature=temperature, seed=seed)
        extra["format_retry"] = retried
        save(tag, cid, meta, extra, call, edits, responses, None if edits is not None else "parse failed")
    except Exception as err:
        print("ERROR", tag, cid, err, flush=True)


def prompt(name):
    return (PROMPTS / name).read_text(encoding="utf-8")


def split_functional(cid, model):
    """Locator → Editor → Checker (3회)."""
    base, _ = build_messages("B2", cid)
    system, user = base[0]["content"], base[1]["content"]
    call = Call(model)
    loc_user = user.replace(OUTPUT_FMT, prompt("split_locator_v1.txt"))
    text = call.chat([{"role": "system", "content": system}, {"role": "user", "content": loc_user}], schema=LOC_SCHEMA)
    try:
        cands = json.loads(text)["candidates"]
    except (ValueError, KeyError):
        cands = []
    cand_txt = json.dumps({"candidates": cands}, ensure_ascii=False)
    ed_user = user.replace(OUTPUT_FMT, f"[후보 위치]\n{cand_txt}\n\n{prompt('split_editor_v1.txt')}\n\n{OUTPUT_FMT}")
    edits, _, _ = call.chat_edits([{"role": "system", "content": system}, {"role": "user", "content": ed_user}])
    edits = edits or []
    if call.n < CALL_CAP and call.inp + call.out < TOKEN_CAP:
        draft = json.dumps({"edits": edits}, ensure_ascii=False)
        ck_user = user.replace(OUTPUT_FMT, f"[수정 초안]\n{draft}\n\n{prompt('split_checker_v1.txt')}\n\n{OUTPUT_FMT}")
        final, _, _ = call.chat_edits([{"role": "system", "content": system}, {"role": "user", "content": ck_user}])
        if final is not None:
            edits = final
    return call, edits, {"candidates": cands}


def split_by_doc(cid, model, docs):
    """문서별 담당(케이스 문서 수만큼, 최대 3회) → Merger(1회)."""
    base, _ = build_messages("B2", cid)
    system, user = base[0]["content"], base[1]["content"]
    owner_txt = prompt("split_docowner_v1.txt")
    call = Call(model)
    parts = {}
    for d in docs:
        if call.n >= CALL_CAP - 1 or call.inp + call.out >= TOKEN_CAP:
            break
        u = user.replace(OUTPUT_FMT, owner_txt.format(doc=d) + "\n\n" + OUTPUT_FMT)
        e, _, _ = call.chat_edits([{"role": "system", "content": system}, {"role": "user", "content": u}])
        parts[d] = [x for x in (e or []) if str(x.get("doc", "")).strip().upper().startswith(d)]
    merged_in = json.dumps({d: {"edits": v} for d, v in parts.items()}, ensure_ascii=False)
    request = read_input(cid, "request.txt").strip()
    mu = f"## 변경 요청\n{request}\n\n[문서별 수정 목록]\n{merged_in}\n\n{prompt('split_merger_v1.txt')}\n\n{OUTPUT_FMT}"
    final, _, _ = call.chat_edits([{"role": "system", "content": system}, {"role": "user", "content": mu}])
    edits = final if final is not None else [x for v in parts.values() for x in v]
    return call, edits, {"per_doc": parts}


def split_case(tag, cid, meta, model):
    if (OUT / tag / f"{cid}.json").exists():
        return
    try:
        if tag == "split_functional":
            call, edits, info = split_functional(cid, model)
        else:
            call, edits, info = split_by_doc(cid, model, meta["docs"])
        info.update(prompt_version=f"run2-{tag}-v1", within_call_cap=call.n <= CALL_CAP,
                    within_token_cap=call.inp + call.out <= TOKEN_CAP + MAX_TOKENS)
        save(tag, cid, meta, info, call, edits, [])
    except Exception as err:
        print("ERROR", tag, cid, err, flush=True)


def split_sample(metas):
    """유형마다 4케이스."""
    rng = random.Random(SEED)
    out = []
    for t in ("T1", "T2", "T3"):
        ids = sorted(c for c, m in metas.items() if m["case_type"] == t)
        out += sorted(rng.sample(ids, 4))
    return out


def run_stage(stage, metas, cases, model):
    def timed(tag, cids, fn):
        run_timed(tag, cids, metas, fn, OUT, log)

    cids = cases or list(metas)
    if stage == "b2v":
        timed("B2V", cids, lambda c, mt: followup("B2V", c, mt, "verifier", model, RUN1_B2))
    elif stage == "b2sc":
        timed("B2SC", cids, lambda c, mt: followup("B2SC", c, mt, "selfcheck", model, RUN1_B2))
    elif stage == "seeds":
        for s in (1, 2, 3):
            tag = f"B2_t06_s{s}"
            timed(tag, cids, lambda c, mt, tag=tag, s=s: single(tag, c, mt, "B2", model, 0.6, s, False))
    elif stage == "nl":
        for method in ("B2", "B1"):
            tag = f"{method}_nl"
            timed(tag, cids, lambda c, mt, tag=tag, method=method: single(tag, c, mt, method, model, 0.0, SEED, True))
    elif stage == "split":
        sample = cases or split_sample(metas)
        (RESULTS / "split_sample.json").write_text(json.dumps(sample), encoding="utf-8")
        for tag in ("split_functional", "split_bydoc"):
            timed(tag, sample, lambda c, mt, tag=tag: split_case(tag, c, mt, model))
    elif stage == "m14":
        timed("B2_14b", cids, lambda c, mt: single("B2_14b", c, mt, "B2", model, 0.0, SEED, False))
        timed("B2V_14b", cids, lambda c, mt: followup("B2V_14b", c, mt, "verifier", model, OUT / "B2_14b"))


def run_all(metas, cases, model):
    """32B 단계를 우선순위대로 돌린다. 누적 경과 + 이 단계 예상 + 14B 단계 예상이 12시간을 넘으면 그 단계는 건너뛴다."""
    t0 = time.time()
    skipped = []
    log("run2 32B stages start")
    for stage in ALL_STAGES:
        elapsed = (time.time() - t0) / 3600
        if elapsed + EST[stage] + M14_EST > LIMIT_H:
            skipped.append(stage)
            log(f"SKIP {stage}: elapsed {elapsed:.2f}h + est {EST[stage]}h + 14B {M14_EST}h > {LIMIT_H}h")
            continue
        log(f"stage {stage} (elapsed {elapsed:.2f}h)")
        try:  # 한 단계가 실패해도 다음 단계로 넘어간다
            run_stage(stage, metas, cases, model)
        except Exception:
            traceback.print_exc()
    log("SKIP b4: 첨부3·4·5 PDF를 pilot_base·pilot_data·workspace 아래에서 찾지 못함(PDF 경로 필요)")
    log(f"run2 32B stages done in {(time.time() - t0) / 3600:.2f}h, skipped={skipped}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=[*ALL_STAGES, "m14", "all"])
    ap.add_argument("--cases", default="")
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    metas = load_cases()
    cases = a.cases.split(",") if a.cases else None
    if a.stage == "all":
        run_all(metas, cases, a.model)
    else:
        run_stage(a.stage, metas, cases, a.model)


if __name__ == "__main__":
    main()
