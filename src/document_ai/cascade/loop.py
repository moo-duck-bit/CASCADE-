"""고리 제어: 적용 → 검사 → 지적된 칸만 재수정, 최대 K회.

편집 연산(edit_mode=ops)의 실행 오류는 지적(type=E)으로 Verifier 지적과 함께 다룬다.
멈춤 조건: 지적 없음(no_issues) / 편집 변화 없음(no_change) / 같은 지적 반복(repeat_issues) / 토큰 예산 초과(token_budget)
그 밖에: K회를 다 씀(max_rounds), 검사 끔(verifier_off), ledger_like는 검사를 표시만 하고 재수정하지 않음(report_only).
"""

from collections import Counter

from .apply import apply_edits
from .cells import parse_case
from .editor import revise
from .ops import error_issues
from .verifier import fake_issues, verify


def _key(edits):
    return sorted((e["site"], e["new_text"]) for e in edits)


def run_loop(ch, res, g, config, llm, log, edits, input_dir, work_dir, reader=None, case_id=""):
    k = 0
    prev_issue_keys = None
    while True:
        rdir = work_dir / f"round{k}"
        ar = apply_edits(input_dir, rdir, edits, reader, g.pack)
        applied = parse_case(rdir, pack=g.pack)
        check_cfg = config
        checks = verify(ch, res, edits, g, applied, check_cfg, ar) if (config.verifier or config.mode == "ledger_like") else []
        errs = error_issues(getattr(edits, "errors", []))
        shown = (fake_issues(checks, g.cells, config.seed, case_id, k) if config.mode == "fake_feedback" else checks) + errs
        real = checks + errs
        log.round(k, issues=len(shown), real_issues=len(real), by_type=dict(Counter(i["type"] for i in real)),
                  edits=len(edits), exec_errors=len(errs), tokens=log.tokens_in + log.tokens_out,
                  sites=sorted(e["site"] for e in edits), issue_list=real)
        state = {"edits": edits, "issues": real, "apply": ar, "applied": applied, "rounds": k + 1}
        if config.mode == "ledger_like":
            return {**state, "stop": "report_only"}
        if not config.verifier:
            return {**state, "stop": "verifier_off"}
        if not real:
            return {**state, "stop": "no_issues"}
        keys = {(i["type"], i["site"]) for i in real}
        if prev_issue_keys is not None and keys == prev_issue_keys:
            return {**state, "stop": "repeat_issues"}
        if k >= config.loop_k:
            return {**state, "stop": "max_rounds"}
        if log.tokens_in + log.tokens_out > config.token_budget:
            return {**state, "stop": "token_budget"}
        new_edits = revise(ch, res, g, config, llm, log, edits, shown, round_no=k + 1)
        if _key(new_edits) == _key(edits):
            log.round(k + 1, issues=len(shown), real_issues=len(real), by_type=dict(Counter(i["type"] for i in real)),
                      edits=len(edits), tokens=log.tokens_in + log.tokens_out, unchanged=True)
            return {**state, "rounds": k + 2, "stop": "no_change"}
        prev_issue_keys = keys
        edits = new_edits
        k += 1
