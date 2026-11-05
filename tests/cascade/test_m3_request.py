import pytest

from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.request import RequestError, current_attrs, parse_request

from .fixture_docs import build_case


def test_t1_value_change():
    r = parse_request("Req. 2 '계정 잠금'의 잠금 해제 안내 시각을 15분에서 10분으로 변경한다.")
    assert (r.kind, r.origin_req, r.title, r.target, r.old, r.new) == \
        ("T1", "Req. 2", "계정 잠금", "잠금 해제 안내 시각", "15분", "10분")


def test_t1_text_value_with_eu_ro():
    r = parse_request("Req. 102 '전송 암호화'의 전송 구간 암호화 방식을 TLS 1.2 이상에서 TLS 1.3(으)로 변경한다.")
    assert (r.old, r.new, r.target) == ("TLS 1.2 이상", "TLS 1.3", "전송 구간 암호화 방식")
    r = parse_request("Req. 1 '사용자 로그인'의 비밀번호 조합 규칙을 대소문자, 숫자, 특수문자 포함에서 "
                      "대소문자, 숫자, 특수문자 포함 및 연속 문자 금지로 변경한다.")
    assert r.old == "대소문자, 숫자, 특수문자 포함" and r.new.endswith("연속 문자 금지")


def test_t2_attr_change():
    r = parse_request("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다. "
                      "첨부3 적용 예외에 따라 문서를 갱신한다.")
    assert (r.kind, r.attr, r.attr_ko, r.old, r.new) == ("T2", "stores_pii", "개인식별정보 저장", False, True)
    r = parse_request("제품 사이버보안 속성 '독립형 소프트웨어 의료기기(SaMD)'(samd)을 true에서 false로 변경한다.")
    assert (r.attr, r.attr_ko, r.old, r.new) == ("samd", "독립형 소프트웨어 의료기기(SaMD)", True, False)


def test_t3_add_and_delete():
    r = parse_request("서버 요구사항 Req. 107 '감사로그 외부 반출 차단'을(를) 신규 추가한다. "
                      "내용: 감사로그의 외부 반출을 권한 없는 사용자로부터 차단하여야 한다.")
    assert (r.kind, r.new_req, r.component, r.title) == ("T3_add", "Req. 107", "서버", "감사로그 외부 반출 차단")
    assert r.content.startswith("감사로그의 외부 반출")
    r = parse_request("Req. 104 '감사로그 보관' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다.")
    assert (r.kind, r.req, r.title) == ("T3_delete", "Req. 104", "감사로그 보관")


@pytest.mark.parametrize("bad", ["", "계정 잠금 시간을 바꿔 주세요.", "Req. 2의 값을 변경한다.",
                                 "제품 사이버보안 속성 'x'(x)을 yes에서 no로 변경한다."])
def test_bad_request_clear_error(bad):
    with pytest.raises(RequestError) as e:
        parse_request(bad)
    assert "구조화 요청" in str(e.value)


def test_current_attrs_from_xxcs_and_request(tmp_path):
    g = CellGraph(parse_case(build_case(tmp_path / "in")))
    r = parse_request("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다.")
    attrs, notes = current_attrs(g, r)
    assert attrs["stores_pii"] is False and attrs["password_auth"] is True
    assert notes == []
    r2 = parse_request("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 true에서 false로 변경한다.")
    attrs2, notes2 = current_attrs(g, r2)
    assert attrs2["stores_pii"] is True and notes2  # 문서와 요청이 엇갈리면 요청을 따르고 기록한다
