"""자연어 요청 → 변경 객체(request_mode=natural). LLM 1회, JSON schema.

전파 유형은 범용 이름으로 정의한다: fact(사실 전파) / constraint(외부 규칙에 의한 전파) / structural_add / structural_delete.
기존 이름 T1·T2·T3_add·T3_delete는 별칭으로 유지한다(request.PROPAGATION).
LLM에게는 요청과 함께 문서의 항목 목록(번호, 제목, 설명 앞부분), 규칙 표의 속성 목록, 구성요소 목록을 준다.
새 항목 번호는 LLM이 정하지 않고 코드가 같은 구성요소의 마지막 번호 다음으로 정한다.
필수 필드가 비었거나 문서에 없는 항목·속성을 가리키면 IntentError(무엇이 문제인지 적어서)로 실패를 기록한다.

intent_v2(v0.3 전): 요청 하나에 변경이 여러 개일 수 있어 LLM은 변경 객체 목록({"changes": […]})을 낸다.
constraint가 여럿이면 T2 변경 하나로 묶는다(attr_changes). 새 항목 번호가 겹치면 다음 번호를 쓴다.
"""

from .llm import load_prompt
from .request import Change
from . import rules as rules_mod

PROMPT = "intent_v2"
TYPES = {"fact": "T1", "constraint": "T2", "structural_add": "T3_add", "structural_delete": "T3_delete"}
FIELDS = ("type", "origin_item", "target", "old", "new", "attr", "component", "title", "content", "item", "reason")
ITEM_SCHEMA = {"type": "object", "required": list(FIELDS),
               "properties": {k: ({"type": "string", "enum": list(TYPES)} if k == "type" else {"type": "string"})
                              for k in FIELDS}}
# minItems 같은 배열 제약은 서버 쪽 구조화 출력에서 멈춤을 일으켜 넣지 않고, 빈 목록은 코드가 실패로 처리한다
SCHEMA = {"type": "object", "required": ["changes"],
          "properties": {"changes": {"type": "array", "items": ITEM_SCHEMA}}}


class IntentError(ValueError):
    pass


def _title(g, req):
    for c in g.item_cells(g.pack.req_doc, req):
        if c.base == g.pack.title_cell:
            return c.text.strip()
    return ""


def _desc(g, req, n=150):
    for c in g.item_cells(g.pack.req_doc, req):
        if c.base == g.pack.desc_cell:
            return " ".join(c.text.split())[:n]
    return ""


def components(g):
    """구성요소: 팩의 component_split으로 나눈 Req 묶음. [(이름, [Req …])]"""
    p = g.pack
    reqs = sorted(g.req_items(), key=p.req_num)
    if not reqs:
        return []
    groups = {}
    for r in reqs:
        key = p.req_num(r) >= p.component_split if p.component_split else False
        groups.setdefault(key, []).append(r)
    out = []
    for i, (_, rs) in enumerate(sorted(groups.items())):
        sample = ", ".join(_title(g, r) for r in rs[:3])
        out.append((f"구성요소{i + 1}({rs[0]}~{rs[-1]}: {sample})", rs))
    return out


def _attr_lines(g):
    from .request import current_attrs
    attrs, _ = current_attrs(g, Change("T1"))
    return [f"- {r['attr']}: {r['attr_ko']} (지금: {str(attrs.get(r['attr'], '알 수 없음')).lower()})"
            for r in g.rule_table]


def parse_natural_all(text, g, llm, log):
    """자연어 요청 → 변경 목록(요청 순서). constraint 여러 개는 T2 변경 하나로 묶는다."""
    p = g.pack
    system, tpl = load_prompt(PROMPT, p)
    items = [f"- {r} '{_title(g, r)}': {_desc(g, r)}" for r in sorted(g.req_items(), key=p.req_num)]
    comps = components(g)
    user = tpl.format(request=text.strip(), items="\n".join(items) or "(없음)",
                      attrs="\n".join(_attr_lines(g)) or "(없음)",
                      components="\n".join(f"- {name}" for name, _ in comps) or "(없음)")
    out, usage = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                               SCHEMA, "intent")
    log.llm_usage("intent", usage)
    return to_changes(out, text, g, comps)


