"""Editor: 관련 칸을 고친다.

구조로 값이 정해지는 칸은 코드가 쓴다(source="rule").
- T3 삭제: 삭제 Req 칸은 "삭제", 연결 칸은 목록에서 그 Req 번호를 뺀다.
- T3 추가: 새 Req 번호·제목 칸, 관련 보안 항목 연결 칸에 새 번호를 번호 순서로 넣는다.
- T2: 적용 여부 칸은 규칙 결과, N/A가 되는 행의 입증 방법·판정 칸은 "해당 없음".
나머지 칸은 LLM 1회 호출로 쓴다(source="llm"). 맥락은 관련 칸과 같은 항목의 다른 칸만 준다.
edit_mode=free_json이면 LLM이 칸마다 글을 내고, ops(기본)이면 편집 연산 호출 목록을 내며 실행기(ops.py)가
검증해 편집으로 바꾼다. 실행 오류는 Edits.errors에 남아 고리에서 Verifier 지적과 함께 피드백된다.
"""

import json
import re

from . import pack as pack_mod
from . import rules as rules_mod
from .cells import split_site
from .llm import load_prompt
from .locator import Cand, change_info
from .ops import OPS_SCHEMA, execute, format_errors
from .postfix import postfix

PROMPT = "editor_v1"
PROMPT_OPS = "editor_ops_v1"
WIDEN = ("V2b", "V6b")
FEEDBACK = "아래 지적은 문제일 수도 아닐 수도 있다. 확인 후 지적된 칸만 수정하고, 나머지 편집은 그대로 유지한 최종 JSON을 출력하라."
EMPTY_LIST = "-"
SCHEMA = {"type": "object", "required": ["edits"], "properties": {"edits": {"type": "array", "items": {
    "type": "object", "required": ["site", "new_text"],
    "properties": {"site": {"type": "string"}, "new_text": {"type": "string"}}}}}}


class Edits(list):
    """편집 목록 + 마지막 LLM 출력의 실행 오류(errors)와 연산 호출(calls)."""

    def __init__(self, items=(), errors=(), calls=()):
        super().__init__(items)
        self.errors = list(errors)
        self.calls = list(calls)


def make_edit(site, new_text, op="modify", source="llm", kind="", anchor=None):
    parts = split_site(site) or ("", "", "")
    e = {"site": site, "doc": parts[0], "item": parts[1], "field": parts[2], "op": op, "new_text": new_text,
         "source": source, "kind": kind}
    if anchor:
        e["anchor"] = anchor
    return e


def _req_key(r):
    return int(re.sub(r"\D", "", r))


def list_add(text, req, pack=None):
    reqs = pack_mod.load(pack).ordered_refs(text)
    if req not in reqs:
        reqs.append(req)
    return ", ".join(sorted(reqs, key=_req_key))


def list_remove(text, req, pack=None):
    reqs = [r for r in pack_mod.load(pack).ordered_refs(text) if r != req]
    return ", ".join(reqs) if reqs else EMPTY_LIST


def structural_edits(ch, res, g, rules_doc):
    out, p = {}, g.pack
    for c in res.relevant:
        if c.kind == "delete":
            out[c.site] = make_edit(c.site, "삭제", "mark_deleted", "rule", c.kind)
        elif c.kind == "unlink":
            out[c.site] = make_edit(c.site, list_remove(c.cell.text, ch.req, p), "modify", "rule", c.kind)
        elif c.kind == "link":
            out[c.site] = make_edit(c.site, list_add(c.cell.text, ch.new_req, p), "modify", "rule", c.kind)
        elif c.kind == "insert":
            doc, _, field = split_site(c.site)
            if field == p.number_cell:
                out[c.site] = make_edit(c.site, ch.new_req, "insert", "rule", c.kind, c.note)
                if p.body_cell(doc):  # 문단형 문서(MDDR)의 번호는 본문 문단 묶음의 제목 문단: 문서에는 쓰고 칸 편집으로는 보고하지 않는다
                    out[c.site]["report"] = False
            elif field == p.title_cell:
                out[c.site] = make_edit(c.site, ch.title, "insert", "rule", c.kind, c.note)
        elif c.kind == "rule" and ch.kind == "T2":
            after = (c.note.split("→")[1] if "→" in c.note else
                     rules_mod.FLIP.get(g.status(c.sec) or "", ""))
            texts = rules_doc["na_texts"] if after == "N/A" else rules_doc["applied_texts"]
            if c.cell.base in texts:
                out[c.site] = make_edit(c.site, texts[c.cell.base], "modify", "rule", c.kind)
    return out


