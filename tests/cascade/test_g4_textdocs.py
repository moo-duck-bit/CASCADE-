"""G4(CASCADE 쪽): 범용 텍스트 문서(md·rst) 읽기·쓰기, 요청 파싱, generic 실행. 도메인 팩 없이 형식 팩(generic_text)만."""

from pathlib import Path

from document_ai.cascade import config as cfg
from document_ai.cascade import pack as pack_mod
from document_ai.cascade.apply import apply_edits
from document_ai.cascade.cells import parse_case, present_docs
from document_ai.cascade.editor import make_edit
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.request import parse_request
from document_ai.cascade.run import run_case

MD = """# Install

Use version 2.31.0 of the library.

## Config

- timeout: 30
- pin: 2.31.0

```
pip install lib==2.31.0
```
"""
RST = """Usage
=====

Pin lib to 2.31.0 in requirements.

Other note about 2.31.0 here.
"""
REQ = "docs/install.md 'Install'의 값을 2.31.0에서 2.32.0(으)로 변경한다."


def case(tmp_path):
    inp = tmp_path / "c" / "input"
    (inp / "docs").mkdir(parents=True)
    (inp / "docs" / "install.md").write_text(MD, encoding="utf-8")
    (inp / "docs" / "usage.rst").write_text(RST, encoding="utf-8")
    (inp / "request.txt").write_text(REQ, encoding="utf-8")
    (tmp_path / "c" / "gold").mkdir()
    (tmp_path / "c" / "gold" / "gold.json").write_text("{}", encoding="utf-8")
    return inp


def test_text_cells_with_lines(tmp_path):
    inp = case(tmp_path)
    pk = pack_mod.load("generic_text")
    assert present_docs(inp, pk) == ["docs/install.md", "docs/usage.rst"]
    cells = parse_case(inp, pack=pk)
    by = {c.site: c for c in cells}
    assert by["[docs/install.md | Install | para#1]"].lines == (3, 3)
    assert by["[docs/install.md | Config | list#2]"].text == "- pin: 2.31.0"
    assert by["[docs/install.md | Config | code#1]"].lines == (11, 11)
    assert by["[docs/usage.rst | Usage | para#2]"].lines == (6, 6)


def test_text_request_parse():
    ch = parse_request(REQ, pack_mod.load("generic_text"))
    assert (ch.kind, ch.origin_doc, ch.origin_req, ch.old, ch.new) == ("T1", "docs/install.md", "Install", "2.31.0",
                                                                       "2.32.0")


def test_text_apply_replaces_unit_lines(tmp_path):
    inp = case(tmp_path)
    pk = pack_mod.load("generic_text")
    edits = [make_edit("[docs/install.md | Config | code#1]", "pip install lib==2.32.0"),
             make_edit("[docs/usage.rst | Usage | para#1]", "Pin lib to 2.32.0 in requirements.")]
    ar = apply_edits(inp, tmp_path / "out", edits, pack=pk)
    assert ar.failed == []
    assert "lib==2.32.0" in (tmp_path / "out" / "docs" / "install.md").read_text()
    assert (tmp_path / "out" / "docs" / "usage.rst").read_text().splitlines()[3] == "Pin lib to 2.32.0 in requirements."


def test_generic_run_on_text_docs(tmp_path):
    inp = case(tmp_path)

    def fn(m, n):
        if n == "locator":
            lines = [l for l in m[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": "Other note" not in l, "reason": "t"} for i, l in enumerate(lines)]}
        tl = m[1]["content"].split("## 고칠 칸")[1].split("##")[0]
        out = []
        for l in tl.splitlines():
            if l.startswith("["):
                site, text = l.split("] ", 1)
                out.append({"site": site + "]", "new_text": text.replace("2.31.0", "2.32.0")})
        return {"edits": out}
    conf = cfg.from_dict({"domain_pack": "generic_text", "graph_mode": "generic", "retrieval": False,
                          "edit_mode": "free_json", "verifier_checks": {"V6b": False}})
    pred, log = run_case(inp, conf, llm=MockLLM(fn), work_dir=tmp_path / "w", embed_fn=lambda ts: [[1.0] * 4 for _ in ts])
    assert pred["error"] is None and log["violations"] == []
    got = {(e["file"], tuple(e["lines"])) for e in pred["edits"]}
    assert ("docs/install.md", (11, 11)) in got and ("docs/usage.rst", (4, 4)) in got
    assert ("docs/usage.rst", (6, 6)) not in got
    assert not any("gold" in f for f in log["files_read"])
