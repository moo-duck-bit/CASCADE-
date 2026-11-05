"""타입이 있는 편집 연산(CodeAct식 행동)과 실행기.

Editor(edit_mode=ops)는 칸 글을 바로 내지 않고 연산 호출 목록을 낸다. 실행기가 호출마다 위치·인자 타입·대상을
검사하고, 통과한 호출만 편집(사본에 쓸 칸 단위 편집)으로 바꾼다. 실패한 호출은 traceback 형식 오류
(연산, 인자, 오류 유형, 설명)로 돌려주고, 고리에서 Verifier 지적과 한 피드백 메시지로 합쳐 다시 고치게 한다.

연산
- set_cell(site, text): 기존 칸 하나의 글을 바꾼다. 번호 칸과 연결 칸은 대상이 아니다(각각 mark_deleted, add_link/remove_link).
- insert_block(doc, anchor, block_type, fields): anchor 항목 뒤에 새 항목 블록을 넣는다. block_type은 "req",
  fields는 [{name, text}] (칸 이름은 앵커 항목의 칸 이름, 여러 문단 칸은 #pN).
- add_link(sec_id, req_id) / remove_link(sec_id, req_id): 연결 항목의 모든 연결 칸 목록에 Req 번호를 넣거나 뺀다.
- mark_deleted(req_id): 삭제 Req의 칸(번호 칸 제외)을 모두 '삭제'로 표기한다.
"""

import json

OP_NAMES = ("set_cell", "insert_block", "add_link", "remove_link", "mark_deleted")
DELETED = "삭제"
OPS_SCHEMA = {"type": "object", "required": ["ops"], "properties": {"ops": {"type": "array", "items": {
    "type": "object", "required": ["op"],
    "properties": {"op": {"type": "string", "enum": list(OP_NAMES)},
                   "site": {"type": "string"}, "text": {"type": "string"},
                   "doc": {"type": "string"}, "anchor": {"type": "string"}, "block_type": {"type": "string"},
                   "fields": {"type": "array", "items": {"type": "object", "required": ["name", "text"],
                                                         "properties": {"name": {"type": "string"},
                                                                        "text": {"type": "string"}}}},
                   "sec_id": {"type": "string"}, "req_id": {"type": "string"}}}}}}
ARGS = {"set_cell": ("site", "text"), "insert_block": ("doc", "anchor", "block_type", "fields"),
        "add_link": ("sec_id", "req_id"), "remove_link": ("sec_id", "req_id"), "mark_deleted": ("req_id",)}


class OpError(Exception):
    def __init__(self, etype, message, site=""):
        super().__init__(message)
        self.etype = etype
        self.message = message
        self.site = site


def _str_arg(call, name):
    v = call.get(name)
    if not isinstance(v, str) or not v.strip():
        raise OpError("ArgTypeError", f"인자 {name}은(는) 비어 있지 않은 문자열이어야 합니다(받은 값: {v!r}).")
    return v.strip() if name != "text" else v


def _edit(site, new_text, op, kind, anchor=None):
    from .editor import make_edit
    return make_edit(site, new_text, op, "llm", kind, anchor)


