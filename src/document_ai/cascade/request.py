"""구조화 요청 문장 → 변경 객체.

T1 값 변경   : "Req. 2 '계정 잠금'의 잠금 해제 안내 시각을 15분에서 10분으로 변경한다."
T2 속성 변경 : "제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다. …"
T3 추가      : "모바일 애플리케이션 요구사항 Req. 15 '비밀번호 재설정'을(를) 신규 추가한다. 내용: …"
T3 삭제      : "Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다."

변경이 여러 개인 요청(v0.3)
- 속성 여러 개: "제품 사이버보안 속성 'A'(a)을 false에서 true로, 'B'(b)을 true에서 false로 변경한다. …"
  → T2 변경 하나(attr_changes에 속성마다 (키, 이름, 옛 값, 새 값)). 규칙 표는 속성을 함께 바꾼 상태로 계산한다.
- 복합: "<요청 1> 또한 <요청 2>" → 변경 두 개(parse_requests). 각 부분은 위 단일 형식 중 하나다.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from . import pack as pack_mod
from . import rules as rules_mod

T2_RE = re.compile(r"속성\s*'(.+?)'\((\w+)\)\s*(?:을|를)\s*(true|false)에서\s*(true|false)로\s*변경한다")
# 속성 여러 개: "속성 'A'(a)을 x에서 y로, 'B'(b)을 x에서 y로 변경한다"
T2_MULTI_RE = re.compile(r"속성\s*((?:'[^']+'\(\w+\)\s*(?:을|를)\s*(?:true|false)에서\s*(?:true|false)로\s*,\s*)+"
                         r"'[^']+'\(\w+\)\s*(?:을|를)\s*(?:true|false)에서\s*(?:true|false)로)\s*변경한다")
T2_CLAUSE_RE = re.compile(r"'([^']+)'\((\w+)\)\s*(?:을|를)\s*(true|false)에서\s*(true|false)로")
JOIN_RE = re.compile(r"\s또한\s")


@lru_cache(maxsize=None)
def _patterns(token, noun):
    """항목 번호 형식(token)과 항목 명사(noun, EC-SW는 '요구사항')는 도메인 팩에서 온다."""
    t1 = re.compile(rf"^({token})\s*'([^']+)'의\s*(.+?)(?:을|를)\s+(.+?)에서\s+(.+?)(?:\(으\)로|으로|로)\s*변경한다\.?$")
    add = re.compile(rf"^(.+?)\s*{noun}\s*({token})\s*'([^']+)'을\(를\)\s*신규 추가한다\.\s*(?:내용:\s*(.*))?$", re.S)
    dele = re.compile(rf"^({token})\s*'([^']+)'\s*{noun}(?:을|를)\s*삭제한다")
    return t1, add, dele


# 범용 전파 유형(기존 T 이름은 별칭으로 유지): 사실 전파 / 외부 규칙에 의한 전파 / 구조 추가 / 구조 삭제
PROPAGATION = {"T1": "fact", "T2": "constraint", "T3_add": "structural_add", "T3_delete": "structural_delete"}


class RequestError(ValueError):
    pass


@dataclass
class Change:
    kind: str
    origin_req: str = ""
    origin_doc: str = ""          # 텍스트 형식: 출발 위치의 문서(origin_req는 절 제목)
    title: str = ""
    target: str = ""
    old: object = None
    new: object = None
    attr: str = ""
    attr_ko: str = ""
    new_req: str = ""
    component: str = ""
    content: str = ""
    req: str = ""
    text: str = ""
    attr_changes: list = field(default_factory=list)   # T2 속성이 둘 이상일 때만: [(attr, attr_ko, old, new), …]


def attr_changes(ch):
    """T2 변경의 속성 변경 목록. 속성 하나면 [(attr, attr_ko, old, new)] 하나."""
    return list(ch.attr_changes) or [(ch.attr, ch.attr_ko, ch.old, ch.new)]


def _parse_one(t, pk):
    t1_re, add_re, del_re = _patterns(pk.req_token, pk.req_noun)
    m = T2_MULTI_RE.search(t)
    if m:
        parts = [(ko, key, old == "true", new == "true") for ko, key, old, new in T2_CLAUSE_RE.findall(m.group(1))]
        ko, key, old, new = parts[0]
        return Change("T2", attr_ko=ko, attr=key, old=old, new=new, text=t,
                      attr_changes=[(k, n, o, w) for n, k, o, w in parts])
    m = T2_RE.search(t)
    if m:
        return Change("T2", attr_ko=m.group(1), attr=m.group(2), old=m.group(3) == "true", new=m.group(4) == "true",
                      text=t)
    m = add_re.match(t)
    if m:
        return Change("T3_add", component=m.group(1).strip(), new_req=pk.norm_req(m.group(2)), title=m.group(3),
                      content=(m.group(4) or "").strip(), text=t)
    m = del_re.match(t)
    if m:
        return Change("T3_delete", req=pk.norm_req(m.group(1)), title=m.group(2), text=t)
    m = t1_re.match(t)
    if m and pk.is_text:  # "<파일> '<절 제목>'의 값을 X에서 Y(으)로 변경한다."
        return Change("T1", origin_doc=m.group(1), origin_req=m.group(2), title=m.group(2),
                      target=m.group(3).strip(), old=m.group(4).strip(), new=m.group(5).strip(), text=t)
    if m:
        return Change("T1", origin_req=pk.norm_req(m.group(1)), title=m.group(2), target=m.group(3).strip(),
                      old=m.group(4).strip(), new=m.group(5).strip(), text=t)
    raise RequestError(f"구조화 요청 형식이 아닙니다(T1 값 변경, T2 속성 변경, T3 추가·삭제 중 하나여야 함): {t!r}")


def _split(t, pk):
    """'<요청 1> 또한 <요청 2>'를 왼쪽부터 나눈다. 양쪽이 모두 요청 형식으로 읽히는 첫 '또한'에서 자른다
    (추가 요청의 내용 글에 든 '또한'은 뒤쪽이 요청 형식이 아니므로 자르지 않는다). 못 나누면 None."""
    for m in JOIN_RE.finditer(t):
        left, right = t[:m.start()].strip(), t[m.end():].strip()
        try:
            first = _parse_one(left, pk)
            rest = _split(right, pk) or [_parse_one(right, pk)]
        except RequestError:
            continue
        return [first] + rest
    return None


def parse_requests(text, pack=None):
    """요청 문장 → 변경 목록(요청에 적힌 순서). 변경이 하나면 길이 1."""
    pk = pack_mod.load(pack)
    t = " ".join((text or "").split())
    return _split(t, pk) or [_parse_one(t, pk)]


def parse_request(text, pack=None):
    """변경이 하나인 요청. 복합 요청('… 또한 …')은 parse_requests로 읽는다."""
    chs = parse_requests(text, pack)
    if len(chs) > 1:
        raise RequestError(f"변경이 {len(chs)}개인 요청입니다(parse_requests로 읽어야 함): {chs[0].text!r}")
    return chs[0]


def current_attrs(graph, change):
    """문서의 적용 여부로 속성을 읽고, 요청의 '옛 값'과 엇갈리면 요청을 따르되 기록을 남긴다."""
    status = {s: graph.status(s) for s in graph.sec_items()}
    status = {s: v for s, v in status.items() if v in ("적용", "N/A")}
    attrs = rules_mod.infer_attrs(status, graph.rule_table)
    notes = []
    if change.kind == "T2":
        for attr, attr_ko, old, _ in attr_changes(change):
            key = rules_mod.resolve_attr(attr, attr_ko, graph.rule_table) or attr
            if key in attrs and attrs[key] != old:
                notes.append(f"문서에서 읽은 {key}={attrs[key]}가 요청의 옛 값 {old}와 다름(요청을 따름)")
            attrs[key] = old
    return attrs, notes
