"""게이트: 원본 지문 확인, 사본 쓰기, 승인, 칸 단위 롤백, 마지막 상태 수치."""

import hashlib
from pathlib import Path

from .cells import present_docs

ROLLBACK_TYPES = ("V1", "V5")


class GateError(RuntimeError):
    pass


def fingerprint(input_dir, reader=None, pack=None):
    from . import pack as pack_mod
    out = {}
    for name in present_docs(input_dir, pack):
        p = pack_mod.load(pack).doc_path(input_dir, name)
        data = reader(p) if reader else p.read_bytes()
        out[name] = hashlib.sha256(data).hexdigest()
    return out


def rollback(edits, issues, types=ROLLBACK_TYPES):
    """지적 유형이 types인 칸의 편집을 되돌린다(편집 목록에서 뺀다)."""
    bad = {i["site"] for i in issues if i["type"] in types}
    kept = [e for e in edits if e["site"] not in bad]
    return kept, [e["site"] for e in edits if e["site"] in bad]


def approve(edits, auto_approve, ask=None):
    if auto_approve:
        return True
    if ask is None:
        raise GateError("approval required (auto_approve=False and no approver)")
    return bool(ask(edits))


def final_metrics(issues):
    by = {}
    for i in issues:
        by[i["type"]] = by.get(i["type"], 0) + 1
    return {"final_issues": by, "structural_deficit": by.get("V3", 0), "consistency_regression": by.get("V6", 0)}
