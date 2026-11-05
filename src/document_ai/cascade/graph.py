"""칸 그래프와 색인. 입력 docx에서 만든 칸 목록만 쓴다(연결표·사실 표 같은 정답 쪽 자료는 쓰지 않는다).

엣지 5종
- contains: 문서 → 항목 → 칸
- same_id: 문서가 달라도 항목 이름이 같은 항목(Req 번호, 보안 항목 번호)
- trace: 연결 칸(팩의 link_cells: EC-SW는 MDSR 6절 표 'Req 번호' 칸, XXCS '해당 요구사항' 칸)에 적힌 Req ↔ 연결 항목
- value: 정규화한 값이 같은 칸끼리(값 → 칸 색인)
- rule: 속성 → 규칙 표가 정하는 연결 항목
문서 이름·번호 형식·연결 칸 위치는 도메인 팩에서 읽는다.
"""

from collections import defaultdict

from . import pack as pack_mod
from . import rules as rules_mod
from . import values


def req_refs(text, pack=None):
    return pack_mod.load(pack).req_refs(text)


class CellGraph:
    def __init__(self, cells, rule_table=None, pack=None):
        self.cells = cells
        self.pack = pack_mod.load(pack)
        self.values = self.pack.values
        self.rules_doc = self.pack.rules_doc()
        self.rule_table = rule_table if rule_table is not None else self.rules_doc["rules"]
        self.by_site = {}
        self.items = defaultdict(list)          # (doc, item) → cells
        self.docs = defaultdict(list)           # doc → items(순서)
        for c in cells:
            self.by_site.setdefault(c.site, c)
            if (c.doc, c.item) not in self.items:
                self.docs[c.doc].append(c.item)
            self.items[(c.doc, c.item)].append(c)
        self.item_docs = defaultdict(set)
        for doc, item in self.items:
            self.item_docs[item].add(doc)
        self.req_secs = defaultdict(set)
        self.sec_reqs = defaultdict(set)
        self.trace_src = defaultdict(list)      # sec → 연결 칸
        for c in cells:
            if self.pack.is_link(c.doc, c.base) and self.pack.sec_full_re.match(c.item):
                self.trace_src[c.item].append(c)
                for r in self.pack.req_refs(c.text):
                    self.req_secs[r].add(c.item)
                    self.sec_reqs[c.item].add(r)
        self.mentions = {}
        self.value_index = defaultdict(list)    # 정규화 키 → cells
        self.conv_index = defaultdict(list)
        for c in cells:
            ms = self.values.mentions(c.text)
            self.mentions[id(c)] = ms
            for k in {m.key for m in ms}:
                self.value_index[k].append(c)
            for k in {m.conv_key for m in ms}:
                self.conv_index[k].append(c)

    # 조회

    def cell(self, site):
        return self.by_site.get(site)

    def item_cells(self, doc, item):
        return list(self.items.get((doc, item), []))

    def same_id(self, doc, item):
        return {(d, item) for d in self.item_docs[item] if d != doc}

    def secs_of_req(self, req):
        return set(self.req_secs.get(req, set()))

    def reqs_of_sec(self, sec):
        return set(self.sec_reqs.get(sec, set()))

    def trace_cells(self, sec):
        return list(self.trace_src.get(sec, []))

    def cell_mentions(self, c):
        return self.mentions.get(id(c), [])

    def value_cells(self, surface, normalized=True, converted=False):
        """옛 값을 담은 칸. normalized=False면 표기 글자가 같은 칸만(값 색인 끔), 숫자 없는 값은 느슨한 비교."""
        if not normalized:
            needle = surface.strip()
            return [c for c in self.cells if needle and needle in c.text]
        key = self.values.key_of(surface)
        if key is None:
            return [c for c in self.cells if values.loose_contains(c.text, surface)]
        hits = list(self.value_index.get(key, []))
        if converted:
            ck = self.values.conv_key_of(surface)
            seen = {id(c) for c in hits}
            hits += [c for c in self.conv_index.get(ck, []) if id(c) not in seen]
        return hits

    def rule_secs(self, attr):
        return rules_mod.rule_secs(attr, self.rule_table)

    def status(self, sec):
        """판단 문서(EC-SW는 XXCS)의 적용 여부. 없으면 연결 칸의 '해당 없음' 표기로 추정."""
        p = self.pack
        for c in self.items.get((p.status_doc, sec), []):
            if c.cell in p.status_cells:
                t = c.text.strip()
                return "N/A" if t.upper().startswith("N/A") else ("적용" if t.startswith("적용") else t)
        for doc in p.sec_docs:
            for c in self.items.get((doc, sec), []):
                if p.is_link(doc, c.base) and p.link_na_text:
                    return "N/A" if p.link_na_text in c.text else "적용"
        return None

    def sec_items(self):
        out = []
        for doc in self.pack.sec_docs:
            for item in self.docs.get(doc, []):
                if self.pack.sec_full_re.match(item) and item not in out:
                    out.append(item)
        return out

    def req_items(self, doc=None):
        return [i for i in self.docs.get(doc or self.pack.req_doc, []) if self.pack.is_req(i)]

    def neighbors(self, c):
        """1-hop 이웃 칸: 같은 항목, same_id 항목, trace로 이어진 항목의 칸."""
        out = [x for x in self.items[(c.doc, c.item)] if x is not c]
        for d, i in self.same_id(c.doc, c.item):
            out += self.items[(d, i)]
        linked = set()
        if self.pack.is_req(c.item):
            linked = {(d, s) for s in self.secs_of_req(c.item) for d in self.pack.sec_docs}
        elif self.pack.sec_full_re.match(c.item):
            linked = {(d, r) for r in self.reqs_of_sec(c.item) for d in self.pack.req_docs}
        for key in linked:
            out += self.items.get(key, [])
        return out

    def n_edges(self, kind):
        if kind == "contains":
            return len(self.items) + len(self.cells)
        if kind == "same_id":
            return sum(len(v) * (len(v) - 1) // 2 for v in self.item_docs.values())
        if kind == "trace":
            return sum(len(v) for v in self.req_secs.values())
        if kind == "value":
            return sum(len(v) * (len(v) - 1) // 2 for v in self.value_index.values())
        if kind == "rule":
            return sum(len(r["secs"]) for r in self.rule_table)
        raise ValueError(kind)
