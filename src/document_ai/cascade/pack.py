"""도메인 팩: 문서 종류, 칸 주소 규칙, 연결 표 위치, 항목 번호 패턴, 규칙 표, 값 단위 사전, 코퍼스 경로.

코드는 문서 이름이나 번호 형식을 직접 쓰지 않고 팩을 읽어 동작한다. 팩은 domain_packs/<이름>/pack.yaml
(JSON과 호환되는 YAML, '#' 줄은 주석)이고, 설정의 domain_pack에 이름이나 pack.yaml 경로를 준다.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from . import values

PACKS_DIR = Path(__file__).resolve().parent / "domain_packs"
DEFAULT = "ec_sw"


def read_commented_json(path, reader=None):
    text = reader(path) if reader else Path(path).read_text(encoding="utf-8")
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return json.loads(body)


class DomainPack:
    def __init__(self, d, root):
        self.raw = d
        self.root = Path(root)
        self.is_text = d.get("format") == "text"  # 범용 텍스트 문서 형식(textdocs.py)
        self.id = d["pack_id"]
        self.docs = list(d["docs"])
        self.prompt_domain = d.get("prompt_domain", "")
        r = d["req"]
        self.req_re = re.compile(r["pattern"])
        self.req_ref_re = re.compile(r["ref"])
        self.req_token = r["token"]
        self.req_format = r["format"]
        self.req_noun = r.get("noun", "요구사항")
        self.sec_re = re.compile(d["sec"]["pattern"])
        self.sec_full_re = re.compile(d["sec"]["full"])
        self.req_doc = d["req_doc"]
        self.req_docs = list(d["req_docs"])
        self.sec_docs = list(d["sec_docs"])
        self.status_doc = d.get("status_doc", "")
        self.number_cell = d.get("number_cell", "번호")
        self.title_cell = d.get("title_cell", "제목")
        self.desc_cell = d.get("desc_cell", "설명")
        self.header_cells = (self.number_cell, self.title_cell)
        self.sec_title_cells = [tuple(x) for x in d.get("sec_title_cells", [])]
        self.component_split = int(d.get("component_split", 0))
        self.link_cells = {tuple(x) for x in d["link_cells"]}
        self.link_na_text = d.get("link_na_text", "")
        self.status_cells = tuple(d.get("status_cells", []))
        self.decision_cells = tuple(d.get("decision_cells", []))
        self.layouts = d.get("layouts", {})
        self.insert_style = d.get("insert_style", "")
        self.rules_file = d.get("rules_table") or ""
        self.corpus = d.get("corpus") or None
        self.questions = d.get("questions", {})
        self.values = values.Normalizer(d["units"]) if d.get("units") else values.DEFAULT
        self.site_re = re.compile(r"^\[(" + "|".join(map(re.escape, self.docs)) + r") \| (.+?) \| (.+?)\]$")

    # 항목 번호

    def norm_req(self, text):
        m = re.search(r"\d+", text)
        return self.req_format.format(n=int(m.group(0))) if m else text

    def match_req(self, text):
        m = self.req_re.match(text.strip())
        return self.req_format.format(n=int(m.group(1))) if m else None

    def is_req(self, item):
        return bool(self.req_re.match(item))

    def req_num(self, item):
        return int(self.req_re.match(item).group(1))

    def req_refs(self, text):
        return {self.req_format.format(n=int(n)) for n in self.req_ref_re.findall(text)}

    def ordered_refs(self, text):
        return [self.req_format.format(n=int(n)) for n in self.req_ref_re.findall(text)]

    def same_component(self, a, b):
        s = self.component_split
        return True if not s else (a >= s) == (b >= s)

    # 칸 종류

    def is_link(self, doc, base):
        return (doc, base) in self.link_cells

    def layout(self, doc):
        return self.layouts.get(doc, {})

    def body_cell(self, doc):
        """번호 문단 아래 본문 문단을 담는 칸 이름(item_paragraph 문서)."""
        ip = self.layout(doc).get("item_paragraph")
        return ip.get("body") if isinstance(ip, dict) else None

    def doc_path(self, input_dir, name):
        return Path(input_dir) / (name if self.is_text else f"{name}.docx")

    def with_docs(self, docs):
        """텍스트 형식: 케이스마다 문서 목록이 달라 케이스용 팩을 만든다."""
        return DomainPack({**self.raw, "docs": list(docs)}, self.root)

    def is_table_doc(self, doc):
        return bool(self.layout(doc).get("item_table"))

    # 자료

    def rules_doc(self, reader=None):
        if not self.rules_file:
            return {"rules": [], "decision_cells": [], "na_texts": {}, "applied_texts": {}}
        return read_commented_json(self.root / self.rules_file, reader)

    def rules_path(self):
        return self.root / self.rules_file if self.rules_file else None

    def corpus_dir(self):
        """코퍼스 폴더. 설정의 dir은 작업 공간(pilot_base의 상위 폴더) 기준 상대 경로다."""
        if not self.corpus:
            return None
        from .guard import default_corpus_dir
        d = Path(self.corpus["dir"])
        if d.is_absolute():
            return d
        if self.corpus["dir"].rstrip("/") == "pilot_data/corpus":
            return default_corpus_dir()
        return Path(__file__).resolve().parents[3] / d


@lru_cache(maxsize=None)
def _load(path):
    path = Path(path)
    return DomainPack(read_commented_json(path), path.parent)


def pack_file(name):
    p = Path(str(name))
    if p.suffix in (".yaml", ".json") or p.is_file():
        return p.resolve()
    return PACKS_DIR / str(name) / "pack.yaml"


def load(name=DEFAULT):
    if isinstance(name, DomainPack):
        return name
    return _load(str(pack_file(name or DEFAULT)))
