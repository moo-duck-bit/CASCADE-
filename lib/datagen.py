"""v0.2·v0.3 데이터 생성 스크립트가 함께 쓰는 상수, 입출력, 본문 검사."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

# 템플릿(첨부3 기반) 사이버보안 항목. RA-05는 연결 대상 밖이라 항상 N/A로 둔다.
SEC_ITEMS = {
    "IA-01": "사용자 식별 및 인증", "IA-02": "계정 관리", "IA-03": "식별정보 관리", "IA-04": "인증정보 관리",
    "IA-05": "비밀번호 강도 설정", "IA-06": "인증정보에 대한 피드백", "IA-07": "연속적인 로그인 시도 실패 시 로그인 제한",
    "IA-08": "시스템 사용 알림 메시지", "UC-01": "권한 부여", "UC-02": "모바일 코드 사용 통제", "UC-03": "세션 잠금",
    "UC-04": "감사기록 생성", "UC-05": "감사 처리 실패 대응", "UC-06": "타임스탬프", "UC-07": "부인 방지",
    "SI-01": "통신에 대한 무결성 보장", "SI-02": "악성코드로부터 보호", "SI-03": "보안 기능 검증",
    "SI-04": "소프트웨어 및 정보에 대한 무결성 점검", "SI-05": "입력값 검증", "SI-06": "오류 시 사전 결정된 상태로 출력",
    "SI-07": "오류 처리", "SI-08": "업데이트", "SI-09": "업데이트에 대한 진본성 및 무결성 검증", "SI-10": "물리적 변조 방지",
    "SI-11": "부트 프로세스 무결성 검증", "DC-01": "정보에 대한 기밀성 보장", "DC-02": "보건의료정보 비식별화",
    "DC-03": "안전한 암호화 사용", "TRE-01": "감사로그에 대한 비인가된 접근 제한", "RA-01": "서비스 거부(DoS) 방지",
    "RA-02": "의료기기 백업", "RA-03": "의료기기 복구 및 재구성", "RA-04": "네트워크 및 보안 구성 설정",
    "RA-05": "불필요한 기능 비활성화",
}
LINK_SECS = [s for s in SEC_ITEMS if s != "RA-05"]

# 사실 표와 생성 본문에 나오면 안 되는 실제 제품·서비스명. 표준·알고리즘 이름(AES, TLS, SHA)은 허용.
REAL_NAMES = [
    "솜리스트", "Somryst", "Sleepio", "슬리피오", "솜즈", "Somzz", "웰트", "WELT", "마인드리움", "Mindrium",
    "Pear", "Big Health", "에임메드", "카카오", "Kakao", "네이버", "Naver", "구글", "Google", "애플", "Apple",
    "삼성", "Samsung", "Firebase", "AWS", "Amazon", "Azure", "GCP", "Cloud Run", "iCloud", "Fitbit", "핏빗",
]

TOKEN_RE = re.compile(r"\{\{\s*(F_[A-Z0-9_]+)\s*\}\}")
ANY_BRACE_RE = re.compile(r"\{\{|\}\}")
NUMBER_RE = re.compile(r"\d+(\.\d+)?")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def real_name_hits(text):
    # '애플리케이션'의 '애플'은 제품명이 아니다.
    t = text.lower().replace("애플리케이션", "")
    return [n for n in REAL_NAMES if n.lower() in t]


def digits_of(text):
    return "".join(re.findall(r"\d+", text))


def unit_of(fact):
    """대표 표기에서 값을 뺀 나머지('15분' → '분'). 숫자 값이 아니면 빈 문자열."""
    v0 = fact["variants"][0]
    if fact["value"].isdigit() and fact["value"] in v0:
        return v0.replace(fact["value"], "", 1).strip()
    return ""


def with_unit(fact, value):
    """숫자만 있는 값에 대표 표기의 단위를 붙인다('30', 대표 표기 '15분' → '30분')."""
    base = fact["variants"][0]
    if NUMBER_RE.fullmatch(value) and fact["value"] in base:
        return base.replace(fact["value"], value, 1)
    return value


def base_applicability(rules, cyber):
    """보안 항목별 (적용 여부, 근거 규칙 id). 규칙 조건이 참이면 then.applicability, 거짓이면 반대."""
    out = {s: ("적용", None) for s in SEC_ITEMS}
    out["RA-05"] = ("N/A", None)
    for r in rules:
        cond = cyber[r["if"]["attr"]] == r["if"]["equals"]
        app = r["then"]["applicability"]
        status = app if cond else ("적용" if app == "N/A" else "N/A")
        for s in r["then"]["secs"]:
            out[s] = (status, r["id"])
    return out


def token_lines(fids, facts):
    if not fids:
        return "- 이 항목에는 넣을 토큰이 없습니다. 중괄호({ })와 숫자를 전혀 쓰지 마세요."
    return "\n".join(
        f"- {{{{{fid}}}}}: {facts[fid]['name']} ({facts[fid]['type']}, "
        f"토큰 자체가 '{facts[fid]['variants'][0]}'처럼 단위까지 포함)"
        for fid in fids
    )


UNIT_AFTER_RE = re.compile(r"\s(회|분|자|일|시간|개월|개|초|번|주)(?=[\s,.)을를이가의에]|$)")
UNIT_JOSA_AFTER_RE = re.compile(r"\s(회|분|자|일|시간|개월|개|초|번|주)(로|으로|와|과|도|만|씩)")
SPACED_JOSA_RE = re.compile(r"\s(을|를|이|가|은|는|으로|로)(?=\s)")
ID_RE = re.compile(r"Req\.\s*\d+|[A-Z]{2,3}-\d{2}")


def make_validator(required, allowed, facts):
    """생성 본문 검사: 필수 토큰이 다 있고, 허용 밖 토큰이 없고, 토큰 밖에 숫자·사실 표기·실제 이름·중괄호가 없어야 한다."""
    surfaces = sorted({s for f in facts.values() for s in [f["value"], *f["variants"]]}, key=len, reverse=True)

    def validate(value):
        texts = []
        for v in value.values():
            texts += v if isinstance(v, list) else [v]
        joined = "\n".join(texts)
        found = set(TOKEN_RE.findall(joined))
        errs = [f"필수 토큰 {{{{{t}}}}} 이 없음" for t in required if t not in found]
        errs += [f"허용되지 않은 토큰 {{{{{t}}}}}" for t in found - set(allowed)]
        for m in TOKEN_RE.finditer(joined):
            token = m.group(0)
            after = joined[m.end():m.end() + 6]
            if re.match(r"[회분자일시초번개주]", after) or UNIT_AFTER_RE.match(after) or UNIT_JOSA_AFTER_RE.match(after):
                errs.append(f"토큰 {token} 뒤에 단위를 붙임: '{token}{after}'")
            elif SPACED_JOSA_RE.match(after):
                errs.append(f"토큰 {token} 뒤 조사를 띄어 씀: '{token}{after}' (조사는 토큰에 붙여 쓴다)")
        bare = TOKEN_RE.sub("", joined)
        if "{" in bare or "}" in bare:
            errs.append("토큰 형식이 아닌 중괄호가 남음")
        bare_ids = ID_RE.sub("", bare)
        if re.search(r"\d", bare_ids):
            numbers = ", ".join(sorted(set(re.findall(r"\S*\d\S*", bare_ids))))
            errs.append("토큰 밖에 숫자가 있음: " + numbers[:120])
        for s in surfaces:
            if len(s) >= 2 and not re.search(r"\d", s) and s in bare:
                errs.append(f"사실 표기 {s!r} 를 토큰 없이 직접 씀")
        errs += [f"실제 이름 {n!r}" for n in real_name_hits(joined)]
        if any(not t.strip() for t in texts):
            errs.append("빈 문장")
        return errs

    return validate


def run_cached(jobs, cache_dir):
    """jobs {(종류, id): (함수, 인자)}를 8개씩 병렬로 돌린다. 결과는 cache_dir에 남기고 다시 실행하면 재사용한다."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    def cached(key, fn, args):
        path = cache_dir / (f"{key[0]}_{key[1]}".replace(" ", "").replace(".", "") + ".json")
        if path.exists():
            return read_json(path)
        value = fn(*args)
        write_json(path, value)
        return value

    results, failed = {}, []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {key: pool.submit(cached, key, fn, args) for key, (fn, args) in jobs.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as err:  # 실패 항목은 모아서 한꺼번에 보고한다
                failed.append((key, str(err)[:200]))
    if failed:
        for key, err in failed:
            print("FAILED", key, err)
        raise SystemExit(f"{len(failed)} item(s) failed after retries")
    return results