def instructions(ch, g, res):
    if ch.kind == "T1":
        return (f"각 칸에서 '{ch.target}'을(를) 가리키는 값 {ch.old}을(를) {ch.new}(으)로 바꿉니다. "
                "같은 칸에 있는 다른 사실의 값은 그대로 둡니다.")
    if ch.kind == "T2":
        flips = res.rule_info.get("flips") or []
        desc = ", ".join(f"{f['sec']} {f['before']}→{f['after']}" for f in flips)
        return (f"속성 변경으로 보안 항목의 적용 여부가 바뀝니다({desc}). 새로 적용되는 항목의 입증 방법은 "
                "그 보안 요구사항을 확인하는 방법을 한 문장으로 쓰고, 판정은 시험 전이므로 '시험 예정'으로 씁니다. "
                "적용 여부가 N/A가 되는 행은 '해당 없음'으로 씁니다.")
    if ch.kind == "T3_add":
        return (f"새 {g.pack.req_noun} {ch.new_req} '{ch.title}'의 칸을 씁니다. 요청의 내용을 바탕으로, 참고 맥락의 기존 "
                f"{g.pack.req_noun}과(와) 같은 형식({g.pack.insert_style})으로 씁니다.")
    return f"삭제되는 요구사항 {ch.req}을(를) 가리키는 부분을 지우거나 '삭제'로 바꿉니다."


def context_lines(ch, g, res, targets, config):
    tsites = {c.site for c in targets}
    keys = []
    for c in targets:
        if c.cell is not None and (c.cell.doc, c.cell.item) not in keys:
            keys.append((c.cell.doc, c.cell.item))
    if ch.kind == "T3_add":
        for doc, anchor in res.anchors.items():
            keys.insert(0, (doc, anchor))
    lines = []
    for doc, item in keys:
        for c in g.item_cells(doc, item):
            if c.site not in tsites:
                lines.append(f"{c.site} {' '.join(c.text.split())[:config.context_chars]}")
    return lines


def _evidence(res):
    if not getattr(res, "evidence", None):
        return ""
    from .retrieval import evidence_text
    return "\n\n" + evidence_text(res.evidence)


def _messages(ch, g, res, targets, fixed, config):
    system, tpl = load_prompt(PROMPT_OPS if config.edit_mode == "ops" else PROMPT, g.pack)
    tlines = []
    for c in targets:
        cur = "(새 칸)" if c.cell is None else " ".join(c.cell.text.split())
        if c.cell is None and config.edit_mode == "ops":
            cur = f"(새 칸, insert_block 앵커 {c.note})"
        tlines.append(f"{c.site} {cur}")
    fixed_txt = ""
    if fixed:
        fixed_txt = "\n## 코드가 이미 정한 칸(출력하지 않음)\n" + "\n".join(f"{e['site']} {e['new_text']}"
                                                                for e in fixed.values()) + "\n"
    user = tpl.format(request=ch.text, change_info=change_info(ch), instructions=instructions(ch, g, res),
                      targets="\n".join(tlines), fixed=fixed_txt,
                      context=("\n".join(context_lines(ch, g, res, targets, config)) or "(없음)") + _evidence(res))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _llm_edits(out, targets):
    by_site = {c.site: c for c in targets}
    edits = []
    for e in out["edits"]:
        site = e["site"].strip()
        c = by_site.get(site)
        op = "insert" if (c and c.kind == "insert") else "modify"
        edits.append(make_edit(site, postfix(e["new_text"]), op, "llm", c.kind if c else "outside",
                               c.note if (c and c.kind == "insert") else None))
    return edits


def _ops_edits(out, targets, ch, res, g):
    """연산 호출 → (편집, 실행 오류). 글에는 free_json과 같은 후처리를 한다."""
    by_site = {c.site: c for c in targets}
    edits, errors = execute(out["ops"], ch, res, g)
    for e in edits:
        e["new_text"] = postfix(e["new_text"])
        c = by_site.get(e["site"])
        if c is not None:
            e["kind"] = c.kind
    return edits, errors


def _call_llm(ch, res, g, config, llm, log, msgs, targets, name):
    if config.edit_mode == "ops":
        out, usage = llm.chat_json(msgs, OPS_SCHEMA, name)
        edits, errors = _ops_edits(out, targets, ch, res, g)
        calls = out["ops"]
    else:
        out, usage = llm.chat_json(msgs, SCHEMA, name)
        edits, errors, calls = _llm_edits(out, targets), [], out["edits"]
    log.llm_usage(name, usage)
    return edits, errors, calls


