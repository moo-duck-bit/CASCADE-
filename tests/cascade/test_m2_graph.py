import pytest

from document_ai.cascade import rules, values
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.guard import ReadGuard

from .fixture_docs import build_case


# 값 정규화

@pytest.mark.parametrize("a,b", [
    ("15분", "15 분"), ("15분", "십오 분"), ("15분", "15 min"), ("5회", "5번"), ("5회", "다섯 번"),
    ("8자", "8글자"), ("24시간", "스물네 시간"), ("20분", "이십 분"), ("10분", "십 분"), ("80%", "80 퍼센트"),
    ("22:00", "오후 10시"), ("18:00", "18시"), ("3가지", "세 가지"),
])
def test_same_value(a, b):
    assert values.same_value(a, b), (a, b)


@pytest.mark.parametrize("a,b", [("15분", "15회"), ("15분", "16분"), ("8자", "8분"), ("22:00", "10시간")])
def test_different_value(a, b):
    assert not values.same_value(a, b)


def test_conversion_marked_not_equal():
    assert not values.same_value("6개월", "180일")
    assert values.converted_equal("6개월", "180일")
    assert values.converted_equal("1년", "365일") and values.converted_equal("1시간", "60분")
    assert not values.converted_equal("6개월", "181일")
    assert values.converted_equal("24시간", "매일") and values.converted_equal("1일", "24 시간")
    assert [m.key for m in values.mentions("데이터를 매일마다 백업")] == ["1|day"]
    assert values.converted_equal("24시간", "하루") and values.same_value("7일", "일주일") is False
    assert values.converted_equal("7일", "일주일")


def test_mentions_spans_and_clock_masks_duration():
    ms = values.mentions("오후 10시 30분에 알림, 반복 간격 15 분, 최대 3번 회")
    keys = [m.key for m in ms]
    assert "22:30|clock" in keys and "15|min" in keys and "3|count" in keys
    assert "30|min" not in keys  # 시각 안의 분은 길이로 세지 않는다
    m = next(m for m in ms if m.key == "15|min")
    assert "오후 10시 30분에 알림, 반복 간격 15 분, 최대 3번 회"[m.start:m.end] == "15 분"


def test_no_false_numeral_words():
    assert values.mentions("이 기능은 일정을 관리한다") == []
    assert [m.key for m in values.mentions("AES-256 방식")] == []


def test_loose_text_match():
    assert values.loose_contains("모든 전송 데이터는 TLS1.2 이상 방식으로", "TLS 1.2 이상")
    assert values.loose_contains("AES 256 방식으로 암호화", "AES-256")
    assert values.loose_contains("대소문자+숫자+특수문자에 따라", "대소문자, 숫자, 특수문자 포함")
    assert not values.loose_contains("TLS1.3 방식", "TLS 1.2 이상")
    assert values.loose_contains("TLS v1.2 이상 암호화 방식", "TLS 1.2 이상")
    assert values.loose_contains("TLS 버전 1.2 이상", "TLS 1.2 이상")


# 규칙 표

def test_rules_table_loads_and_recomputes():
    table = rules.load_table()
    # 규칙 표 v2(첨부3 적용 예외사항 원문 기준)
    assert {r["attr"] for r in table} >= {"password_auth", "stores_pii", "authentication", "remote_access", "samd"}
    base = {"password_auth": True, "stores_pii": False, "authentication": True, "remote_access": True, "samd": True}
    app = rules.applicability(base, table)
    assert app["DC-02"] == "N/A" and app["IA-05"] == "적용" and app["SI-10"] == "N/A"
    flips = rules.flips("stores_pii", False, True, base, table)
    assert flips == [{"sec": "DC-02", "before": "N/A", "after": "적용", "rule": "R09"}]
    flips = rules.flips("password_auth", True, False, base, table)
    assert {f["sec"] for f in flips} == {"IA-05"} and all(f["after"] == "N/A" for f in flips)


def test_rules_infer_attrs_from_xxcs_status():
    table = rules.load_table()
    status = {"IA-05": "적용", "IA-06": "적용", "DC-02": "N/A", "UC-03": "적용", "SI-10": "N/A", "SI-11": "N/A"}
    assert rules.infer_attrs(status, table) == {"password_auth": True, "authentication": True, "stores_pii": False,
                                                "remote_access": True, "samd": True}


# 그래프

@pytest.fixture()
def case(tmp_path):
    return build_case(tmp_path / "input")


def test_graph_without_links_json_under_guard(case, tmp_path):
    g_read = ReadGuard([case])
    with g_read.active():
        cells = parse_case(case, reader=g_read.read_bytes)
        g = CellGraph(cells)
    assert not g_read.violations()
    from pathlib import Path
    names = {Path(p).name for p in g_read.files_read()}
    assert names <= {"MDSR.docx", "MDDR.docx", "XXCS.docx", "rules_table.yaml"} and "links.json" not in names
    assert g.n_edges("contains") > 0


def test_graph_contains_and_same_id(case):
    g = CellGraph(parse_case(case))
    sites = [c.site for c in g.item_cells("MDSR", "Req. 2")]
    assert "[MDSR | Req. 2 | 설명]" in sites
    assert ("MDDR", "Req. 2") in g.same_id("MDSR", "Req. 2")
    assert ("XXCS", "IA-07") in g.same_id("MDSR", "IA-07")


def test_graph_trace_from_both_tables(case):
    g = CellGraph(parse_case(case))
    assert g.secs_of_req("Req. 1") == {"IA-05", "IA-07", "SI-01"}
    assert g.secs_of_req("Req. 2") == {"IA-07"}
    assert g.reqs_of_sec("SI-01") == {"Req. 1", "Req. 100"}
    assert g.reqs_of_sec("DC-02") == set()
    srcs = {c.site for c in g.trace_cells("IA-07")}
    assert srcs == {"[MDSR | IA-07 | 소프트웨어 기능 요구사항 번호]", "[XXCS | IA-07 | 해당 요구사항]"}


def test_graph_value_edges(case):
    g = CellGraph(parse_case(case))
    hits = {c.site for c in g.value_cells("15분")}
    assert "[MDSR | 3.4 일반적 제한 | 본문#p1]" in hits  # 십오 분
    assert "[MDDR | Req. 2 | 설계#p1]" in hits
    assert "[XXCS | IA-07 | 시험방법#p2]" in hits
    assert "[MDSR | Req. 1 | 설명]" not in hits
    lit = {c.site for c in g.value_cells("15분", normalized=False)}
    assert "[MDSR | 3.4 일반적 제한 | 본문#p1]" not in lit and "[MDSR | Req. 2 | 설명]" in lit
    conv = {c.site for c in g.value_cells("60분", converted=True)}
    assert "[MDSR | Req. 100 | 기준#p2]" in conv  # 1시간


def test_graph_rule_edges_and_status(case):
    g = CellGraph(parse_case(case))
    assert g.rule_secs("stores_pii") == ["DC-02"]
    assert g.status("DC-02") == "N/A" and g.status("IA-05") == "적용"
    assert g.n_edges("rule") >= 4
