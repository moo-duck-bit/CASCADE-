"""Verifier v0: 공개 입력(site_index.md, request.txt, b2_rules_v1.txt)만으로 예측 수정 목록의 문제 후보를 찾는다.

정답, sites.json, facts.json, links.json은 읽지 않는다. 출력은 [{"type", "site", "detail"}].
사용: python experiments/verifier.py <case_id> <prediction.json>
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scoring"))
from score import SEC_RE, norm_edit, parse_index, read_input, split_site  # 위치 정규화는 채점기와 같은 규칙

RULES_TXT = ROOT / "experiments" / "prompts" / "b2_rules_v1.txt"
T1_RE = re.compile(r"^(Req\. \d+) '(.+?)'의 (.+?)[을를] (.+?)에서 (.+?)(?:\(으\)로|으로|로) 변경한다\.$")
ADD_RE = re.compile(r"요구사항 (Req\. \d+) '(.+?)'을\(를\) 신규 추가한다")
DEL_RE = re.compile(r"^(Req\. \d+) '(.+?)' 요구사항을 삭제한다")
ATTR_RE = re.compile(r"\((\w+)\)을 (true|false)에서 (true|false)로 변경한다")
RULE_RE = re.compile(r"^\s*-\s*(\w+)\(.*?\)[가이]\s*(true|false)이면\s*(.+?)[은는]\s*(적용|N/A),\s*(true|false)이면\s*(적용|N/A)")
LINK_CELLS = ("소프트웨어 기능 요구사항 번호", "해당 요구사항")
RULE_CELLS_XXCS = ("체크리스트 적용 여부", "체크리스트 입증 방법", "적용 여부", "판정")
SINO = "영 일 이 삼 사 오 육 칠 팔 구".split()


def sino_korean(n):
    """1~99 → 한자어 수사(15 → 십오)."""
    if not 0 < n < 100:
        return ""
    t, o = divmod(n, 10)
    return (("" if t == 1 else SINO[t]) + "십" if t else "") + (SINO[o] if o else "")


def req_pattern(rid):
    return re.compile(r"Req\.\s*" + rid.split()[1] + r"(?!\d)")


def linked_secs(index, rid):
    """6절 Req 번호 칸이나 XXCS 해당 요구사항 칸에 rid가 적힌 보안 항목."""
    pat = req_pattern(rid)
    out = set()
    for s, t in index.items():
        _, item, cell = split_site(s)
        if SEC_RE.match(item) and cell in LINK_CELLS and pat.search(t):
            out.add(item)
    return out


def value_patterns(v):
    """'15분' → 15분 / 15 분 / 십오 분 / 십오분. 숫자+단위가 아니면 빈 목록(옛 값 검사는 숫자+단위만)."""
    m = re.fullmatch(r"(\d+)\s*([가-힣]+)", v.strip())
    if not m:
        return []
    num, unit = m.group(1), m.group(2)
    pats = [re.compile(r"(?<![\d.])" + num + r"\s?" + re.escape(unit))]
    sk = sino_korean(int(num))
    if sk:
        pats.append(re.compile(r"(?<![가-힣])" + sk + r"\s?" + re.escape(unit)))
    return pats


def parse_rules(text):
    rules = []
    for ln in text.splitlines():
        m = RULE_RE.match(ln)
        if m:
            secs = [s.strip() for s in re.split(r",\s*", m.group(3))]
            rules.append({"attr": m.group(1), "when": m.group(2) == "true", "then": m.group(4),
                          "otherwise": m.group(6), "secs": secs})
    return rules


def rule_status(rules, attr, value):
    out = {}
    for r in rules:
        if r["attr"] != attr:
            continue
        status = r["then"] if value == r["when"] else r["otherwise"]
        for s in r["secs"]:
            out[s] = status
    return out


def verify(index_md, request, edits):
    index = parse_index(index_md)
    rules = parse_rules(RULES_TXT.read_text(encoding="utf-8"))
    pred = {}
    for e in edits:
        s = norm_edit(e)
        if s:
            pred[s] = str(e.get("new_text") or "")
    after = dict(index)
    after.update({s: t for s, t in pred.items() if s in index})
    findings = []
    docs = {split_site(s)[0] for s in index}

    m_t1, m_del = T1_RE.match(request), DEL_RE.match(request)
    m_add, m_attr = ADD_RE.search(request), ATTR_RE.search(request)
    new_req = m_add.group(1) if m_add else None

    # 1) 없는 위치. 요청이 추가하는 새 Req의 MDSR·MDDR 칸은 예외
    for s in pred:
        if s in index:
            continue
        doc, item, _ = split_site(s)
        if new_req and item == new_req and doc in ("MDSR", "MDDR"):
            continue
        findings.append({"type": "nonexistent", "site": s, "detail": "문서에 없는 위치입니다."})

    origin = m_t1.group(1) if m_t1 else (m_del.group(1) if m_del else None)
    linked = linked_secs(index, origin) if origin else set()

    # 2) 옛 값 잔존: "A에서 B로"의 A(숫자+단위)가 출발 Req 칸이나 연결된 보안 항목 칸에 남음
    if m_t1:
        old, new = m_t1.group(4), m_t1.group(5)
        pats = value_patterns(old)
        for s, t in after.items():
            doc, item, _ = split_site(s)
            if not pats or not (item == origin or (item in linked and doc in ("MDSR", "XXCS"))):
                continue
            if any(p.search(t) for p in pats):
                findings.append({"type": "old_value", "site": s,
                                 "detail": f"출발 Req 또는 연결된 보안 항목 칸에 이전 값 '{old}'이(가) 남아 있습니다"
                                           f"(변경 후 값 '{new}')."})

    # 3) Req 삭제 후 번호가 다른 칸에 남음 / 추가된 Req가 어느 연결 칸에도 없음
    if m_del:
        pat = req_pattern(origin)
        for s, t in after.items():
            if split_site(s)[1] == origin or not pat.search(t):
                continue
            findings.append({"type": "link_missing", "site": s, "detail": f"삭제된 {origin} 번호가 아직 적혀 있습니다."})
    if new_req and ({"MDSR", "XXCS"} & docs):
        pat = req_pattern(new_req)
        if not any(pat.search(t) for s, t in after.items() if split_site(s)[2] in LINK_CELLS):
            findings.append({"type": "link_missing", "site": None,
                             "detail": f"새 {new_req}가 6절 보안 항목 표나 XXCS '해당 요구사항' 어디에도 연결되어 있지 않습니다."})

    # 4) 규칙 결과 누락: 속성 변경으로 적용 여부가 뒤집히는 보안 항목의 6절 Req 번호 칸, XXCS 적용 여부·입증 방법·판정 칸
    flipped = set()
    if m_attr:
        attr, before, now = m_attr.group(1), m_attr.group(2) == "true", m_attr.group(3) == "true"
        st0, st1 = rule_status(rules, attr, before), rule_status(rules, attr, now)
        flipped = {s for s in st1 if st0.get(s) != st1[s]}
        for s in sorted(index):
            doc, item, cell = split_site(s)
            if item not in flipped or s in pred:
                continue
            if (doc == "XXCS" and cell in RULE_CELLS_XXCS) or (doc == "MDSR" and cell == "소프트웨어 기능 요구사항 번호"):
                findings.append({"type": "rule_missing", "site": s,
                                 "detail": f"{attr}가 {str(now).lower()}이면 {item}의 적용 여부가 {st0.get(item)}에서 "
                                           f"{st1[item]}(으)로 바뀌는데 이 칸을 고치지 않았습니다."})

    # 5) 무관 항목 수정: 출발 Req와 연결 없는 보안 항목, 또는 규칙이 뒤집지 않는 보안 항목
    if origin or m_attr:
        allowed = flipped if m_attr else linked
        for s in pred:
            doc, item, _ = split_site(s)
            if doc in ("MDSR", "XXCS") and SEC_RE.match(item) and item not in allowed:
                why = "이번 속성 변경이 적용 여부를 바꾸지 않는 보안 항목" if m_attr else f"{origin}와 추적성 연결이 없는 보안 항목"
                findings.append({"type": "unrelated", "site": s, "detail": f"{why}({item})을 수정했습니다."})
    return findings


def feedback_text(findings):
    lines = ["[검증 결과] 아래 항목은 문제일 수도 있고 아닐 수도 있다. 각 항목을 확인해 실제로 바꿔야 하는 것만 반영한 최종 JSON을 출력하라."]
    if not findings:
        lines.append("- 지적 사항 없음")
    for f in findings:
        lines.append(f"- ({f['type']}) {f['site'] or '-'}: {f['detail']}")
    return "\n".join(lines)


def main():
    cid, pred_path = sys.argv[1], Path(sys.argv[2])
    edits = json.loads(pred_path.read_text(encoding="utf-8"))
    edits = edits.get("edits", edits) if isinstance(edits, dict) else edits
    out = verify(read_input(cid, "site_index.md"), read_input(cid, "request.txt").strip(), edits)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