def edit(ch, res, g, config, llm, log, rules_doc=None):
    rules_doc = rules_doc or g.rules_doc
    fixed = structural_edits(ch, res, g, rules_doc)
    targets = [c for c in res.relevant if c.site not in fixed]
    edits = Edits(fixed.values())
    if targets:
        msgs = _messages(ch, g, res, targets, fixed, config)
        new, errors, calls = _call_llm(ch, res, g, config, llm, log, msgs, targets, "editor")
        edits.errors, edits.calls = errors, calls
        log.stage("editor", context_chars=sum(len(m["content"]) for m in msgs), exec_errors=len(errors))
        seen = set(fixed)
        for e in new:
            if e["site"] not in seen:
                seen.add(e["site"])
                edits.append(e)
    if config.mode == "ledger_like":  # 후보 전부를 넘겼으므로 바뀌지 않은 칸은 편집이 아니다
        cur = {c.site: c.cell.text for c in res.relevant if c.cell is not None}
        edits = Edits([e for e in edits if " ".join(cur.get(e["site"], "").split()) != " ".join(e["new_text"].split())],
                      edits.errors, edits.calls)
    log.stage("editor", targets=len(targets), structural=len(fixed), edits=len(edits))
    return edits


def feedback_message(issues, errors=()):
    """실행 오류(traceback)와 Verifier 지적을 한 메시지로."""
    parts = [FEEDBACK]
    if errors:
        parts.append("## 실행 오류\n" + format_errors(errors))
    checks = [i for i in issues if i["type"] != "E"]
    if checks:
        parts.append("## 지적\n" + "\n".join(f"- {i['type']} {i['site']}: {i['detail']}" for i in checks))
    return "\n\n".join(parts)


def revise(ch, res, g, config, llm, log, prev, issues, round_no=1):
    """지적된 칸만 다시 쓴다. LLM은 최종 편집 전체를 내지만, 지적되지 않은 칸은 이전 편집을 그대로 둔다.

    issues에는 Verifier 지적과 실행 오류(type=E, prev.errors)가 함께 들어온다."""
    errors = getattr(prev, "errors", [])
    flagged = {i["site"] for i in issues}
    prev_by = {e["site"]: e for e in prev}
    targets = [c for c in res.relevant if c.site not in {e["site"] for e in prev if e["source"] == "rule"}]
    # Locator 독립 검사(V2b·V6b)가 지적한 칸이 고칠 칸 목록에 없으면 현재 글과 함께 더한다
    have = {c.site for c in targets}
    for i in issues:
        c = g.cell(i["site"]) if i["type"] in WIDEN else None
        if c is not None and i["site"] not in have:
            targets.append(Cand(i["site"], "verifier", c))
            have.add(i["site"])
            res.verifier_scope.add(i["site"])
    fixed = {e["site"]: e for e in prev if e["source"] == "rule"}
    msgs = _messages(ch, g, res, targets, fixed, config)
    if config.edit_mode == "ops":
        prev_out = {"ops": getattr(prev, "calls", [])}
    else:
        prev_out = {"edits": [{"site": e["site"], "new_text": e["new_text"]} for e in prev if e["source"] != "rule"]}
    msgs.append({"role": "assistant", "content": json.dumps(prev_out, ensure_ascii=False)})
    msgs.append({"role": "user", "content": feedback_message(issues, errors)})
    new, new_errors, calls = _call_llm(ch, res, g, config, llm, log, msgs, targets, f"editor_r{round_no}")
    orig = {c.site: " ".join(c.text.split()) for c in g.cells}
    # 원문과 같은 글은 편집이 아니다(지적을 확인하고 그대로 둔 경우)
    new_by = {e["site"]: e for e in new if orig.get(e["site"]) != " ".join(e["new_text"].split())}
    final = []
    for site, e in prev_by.items():
        if site not in flagged or e["source"] == "rule":
            final.append(e)
        elif site in new_by:
            final.append(new_by[site])
        # 지적된 칸이 새 출력에 없으면 그 편집을 뺀다(예: 후보 밖 수정 지적)
    target_sites = {c.site for c in targets}
    for site, e in new_by.items():
        # 새로 생긴 편집은 지적된 칸이거나, 실행 오류로 비어 있던 고칠 칸을 채우는 경우만 받는다
        if site not in prev_by and (site in flagged or (errors and site in target_sites)):
            final.append(e)
    return Edits(final, new_errors, calls)
