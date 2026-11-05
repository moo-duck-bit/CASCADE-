"""렌더된 문서 검사: 남은 {{ }} 0건, 직접 기재 값 0건, sites_hint 대비 배치 일치율 90% 이상, python-docx로 다시 열림."""

import json
import re
import zipfile

from docx import Document

from datagen import ANY_BRACE_RE, TOKEN_RE, read_json, write_json
from sites import DOC_NAMES

ID_RE = re.compile(r"Req\.\s*\d+|[A-Z]{2,3}-\d{2}")


def leftover_braces(docs_dir):
    """문서 XML 전체(런이 쪼개진 경우 포함)와 site_index에 남은 {{ }} 개수."""
    out = {}
    for name in DOC_NAMES:
        xml = zipfile.ZipFile(docs_dir / f"{name}.docx").read("word/document.xml").decode("utf-8")
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
        out[name] = len(ANY_BRACE_RE.findall(text))
    out["site_index"] = len(ANY_BRACE_RE.findall((docs_dir / "site_index.md").read_text(encoding="utf-8")))
    return out


def direct_values(content, facts):
    """생성 본문에서 토큰을 지운 뒤 사실 value/variants 또는 숫자가 남은 문장."""
    surfaces = sorted({s for f in facts.values() for s in [f["value"], *f["variants"]]}, key=len, reverse=True)
    texts = []
    for rid, b in content["mdsr"].items():
        texts += [(f"MDSR {rid}", t) for t in [b["설명"], b["목적"], *b["기준"]]]
    texts += [("MDSR 3.4", t) for t in content["mdsr34"]]
    for rid, bullets in content["mddr"].items():
        texts += [(f"MDDR {rid}", t) for t in bullets]
    for sec, b in content["xxcs"].items():
        texts += [(f"XXCS {sec}", t) for t in [*b["시험방법"], *b["확인결과"], b["비고"]]]
    out = []
    for where, t in texts:
        bare = TOKEN_RE.sub("", t)
        hits = [s for s in surfaces if s in bare]
        if re.search(r"\d", ID_RE.sub("", bare)):
            hits.append("<digit>")
        if hits:
            out.append({"where": where, "hits": hits, "text": t[:80]})
    return out


def hint_match(facts, substitutions, applicability):
    """(사실, 문서|항목) 힌트가 실제 치환 위치로 실현된 비율. 기본 상태 N/A인 XXCS 항목은 뺀다."""
    placed = {(r["fact"], r["site"].split(" | ")[0].strip("[") + "|" + r["item"]) for r in substitutions}
    na = {s for s, a in applicability.items() if a["status"] == "N/A"}
    hints, hit, miss, skipped = 0, 0, [], []
    for fid, f in facts.items():
        for h in f["sites_hint"]:
            doc, item = h.split("|", 1)
            if h == "MDSR|3.4":
                item = "3.4 일반적 제한"
            if doc == "XXCS" and item in na:
                skipped.append(f"{fid}@{h}")
                continue
            hints += 1
            if (fid, f"{doc}|{item}") in placed:
                hit += 1
            else:
                miss.append(f"{fid}@{h}")
    return {"hints": hints, "matched": hit, "rate": round(hit / max(1, hints), 4), "missed": miss,
            "skipped_na_sections": skipped}


def reopen(docs_dir):
    out = {}
    for name in DOC_NAMES:
        try:
            d = Document(str(docs_dir / f"{name}.docx"))
            out[name] = {"ok": True, "paragraphs": len(d.paragraphs), "tables": len(d.tables)}
        except Exception as err:  # 열리지 않는 문서도 결과에 남긴다
            out[name] = {"ok": False, "error": str(err)[:200]}
    return out


def bad_criteria(sites, reqs):
    """MDSR 기준 칸이 정확히 2문단(기준#p1, 기준#p2)이 아닌 Req."""
    crit = {}
    for s in sites:
        if s["doc"] == "MDSR" and s["cell"].startswith("기준"):
            crit.setdefault(s["item"], []).append(s["cell"])
    return {r: crit.get(r, []) for r in reqs if sorted(crit.get(r, [])) != ["기준#p1", "기준#p2"]}


def main(product_dir, docs_dir, out_path, two_criteria_reqs=None):
    """two_criteria_reqs를 주면 그 Req들의 MDSR 기준 칸이 2문단인지도 본다(v0.3)."""
    facts = read_json(product_dir / "facts.json")["facts"]
    content = read_json(product_dir / "content.json")
    sj = read_json(docs_dir / "sites.json")
    res = {
        "leftover_braces": leftover_braces(docs_dir),
        "direct_values": direct_values(content, facts),
        "hint_match": hint_match(facts, sj["substitutions"], content["applicability"]),
        "reopen": reopen(docs_dir),
    }
    gate = {}
    if two_criteria_reqs is not None:
        bad = bad_criteria(sj["sites"], two_criteria_reqs)
        res["criteria_two_paras"] = {"reqs": len(two_criteria_reqs), "bad": bad}
        gate["criteria_two_paras"] = not bad
    gate.update({
        "no_leftover_braces": all(v == 0 for v in res["leftover_braces"].values()),
        "no_direct_values": not res["direct_values"],
        "hint_match_ge_90": res["hint_match"]["rate"] >= 0.90,
        "reopen_ok": all(v["ok"] for v in res["reopen"].values()),
    })
    res["gate"] = gate
    res["passed"] = all(gate.values())
    write_json(out_path, res)
    print(json.dumps({k: res[k] for k in ("leftover_braces", "gate", "passed")}, ensure_ascii=False, indent=1))
    hm = res["hint_match"]
    print("hint match:", hm["matched"], "/", hm["hints"], "missed:", hm["missed"][:10])
    print("direct values:", len(res["direct_values"]), res["direct_values"][:3])
    return 0 if res["passed"] else 1