class Executor:
    def __init__(self, ch, res, g):
        self.ch, self.res, self.g, self.p = ch, res, g, g.pack
        self.insert_names = {}  # doc → 새 항목 칸 이름(앵커 항목 구조에서 Locator가 정한 것)
        for c in res.candidates:
            if c.kind == "insert":
                doc, item, name = c.site[1:-1].split(" | ")
                self.insert_names.setdefault(doc, []).append(name)

    def new_req(self):
        return self.ch.new_req or ""

    def known_req(self, req):
        return any(self.g.items.get((d, req)) for d in self.p.req_docs) or req == self.new_req()

    def link_cells(self, sec):
        return self.g.trace_cells(sec)

    # 연산

    def set_cell(self, call):
        site, text = _str_arg(call, "site"), _str_arg(call, "text")
        c = self.g.cell(site)
        if c is None:
            raise OpError("SiteNotFound", "원본 문서에 없는 칸 위치입니다. '고칠 칸' 목록의 위치를 글자 그대로 쓰세요.", site)
        if c.base == self.p.number_cell and self.p.is_req(c.item):
            raise OpError("TargetError", "번호 칸은 set_cell로 바꿀 수 없습니다. 삭제는 mark_deleted를 쓰세요.", site)
        if self.p.is_link(c.doc, c.base):
            raise OpError("TargetError", "연결 칸은 set_cell로 바꿀 수 없습니다. add_link 또는 remove_link를 쓰세요.", site)
        return [_edit(site, text, "modify", "set_cell")]

    def insert_block(self, call):
        doc, anchor = _str_arg(call, "doc"), _str_arg(call, "anchor")
        block_type = _str_arg(call, "block_type")
        fields = call.get("fields")
        site = f"[{doc} | {self.new_req()} | {self.p.number_cell}]"
        if block_type != "req":
            raise OpError("ArgTypeError", f"block_type은 'req'만 쓸 수 있습니다(받은 값: {block_type!r}).", site)
        if not isinstance(fields, list) or not fields or not all(
                isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("text"), str) for f in fields):
            raise OpError("ArgTypeError", "fields는 [{name, text}] 목록이어야 합니다.", site)
        if doc not in self.p.req_docs:
            raise OpError("TargetError", f"{doc}에는 새 요구사항 블록을 넣을 수 없습니다(가능: {', '.join(self.p.req_docs)}).", site)
        if not self.g.items.get((doc, anchor)):
            raise OpError("AnchorNotFound", f"{doc}에 앵커 항목 {anchor}이(가) 없습니다.", site)
        if not self.new_req():
            raise OpError("TargetError", "이 요청은 새 항목 추가가 아닙니다.", site)
        if self.g.items.get((doc, self.new_req())):
            raise OpError("TargetError", f"{doc}에 {self.new_req()}이(가) 이미 있습니다.", site)
        allowed = self.insert_names.get(doc, [])
        bad = [f["name"] for f in fields if f["name"] not in allowed]
        if bad:
            raise OpError("TargetError", f"새 블록에 없는 칸 이름 {bad}. 쓸 수 있는 칸: {', '.join(allowed)}", site)
        return [_edit(f"[{doc} | {self.new_req()} | {f['name']}]", f["text"], "insert", "insert", anchor)
                for f in fields]

    def _link(self, call, add):
        sec, req = _str_arg(call, "sec_id"), self.p.norm_req(_str_arg(call, "req_id"))
        cells = self.link_cells(sec)
        if not cells:
            raise OpError("SiteNotFound", f"연결 칸이 있는 항목 {sec}이(가) 없습니다.", f"[{sec}]")
        if add and not self.known_req(req):
            raise OpError("TargetError", f"{req}은(는) 문서에 없고 이번에 추가하는 항목도 아닙니다.", cells[0].site)
        if not add and req not in self.g.reqs_of_sec(sec):
            raise OpError("TargetError", f"{sec}에는 {req}이(가) 연결되어 있지 않습니다.", cells[0].site)
        from .editor import list_add, list_remove
        out = []
        for c in cells:
            new = list_add(c.text, req, self.p) if add else list_remove(c.text, req, self.p)
            out.append(_edit(c.site, new, "modify", "add_link" if add else "remove_link"))
        return out

    def add_link(self, call):
        return self._link(call, True)

    def remove_link(self, call):
        return self._link(call, False)

    def mark_deleted(self, call):
        req = self.p.norm_req(_str_arg(call, "req_id"))
        cells = [c for d in self.p.req_docs for c in self.g.item_cells(d, req)]
        if not cells:
            raise OpError("TargetError", f"문서에 {req}이(가) 없습니다.", f"[{self.p.req_doc} | {req} | {self.p.number_cell}]")
        return [_edit(c.site, DELETED, "mark_deleted", "mark_deleted") for c in cells if c.base != self.p.number_cell]

    def run(self, calls):
        edits, errors, seen = [], [], set()
        for i, call in enumerate(calls):
            name = call.get("op") if isinstance(call, dict) else None
            args = {k: call.get(k) for k in ARGS.get(name, ())} if isinstance(call, dict) else {}
            try:
                if name not in OP_NAMES:
                    raise OpError("UnknownOp", f"없는 연산입니다. 쓸 수 있는 연산: {', '.join(OP_NAMES)}")
                out = getattr(self, name)(call)
            except OpError as err:
                errors.append({"index": i, "op": str(name), "args": args or dict(call or {}), "type": err.etype,
                               "message": err.message, "site": err.site})
                continue
            for e in out:  # 같은 칸을 여러 호출이 고치면 앞 호출을 남긴다
                if e["site"] not in seen:
                    seen.add(e["site"])
                    edits.append(e)
        return edits, errors


def execute(calls, ch, res, g):
    return Executor(ch, res, g).run(calls)


def _fmt_args(args):
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def format_errors(errors):
    """실행 오류를 traceback 형식 글로: 연산 위치, 호출(인자), 오류 유형: 설명."""
    blocks = []
    for e in errors:
        blocks.append(f"Traceback (연산 #{e['index'] + 1}):\n  {e['op']}({_fmt_args(e['args'])})\n"
                      f"{e['type']}: {e['message']}")
    return "\n".join(blocks)


def error_issues(errors):
    """고리에서 Verifier 지적과 함께 다루도록 오류를 지적 형식으로(type=E)."""
    return [{"type": "E", "site": e["site"] or f"[연산 #{e['index'] + 1}]", "detail": f"{e['type']}: {e['message']}"}
            for e in errors]


def to_scorer_edits(edits, keys=("site", "doc", "item", "field", "new_text", "op")):
    return [{k: e[k] for k in keys} | {"source": e.get("source", "")} for e in edits if e.get("report", True)]
