"""범용 텍스트 문서(markdown·reStructuredText·txt) 형식 층. 형식 팩(format=text)일 때 cells·apply가 이 모듈을 쓴다.

- 문서: input/ 아래 .md .rst .txt 파일(request*.txt 제외). 문서 이름은 input/ 기준 상대 경로.
- 칸: 절(제목) 안 단위. 종류는 para(빈 줄로 나뉜 문단), list(목록 항목), table(표 줄), code(코드 블록의 한 줄), heading.
  주소 "[파일 | 절 제목 | 종류#번호]", 번호는 절 안에서 종류별 1부터. 칸마다 원본 줄 범위(lines)를 단다.
- 쓰기: 칸의 줄 범위를 새 글로 바꾼다(아래 칸부터 바꿔 줄 번호가 밀리지 않게). 원본은 건드리지 않는다.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

EXT = (".md", ".rst", ".txt")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#\.)\s+")
TABLE_RE = re.compile(r"^\s*(?:\|.*\||\+[-=+]+\+)\s*$")
UNDER_RE = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(eq=False)
class TextCell:
    doc: str
    item: str
    cell: str
    text: str
    lines: tuple = (0, 0)
    para: object = field(default=None, repr=False)
    block: object = field(default=None, repr=False)

    @property
    def site(self):
        return f"[{self.doc} | {self.item} | {self.cell}]"

    @property
    def base(self):
        return self.cell.split("#")[0]


def doc_names(input_dir):
    root = Path(input_dir)
    out = [p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))
           if p.is_file() and p.suffix in EXT and not (p.parent == root and p.name.startswith("request"))]
    return out


def _is_heading(lines, i):
    s = lines[i].strip()
    if s.startswith("#") and re.match(r"^#{1,6}\s", s):
        return 1
    if i + 1 < len(lines) and s and UNDER_RE.match(lines[i + 1].strip()) and len(lines[i + 1].strip()) >= len(s) * 0.5:
        return 2
    return 0


def segment(lines):
    """줄 목록 → [(시작, 끝, 종류)] (1부터, 끝 포함)."""
    out, i, n = [], 0, len(lines)
    in_code, literal = False, False
    while i < n:
        ln, s = lines[i], lines[i].strip()
        if FENCE_RE.match(ln):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            if s:
                out.append((i + 1, i + 1, "code"))
            i += 1
            continue
        if not s:
            i += 1
            continue
        if literal and (ln.startswith("  ") or ln.startswith("\t")):
            out.append((i + 1, i + 1, "code"))
            i += 1
            continue
        literal = False
        h = _is_heading(lines, i)
        if h:
            out.append((i + 1, i + 1, "heading"))
            i += h
            continue
        if UNDER_RE.match(s):
            i += 1
            continue
        if TABLE_RE.match(ln):
            out.append((i + 1, i + 1, "table"))
            i += 1
            continue
        kind = "list" if LIST_RE.match(ln) else "para"
        j = i + 1
        while j < n and lines[j].strip() and not LIST_RE.match(lines[j]) and not TABLE_RE.match(lines[j]) \
                and not FENCE_RE.match(lines[j]) and not _is_heading(lines, j):
            j += 1
        out.append((i + 1, j, kind))
        literal = s.endswith("::")
        i = j
    return out


def _title(line):
    return line.strip().lstrip("#").strip().replace("|", "¦").replace("[", "(").replace("]", ")") or "제목 없음"


def parse_text(doc, text):
    lines = text.splitlines()
    out, section, counts = [], "문서 시작", {}
    for a, b, kind in segment(lines):
        if kind == "heading":
            section = _title(lines[a - 1])
            counts = {}
            out.append(TextCell(doc, section, "heading", lines[a - 1].strip(), (a, b)))
            continue
        counts[kind] = counts.get(kind, 0) + 1
        body = "\n".join(lines[a - 1:b])
        out.append(TextCell(doc, section, f"{kind}#{counts[kind]}", " ".join(body.split()) if kind == "para" else body.strip(),
                            (a, b)))
    seen, uniq = set(), []
    for c in out:  # 같은 절 이름이 반복되면 뒤 칸 주소가 겹친다: 겹치는 칸은 번호를 이어 붙인다
        while c.site in seen:
            c.cell += "+"
        seen.add(c.site)
        uniq.append(c)
    return uniq


def parse_text_case(input_dir, reader=None):
    out = []
    for name in doc_names(input_dir):
        p = Path(input_dir) / name
        data = reader(p) if reader else p.read_bytes()
        out += parse_text(name, data.decode("utf-8", errors="replace"))
    return out


def apply_text_edits(input_dir, out_dir, edits, reader, result, split_site):
    input_dir, out_dir = Path(input_dir), Path(out_dir)
    by_doc = {}
    for e in edits:
        parts = split_site(e["site"])
        if not parts:
            result.failed.append({"site": e["site"], "reason": "bad site"})
            continue
        by_doc.setdefault(parts[0], []).append(e)
    names = doc_names(input_dir)
    for name in names:
        p = input_dir / name
        data = (reader(p) if reader else p.read_bytes()).decode("utf-8", errors="replace")
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name not in by_doc:
            target.write_text(data, encoding="utf-8")
            continue
        cells = {c.site: c for c in parse_text(name, data)}
        lines = data.splitlines()
        todo = []
        for e in by_doc[name]:
            c = cells.get(e["site"])
            if c is None or e.get("op") == "insert":
                result.failed.append({"site": e["site"], "reason": "site not found"})
                continue
            todo.append((c.lines, e))
        for (a, b), e in sorted(todo, key=lambda t: -t[0][0]):
            lines[a - 1:b] = e["new_text"].splitlines() or [""]
            result.applied.append(e["site"])
        target.write_text("\n".join(lines) + ("\n" if data.endswith("\n") else ""), encoding="utf-8")
    for name in by_doc:
        if name not in names:
            result.failed += [{"site": e["site"], "reason": f"{name} not in input"} for e in by_doc[name]]
    return result
