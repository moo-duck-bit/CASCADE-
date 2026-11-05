"""공개 규정 PDF 3개(corpus/guides/)를 조항·문단 단위 청크로 쪼개 regs.jsonl, regs.sha256, clause_ids.csv를 만든다.

- 첨부3(사이버보안 가이드라인): '□ IA-05 제목' 보안 항목마다 세부 요구사항·시험기준·시험방법·적용 예외사항을 각각 한 청크로.
  조항 ID 예: 첨부3-IA-05-세부요구사항, 첨부3-IA-05-시험기준, 첨부3-IA-05-시험방법, 첨부3-IA-05-예외.
  보안 항목 밖의 본문은 쪽 안 문단 묶음(첨부3-p12-c1).
- 첨부4(디지털의료기기SW 가이드라인), 첨부5(보완사례집): 쪽 안 문단 묶음(첨부4-p20-c1). 절 제목은 가까운 제목 줄.
첨부4·5는 검색 코퍼스에만 쓰고 규칙 표 근거로는 쓰지 않는다(규칙 표는 첨부3 적용 예외사항만).
PDF는 git에 올리지 않는다(corpus/.gitignore). 산출물 세 파일만 커밋한다.

사용: python corpus/build_corpus.py   (pypdf 필요)
"""
import csv
import hashlib
import json
import re
from pathlib import Path

import pypdf

HERE = Path(__file__).resolve().parent
GUIDES = HERE / "guides"
SOURCES = [("첨부3", "guide3_cybersecurity.pdf", "의료기기 사이버보안 가이드라인"),
           ("첨부4", "guide4_dmsw.pdf", "디지털의료기기소프트웨어 가이드라인"),
           ("첨부5", "guide5_supplement_cases.pdf", "의료기기 소프트웨어 허가심사 보완사례집")]
SEC_HEAD = re.compile(r"^□\s*((?:IA|UC|SI|DC|TRE|RA)-\d{2})\s+(.+)$")
FIELDS = [("세부요구사항", re.compile(r"^세부\s*$|^세부\s*요구사항")), ("시험기준", re.compile(r"^시험기준")),
          ("시험방법", re.compile(r"^시험방법")), ("예외", re.compile(r"^적용\s*$|^적용\s*예외사항"))]
PAGE_NO = re.compile(r"^-\s*\d+\s*-$")
# 보안 항목 표가 끝나는 줄: 장 번호(Ⅳ …)나 분류 제목('사용 통제(UC, Use Control)')
SEC_END = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦ]|^[가-힣 ]{2,14}\((?:IA|UC|SI|DC|TRE|RA), [A-Za-z ]+\)$")
HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+\S|[가-하]\.\s+\S|\(\d+\)\s*\S|□\s*\S|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ)")
MAX_CHARS = 700


def pages_of(path):
    return [p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages]


def clean_lines(text):
    return [ln.rstrip() for ln in text.splitlines() if ln.strip() and not PAGE_NO.match(ln.strip())]


def join(lines):
    return re.sub(r"\s+", " ", " ".join(ln.strip() for ln in lines)).strip()


def paragraph_chunks(tag, title, page_no, lines, heading):
    """쪽 안 문단 묶음. 제목 줄에서 끊고, MAX_CHARS를 넘으면 나눈다."""
    out, buf, k = [], [], 0

    def flush():
        nonlocal buf, k
        text = join(buf)
        if len(text) >= 20:
            k += 1
            out.append({"clause_id": f"{tag}-p{page_no}-c{k}", "source": tag, "source_title": title,
                        "page": page_no, "section": heading, "text": text})
        buf = []

    for ln in lines:
        s = ln.strip()
        if HEADING.match(s) and buf:
            flush()
        if HEADING.match(s) and len(s) < 60:
            heading = s
        buf.append(s)
        if len(join(buf)) > MAX_CHARS:
            flush()
    flush()
    return out, heading


def guide3_chunks(tag, title, pages):
    out, heading = [], ""
    cur = None  # 보안 항목 진행 중: {"id", "title", "field", "parts": {field: [(page, line)]}, "page"}

    def close():
        nonlocal cur
        if cur:
            for name, _ in FIELDS:
                rows = cur["parts"].get(name)
                if not rows:
                    continue
                text = join([ln for _, ln in rows])
                text = re.sub(r"^(세부 요구사항|요구사항|시험기준|시험방법 \(예시\)|시험방법|예외사항|적용 예외사항)\s*", "", text)
                out.append({"clause_id": f"{tag}-{cur['id']}-{name}", "source": tag, "source_title": title,
                            "page": rows[0][0], "section": f"{cur['id']} {cur['title']}", "sec_id": cur["id"],
                            "field": name, "text": text})
        cur = None

    for pno, text in enumerate(pages, 1):
        lines = clean_lines(text)
        free = []
        for ln in lines:
            s = ln.strip()
            m = SEC_HEAD.match(s)
            if m:
                close()
                cur = {"id": m.group(1), "title": m.group(2).strip(), "field": None, "parts": {}}
                continue
            if cur is not None and SEC_END.match(s):
                close()
            if cur is None:
                free.append(ln)
                continue
            if s.startswith("구분") and "내용" in s and cur["field"] is None:
                continue
            for name, pat in FIELDS:
                if pat.match(s):
                    cur["field"] = name
                    break
            if cur["field"] is None:
                continue
            # 보안 항목 표가 끝나고 다음 장 제목이 나오면 항목을 닫는다
            if re.match(r"^\d+\.\s+\S", s) and cur["field"] == "예외" and cur["parts"].get("예외"):
                close()
                free.append(ln)
                continue
            cur["parts"].setdefault(cur["field"], []).append((pno, s))
        if free:
            chunks, heading = paragraph_chunks(tag, title, pno, free, heading)
            out += chunks
    close()
    return out


def build():
    chunks = []
    for tag, fname, title in SOURCES:
        pages = pages_of(GUIDES / fname)
        if tag == "첨부3":
            chunks += guide3_chunks(tag, title, pages)
        else:
            heading = ""
            for pno, text in enumerate(pages, 1):
                cs, heading = paragraph_chunks(tag, title, pno, clean_lines(text), heading)
                chunks += cs
    seen = {}
    for i, c in enumerate(chunks):
        n = seen.get(c["clause_id"], 0)
        seen[c["clause_id"]] = n + 1
        if n:
            c["clause_id"] = f"{c['clause_id']}-{n + 1}"
        c["chunk_id"] = f"c{i:04d}"
    lines = [json.dumps({k: c[k] for k in ("chunk_id", "clause_id", "source", "source_title", "page", "section",
                                           "sec_id", "field", "text") if k in c}, ensure_ascii=False)
             for c in chunks]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    (HERE / "regs.jsonl").write_bytes(data)
    (HERE / "regs.sha256").write_text(hashlib.sha256(data).hexdigest() + "  regs.jsonl\n", encoding="utf-8")
    with (HERE / "clause_ids.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clause_id", "source", "page", "section", "field", "preview"])
        for c in chunks:
            w.writerow([c["clause_id"], c["source"], c["page"], c["section"], c.get("field", ""), c["text"][:60]])
    return chunks


if __name__ == "__main__":
    cs = build()
    by = {}
    for c in cs:
        by[c["source"]] = by.get(c["source"], 0) + 1
    print(len(cs), by, sum(1 for c in cs if c.get("sec_id")), "security clauses")