def parse_natural(text, g, llm, log):
    """변경이 하나인 자연어 요청(여럿이면 IntentError). 복합 요청은 parse_natural_all로 읽는다."""
    chs = parse_natural_all(text, g, llm, log)
    if len(chs) > 1:
        raise IntentError(f"변경이 {len(chs)}개인 요청(parse_natural_all로 읽어야 함)")
    return chs[0]


def to_changes(out, text, g, comps=None):
    """LLM 출력({"changes": […]}, 또는 옛 형식의 객체 하나) → 변경 목록."""
    objs = out["changes"] if isinstance(out, dict) and "changes" in out else [out]
    if not objs:
        raise IntentError("변경 객체가 하나도 없음")
    chs, t2, used = [], None, set()
    for o in objs:
        ch = to_change(o, text, g, comps)
        if ch.kind == "T2":
            if t2 is None:
                t2 = ch
                chs.append(ch)
            elif ch.attr not in {a for a, *_ in t2.attr_changes or [(t2.attr,)]}:
                t2.attr_changes = (t2.attr_changes or [(t2.attr, t2.attr_ko, t2.old, t2.new)]) + \
                    [(ch.attr, ch.attr_ko, ch.old, ch.new)]
            continue
        if ch.kind == "T3_add":
            while ch.new_req in used:
                ch.new_req = g.pack.req_format.format(n=g.pack.req_num(ch.new_req) + 1)
            used.add(ch.new_req)
        chs.append(ch)
    return chs


def _need(out, *names):
    miss = [n for n in names if not str(out.get(n, "")).strip()]
    if miss:
        raise IntentError(f"{out.get('type')} 요청에 필수 필드가 비어 있음: {', '.join(miss)}")


def _bool(v, name):
    s = str(v).strip().lower()
    if s not in ("true", "false"):
        raise IntentError(f"constraint의 {name}은(는) true/false여야 함: {v!r}")
    return s == "true"


def to_change(out, text, g, comps=None):
    p = g.pack
    kind = TYPES.get(out.get("type"))
    if kind is None:
        raise IntentError(f"알 수 없는 전파 유형: {out.get('type')!r}")
    reqs = set(g.req_items())
    if kind == "T1":
        _need(out, "origin_item", "target", "old", "new")
        req = p.norm_req(out["origin_item"])
        if req not in reqs:
            raise IntentError(f"출발 항목 {req}이(가) 문서에 없음")
        return Change("T1", origin_req=req, title=_title(g, req), target=out["target"].strip(),
                      old=out["old"].strip(), new=out["new"].strip(), text=text.strip())
    if kind == "T2":
        _need(out, "attr", "old", "new")
        attr = rules_mod.resolve_attr(out["attr"].strip(), "", g.rule_table)
        rule = next((r for r in g.rule_table if r["attr"] == attr), None)
        if rule is None:
            raise IntentError(f"속성 목록에 없는 attr: {out['attr']!r}")
        return Change("T2", attr=attr, attr_ko=rule["attr_ko"], old=_bool(out["old"], "old"),
                      new=_bool(out["new"], "new"), text=text.strip())
    if kind == "T3_add":
        _need(out, "component", "title", "content")
        comps = comps if comps is not None else components(g)
        if not comps:
            raise IntentError("문서에 기존 항목이 없어 새 번호를 정할 수 없음")
        name, rs = next(((n, r) for n, r in comps if n.split("(")[0] in out["component"] or r[0] in out["component"]),
                        comps[0])
        new_req = p.req_format.format(n=max(p.req_num(r) for r in rs) + 1)
        return Change("T3_add", component=out["component"].strip(), new_req=new_req, title=out["title"].strip(),
                      content=out["content"].strip(), text=text.strip())
    _need(out, "item")
    req = p.norm_req(out["item"])
    if req not in reqs:
        raise IntentError(f"삭제할 항목 {req}이(가) 문서에 없음")
    return Change("T3_delete", req=req, title=_title(g, req), text=text.strip())
