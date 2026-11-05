"""v0.3 제품의 사실 표·요구사항·연결표를 만든다. 규칙은 profile.json에 고정돼 있다.

단계: seed(v0.2 사실, 결정적) → new_facts → placement → reqs → links(보안 항목별). 섹션마다 check_facts 검사를 돌리고,
실패하면 오류를 붙여 최대 3회 다시 만든다. 채택된 출력은 v03/runs/<P>/facts_rounds/*_accepted.json에 남기고 다시 실행하면 재사용한다.
출력: v03/products/<P>/product/facts.json(facts, reqs, cyber), links.json(links, rules, overlink_note)
"""

import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import check_facts
from common import ALL_REQS, CYBER_BASE, PROD, PRODUCT, PROFILE, RULES, RUNS, SEED
from datagen import LINK_SECS, SEC_ITEMS, base_applicability, read_json, unit_of, write_json
from llm import chat_json, check_schema, server_schema

TEMP = 0.2
MAX_FIX = 3
ROUNDS = RUNS / "facts_rounds"
BRIEF = PROFILE["brief"]
SYSTEM = (
    "당신은 의료기기 소프트웨어 인허가 문서를 준비하는 기술 문서 작성자입니다. "
    f"가상의 제품 '{PROFILE['name']}'에 대한 설정만 만듭니다. "
    "실제 회사명·제품명·서비스명(클라우드, 앱스토어 브랜드 포함)은 절대 쓰지 않습니다. "
    "출력은 지정된 JSON 스키마 하나만 냅니다."
)
FIX_REQUEST = ("검사에서 아래 문제가 나왔습니다. 문제가 된 부분만 고치고 나머지는 그대로 둔 채, "
               "같은 스키마로 전체를 다시 출력하세요.\n- ")
APPLICABLE = check_facts.APPLICABLE
SEC_LIST = "\n".join(f"- {s}: {SEC_ITEMS[s]}" for s in APPLICABLE)

# v0.2 사실의 보안 여부와 강해지는 방향(spec.md 7절). 변경 후보는 v0.2에서 이미 약화 방향을 뺐다.
SEED_SECURITY = {
    "F_LOGIN_FAILURE_LIMIT": "lower", "F_ACCOUNT_LOCK_DURATION": "higher", "F_SESSION_TIMEOUT": "lower",
    "F_PASSWORD_MIN_LENGTH": "higher", "F_PASSWORD_COMPLEXITY": "text", "F_TRANSPORT_ENCRYPTION": "text",
    "F_STORAGE_ENCRYPTION": "text", "F_AUDIT_LOG_RETENTION": "higher", "F_BACKUP_INTERVAL": "lower",
    "F_UNLOCK_NOTIFICATION_TIME": "higher",
}
FACT_KEYS = ("name", "value", "variants", "type", "change_candidates", "same_value_group", "depends_on")


def seed_facts():
    src = read_json((PROD / PROFILE["seed_facts_from"]).resolve())["facts"]
    out = {}
    for fid, f in src.items():
        g = {k: f[k] for k in FACT_KEYS}
        g["security"] = fid in SEED_SECURITY
        g["stronger_when"] = SEED_SECURITY.get(fid, "none")
        if fid == "F_UNLOCK_NOTIFICATION_TIME":  # 잠금 시간에서 파생, 줄이는 후보(10)는 약화라 뺀다
            g["change_candidates"] = ["20", "30"]
        out[fid] = g
    return out


def ask(task, version, prompt, schema, prev, errs, attempt, max_tokens):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    if prev is not None:
        messages += [{"role": "assistant", "content": prev},
                     {"role": "user", "content": FIX_REQUEST + "\n- ".join(errs[:40])}]
    return chat_json(messages, server_schema(schema), prompt_version=version, task=task, temperature=TEMP,
                     seed=SEED + 100 * attempt, max_tokens=max_tokens,
                     meta={"fix_round": attempt, "product": PROFILE["id"]})


def generate_section(name, version, prompt, schema, convert, check, max_tokens):
    accepted = ROUNDS / f"{name}_accepted.json"
    if accepted.exists():
        raw = read_json(accepted)
        value = convert(json.loads(json.dumps(raw)))
        if not check_schema(raw, schema) and not check(value):
            print(f"[{name}] reuse accepted output", flush=True)
            return value
    prev, errs = None, []
    for attempt in range(MAX_FIX + 1):
        raw = ask(f"v03/{PROFILE['id']}/{name}", version, prompt, schema, prev, errs, attempt, max_tokens)
        write_json(ROUNDS / f"{name}_r{attempt}.json", raw)
        errs = [explain(e, raw) for e in check_schema(raw, schema)]
        value = None
        if not errs:
            try:
                value = convert(json.loads(json.dumps(raw)))
                errs = check(value)
            except (KeyError, ValueError) as err:
                errs = [f"변환 실패: {err}"]
        print(f"[{name}] round {attempt}: {len(errs)} error(s)", flush=True)
        for e in errs[:30]:
            print("    -", e, flush=True)
        if not errs:
            write_json(accepted, raw)
            return value
        prev = json.dumps(raw, ensure_ascii=False)
    raise SystemExit(f"{name}: still failing after {MAX_FIX} fix rounds")


def explain(err, raw):
    """스키마 개수 오류를 고칠 수 있는 문장으로 바꾼다."""
    m = re.match(r"\$\.reqs\[(\d+)\]\.uses: more than 3 items", err)
    if m:
        r = raw["reqs"][int(m.group(1))]
        return (f"{r['id']}: uses가 {len(r['uses'])}개입니다. "
                "3개 이하로 줄이고, 뺀 사실은 uses가 3개 미만인 다른 Req로 옮기세요.")
    return err


def new_fact_schema():
    return {"type": "object", "required": ["facts"], "additionalProperties": False, "properties": {"facts": {
        "type": "array", "minItems": 12, "maxItems": 18, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "name", "value", "variants", "type", "change_candidates", "same_value_group",
                         "depends_on", "security", "stronger_when"],
            "properties": {
                "id": {"type": "string", "pattern": "^F_[A-Z0-9_]+$"},
                "name": {"type": "string", "minLength": 2},
                "value": {"type": "string", "minLength": 1},
                "variants": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string"}},
                "type": {"type": "string", "enum": ["count", "duration", "length", "period", "time", "algorithm",
                                                     "policy", "size", "ratio", "text"]},
                "change_candidates": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "string"}},
                "same_value_group": {"type": "string"},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "security": {"type": "boolean"},
                "stronger_when": {"type": "string", "enum": list(check_facts.STRONGER)},
            }}}}}


def fact_line(fid, f):
    return (f"- {fid}: {f['name']} = {f['value']} (표기 {', '.join(f['variants'])}; "
            f"같은 값 묶음 {f['same_value_group'] or '-'}; 보안 {'예' if f['security'] else '아니오'})")


def new_facts_prompt(seed):
    groups = sorted({f["same_value_group"] for f in seed.values() if f["same_value_group"]})
    return f"""{BRIEF}

이 제품의 문서 세트(MDSR 요구사항명세서, MDDR 설계명세서, XXCS 소프트웨어 보안검증보고서)에는 이미 아래 사실 {len(seed)}개가 있습니다.
{chr(10).join(fact_line(k, v) for k, v in seed.items())}

여기에 새 사실 12~18개를 더 만드세요(기존 사실은 출력하지 않습니다).

조건:
1. 새 기능(이완 훈련, 수면 효율 리포트, 의료진 메시지, 처방 변경 이력, 데이터 보존·파기, 의료진 계정·권한 관리 등)과 기존 기능의 값을 채웁니다.
2. 보안 사실(security=true)을 4개 이상 새로 넣습니다. 예: 의료진 대시보드 세션 종료 시간, 비활성 계정 자동 잠금 기간, 개인정보 파기 기한, 비밀번호 변경 주기, 인증 토큰 유효 시간, 접속 기록 조회 보관 기간.
   보안 사실은 stronger_when에 값이 커질 때 강해지면 "higher", 작아질 때 강해지면 "lower", 숫자가 아니면 "text"를 씁니다.
   보안 사실의 change_candidates는 보안을 강하게 하는 방향의 값만 씁니다(약하게 하는 값 금지). 보안이 아닌 사실은 security=false, stronger_when="none".
3. 같은 값 묶음: 새 사실끼리 또는 기존 사실과 값·단위가 같은 짝을 2쌍 이상 만듭니다. 기존 묶음({', '.join(groups)})에 넣으려면 그 묶음의 값·단위를 그대로 쓰고 같은 이름을 줍니다.
   새 묶음은 "G_" 로 시작하는 새 이름을 줍니다. 같은 값이 아니면 same_value_group은 "" 입니다. 묶음 밖에서 기존·새 사실과 값이나 표기가 겹치면 안 됩니다.
4. variants는 같은 값을 다르게 적은 표기 3개(예: "15분", "15 분", "십오 분"). 값+단위만 쓰고 '까지', '간', '동안', '이상' 같은 수식어를 붙이지 않습니다.
5. change_candidates는 value와 다른 새 값 2~3개. 숫자 값이면 숫자만 씁니다(예: value "30" → "60", "90").
6. depends_on: 다른 사실(기존 포함)에서 파생되는 사실이면 그 id, 대부분은 빈 배열.
7. id는 F_로 시작하는 대문자 영문(기존 id와 겹치지 않게), name은 한국어 설명. value는 숫자 값이면 단위 없이 숫자만.
8. 실제 회사·제품·서비스 이름 금지. 알고리즘·표준 이름(AES-256, TLS 1.3, SHA-256)은 허용.
"""


def auto_group(facts, seed_ids):
    """값과 단위가 같은 사실은 같은 값 짝이어야 한다(묶음 밖 같은 값은 모순). 새 사실이 만든 이런 짝을 한 묶음에 넣는다.
    묶음 이름은 이미 있는 묶음을 따르고, 없으면 G_<값><단위>. 혼자 남은 묶음은 푼다. 단위가 다른 충돌은 오류로 돌린다."""
    by_key = defaultdict(list)
    for fid, f in facts.items():
        by_key[(f["value"], unit_of(f))].append(fid)
    for (val, unit), fids in by_key.items():
        if len(fids) < 2 or all(f in seed_ids for f in fids):
            continue
        names = [facts[f]["same_value_group"] for f in fids if facts[f]["same_value_group"] and f in seed_ids] \
            or [facts[f]["same_value_group"] for f in fids if facts[f]["same_value_group"]]
        g = names[0] if names else f"G_{val}{UNIT_CODE.get(unit, 'X')}"
        for f in fids:
            facts[f]["same_value_group"] = g
    members = Counter(f["same_value_group"] for f in facts.values() if f["same_value_group"])
    for fid, f in facts.items():
        if f["same_value_group"] and members[f["same_value_group"]] < 2 and fid not in seed_ids:
            f["same_value_group"] = ""
    return facts


VARIANT_FIX = {"F_MEDICAL_STAFF_ROLE_ASSIGNMENT": ["4가지", "4 가지", "네 가지"]}


def postfix_facts(facts, seed_ids):
    """모델 출력을 결정적으로 고친다(기록: facts_rounds/facts_postfix.json).
    보관 기간은 길수록 강하므로 이름에 '보관'이 있는 새 보안 사실은 stronger_when=higher로 두고,
    단위가 섞인 표기('4개 권한')는 값+단위 표기로 바꾼다."""
    log = []
    for fid, f in facts.items():
        if fid in seed_ids:
            continue
        if f["security"] and "보관" in f["name"] and f["stronger_when"] != "higher":
            log.append({"fact": fid, "field": "stronger_when", "before": f["stronger_when"], "after": "higher",
                        "reason": "보관 기간은 길수록 강함"})
            f["stronger_when"] = "higher"
        if fid in VARIANT_FIX and f["variants"] != VARIANT_FIX[fid] and f["value"] == VARIANT_FIX[fid][0][0]:
            log.append({"fact": fid, "field": "variants", "before": f["variants"], "after": VARIANT_FIX[fid],
                        "reason": "표기마다 단위가 달라 문장에 넣으면 어색함"})
            f["variants"] = list(VARIANT_FIX[fid])
    write_json(ROUNDS / "facts_postfix.json", log)
    return facts


UNIT_CODE = {"분": "MIN", "시간": "HOUR", "일": "DAY", "회": "TIMES", "번": "TIMES", "자": "CHAR", "개": "EA", "초": "SEC",
             "개월": "MONTH", "주": "WEEK", "년": "YEAR", "%": "PCT"}


def explain_collisions(facts, seed_ids):
    """단위가 달라 같은 값 짝이 될 수 없는 충돌을, 새 사실의 값을 바꾸라는 지시로 바꾼다."""
    errs = []
    owner = {}
    for fid, f in facts.items():
        for v in {f["value"], *f["variants"]}:
            o = owner.setdefault(v, fid)
            if o != fid and (facts[o]["same_value_group"] != f["same_value_group"] or not f["same_value_group"]):
                new = fid if fid not in seed_ids else o
                other = o if new == fid else fid
                if new in seed_ids:
                    errs.append(f"{fid}와 {o}의 표기 {v!r}가 겹칩니다(같은 묶음 아님).")
                    continue
                used = sorted({x["value"] for x in facts.values()})
                errs.append(f"{new}의 값·표기 {v!r}가 {other}({facts[other]['name']})와 겹치는데 "
                            "단위가 달라 같은 값 짝이 될 수 없습니다. "
                            f"{new}의 value와 variants를 아직 쓰이지 않은 다른 값으로 바꾸세요"
                            f"(change_candidates도 겹치지 않게). 이미 쓰인 값: {', '.join(used)}")
    return list(dict.fromkeys(errs))


def placement_schema(fids):
    return {"type": "object", "required": ["placements"], "additionalProperties": False, "properties": {"placements": {
        "type": "array", "minItems": len(fids), "maxItems": len(fids), "items": {
            "type": "object", "required": ["id", "xxcs", "in_34"], "additionalProperties": False,
            "properties": {"id": {"type": "string", "enum": fids},
                           "xxcs": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": APPLICABLE}},
                           "in_34": {"type": "boolean"}}}}}}


def placement_prompt(facts, prev_hints):
    lines = []
    for fid, f in facts.items():
        old = prev_hints.get(fid)
        lines.append(fact_line(fid, f) + (f" [v0.2 배치: {', '.join(old) or '없음'}]" if old is not None else ""))
    t = PROFILE["targets"]["trace2_facts"]
    return f"""{BRIEF}

XXCS 소프트웨어 보안검증보고서의 보안 항목별 시험결과 표(시험방법, 확인결과)에 어떤 사실 값이 들어갈지 정하세요.
사실마다 그 값을 시험 절차나 확인 결과에서 직접 확인하는 보안 항목을 0~3개 고릅니다(xxcs). MDSR 3.4 보안 개요 문단에 들어갈 보안 사실이면 in_34=true.

조건:
1. 모든 사실을 정확히 한 번씩 출력합니다.
2. 보안 사실(보안 예)은 xxcs가 1개 이상입니다. 보안 사실은 여러 항목에서 확인되는 경우가 많습니다(예: 로그인 실패 허용 횟수 → IA-07, IA-06; 감사로그 보관 기간 → UC-04, TRE-01, UC-05; 전송 암호화 → SI-01, DC-03).
3. 보안이 아닌 기능 사실도 그 값을 시험에서 확인하면 고릅니다(예: 수면일기 입력 기한 → SI-05 입력값 검증, UC-06 타임스탬프; 동기화 주기 → SI-01, RA-02).
4. xxcs가 2개 이상인 사실이 {t + 2}개 이상이어야 합니다.
5. 한 보안 항목에는 사실을 4개 이하로 둡니다.
6. in_34=true는 xxcs가 있는 보안 사실만, 5개 이상.
7. 아래 목록의 항목만 씁니다(기본 상태에서 적용되지 않는 항목은 목록에 없습니다).

사실:
{chr(10).join(lines)}

보안 항목:
{SEC_LIST}
"""


MAX_PER_SEC = 4


def trim_placement(facts):
    """한 보안 항목에 사실이 MAX_PER_SEC개를 넘으면 줄인다(모델이 타임스탬프 항목에 기능 사실을 몰아 넣곤 한다).
    보안 사실, XXCS 항목이 2개 이상인 사실, 원래 순서로 남긴다. 뺀 내용은 facts_rounds/placement_trim.json."""
    order = list(facts)
    log = []
    for sec in APPLICABLE:
        here = [f for f in order if sec in facts[f]["xxcs"]]
        if len(here) <= MAX_PER_SEC:
            continue
        ranked = sorted(here, key=lambda f: (not facts[f]["security"], len(facts[f]["xxcs"]) < 2, order.index(f)))
        for f in ranked[MAX_PER_SEC:]:
            facts[f]["xxcs"] = [s for s in facts[f]["xxcs"] if s != sec]
            log.append({"sec": sec, "fact": f, "reason": f"{sec}에 사실 {len(here)}개, {MAX_PER_SEC}개만 남김"})
    write_json(ROUNDS / "placement_trim.json", log)
    return facts


def reqs_schema(fids):
    return {"type": "object", "required": ["reqs"], "additionalProperties": False, "properties": {"reqs": {
        "type": "array", "minItems": len(ALL_REQS), "maxItems": len(ALL_REQS), "items": {
            "type": "object", "required": ["id", "title", "security", "uses"], "additionalProperties": False,
            "properties": {"id": {"type": "string", "enum": ALL_REQS}, "title": {"type": "string", "minLength": 2},
                           "security": {"type": "boolean"},
                           "uses": {"type": "array", "minItems": 1, "maxItems": 3,
                                    "items": {"type": "string", "enum": fids}}}}}}}


def reqs_prompt(facts, fixed):
    t = PROFILE["targets"]
    fixed_lines = "\n".join(f"- {rid}: {title}" for rid, title in fixed.items())
    new_ids = [r for r in ALL_REQS if r not in fixed]
    return f"""{BRIEF}

아래 사실 목록을 바탕으로 요구사항 {len(ALL_REQS)}개(모바일 애플리케이션 {ALL_REQS[0]} ~ Req. {PROFILE['app_reqs'][1]}, 서버 Req. {PROFILE['server_reqs'][0]} ~ Req. {PROFILE['server_reqs'][1]})의 제목, 보안 요구사항 여부, 쓰는 사실을 정하세요.

조건:
1. 아래 요구사항은 제목을 그대로 씁니다.
{fixed_lines}
2. 새 요구사항({', '.join(new_ids)})은 새 기능(이완 훈련, 수면 효율 리포트, 의료진 메시지, 처방 변경 이력, 데이터 보존·파기, 의료진 계정·권한 관리 등)으로 짧은 한국어 명사구 제목을 줍니다. 제목은 서로 달라야 합니다.
3. 각 요구사항은 사실 1~3개를 uses로 씁니다. 모든 사실이 최소 한 요구사항에서 쓰여야 합니다.
4. 같은 값을 실제로 함께 참조하는 요구사항이 있으면 같은 사실을 둘 다 uses에 넣습니다(예: 동기화 주기를 동기화와 대시보드 조회가 함께 씀). 2개 이상 요구사항이 쓰는 사실이 {t['multi_req_facts'] + 2}개 이상이어야 합니다.
5. security: 보안 기능(로그인, 잠금, 세션, 비밀번호, 암호화, 감사로그, 백업, 계정·권한, 개인정보 파기 등)을 정의하는 요구사항은 true.
   보안 사실(보안 예)을 쓰는 요구사항은 반드시 true입니다. 보안이 아닌 기능 요구사항(수면일기, 교육, 알림, 리포트 등)은 보안 사실을 쓰지 않고 false입니다.
   security=false인 요구사항이 {t['nonsec_reqs'] + 2}개 이상이어야 합니다.

사실 목록:
{chr(10).join(fact_line(k, v) for k, v in facts.items())}
"""


def generate_links(facts, reqs):
    """보안 항목마다 한 번씩 묻는다. 그 항목에 놓인 사실을 덮는 relevant Req는 코드가 정하고(greedy cover),
    모델은 추가 relevant와 relevant=false 연결(relevant와 같은 개수, 비율 0.5 미보정)을 고른다."""
    cache = ROUNDS / "links_per_sec.json"
    if cache.exists():
        links = read_json(cache)
        if not check_facts.check_links_section(links, reqs, facts):
            print("[links] reuse accepted output", flush=True)
            return links
    users = check_facts.users_of(reqs)
    req_lines = "\n".join(f"- {rid}: {r['title']}{'' if r['security'] else ' (기능)'}" for rid, r in reqs.items())
    app = base_applicability(RULES, CYBER_BASE)

    def cover(placed):
        """항목에 놓인 사실마다 그 사실을 쓰는 Req가 relevant에 하나는 있어야 trace가 생긴다. 많이 덮는 Req부터 고른다."""
        left, chosen = set(placed), []
        while left:
            best = max(ALL_REQS, key=lambda r: (sum(1 for f in left if r in users[f]), -ALL_REQS.index(r)))
            chosen.append(best)
            left = {f for f in left if best not in users[f]}
        return chosen

    nonsec = [rid for rid, r in reqs.items() if not r["security"]]

    def one(sec, k):
        placed = [fid for fid, f in facts.items() if sec in f["xxcs"]]
        must = cover(placed)
        # 삭제 케이스에 trace가 생기도록 비보안 Req를 돌아가며 하나씩 과연결로 지정한다.
        rot = [r for r in nonsec[k % len(nonsec):] + nonsec[:k % len(nonsec)] if r not in must]
        assigned = rot[0] if rot else None
        n_ex = 1 if len(must) <= 1 else 0  # 개수는 코드가 정한다(모델이 개수 맞추기를 자주 틀림)
        n_ov = len(must) + n_ex - (1 if assigned else 0)  # 지정된 과연결 1개는 코드가 붙인다
        pool = [r for r in ALL_REQS if r not in must and r != assigned]
        schema = {
            "type": "object", "required": ["extra_relevant", "overlinked_reqs"], "additionalProperties": False,
            "properties": {
                "extra_relevant": {"type": "array", "minItems": n_ex, "maxItems": n_ex,
                                   "items": {"type": "string", "enum": pool}},
                "overlinked_reqs": {"type": "array", "minItems": n_ov, "maxItems": n_ov,
                                    "items": {"type": "string", "enum": pool}},
            },
        }

        def validate(v):
            errs = []
            ex, ov = v["extra_relevant"], v["overlinked_reqs"]
            rel = must + ex
            if len(set(rel)) != len(rel) or len(set(ov)) != len(ov) or set(rel) & set(ov):
                errs.append(f"고정 relevant({', '.join(must) or '없음'}), extra_relevant, overlinked_reqs는 "
                            "서로 겹치지 않고 중복 없이")
            return errs

        fixed_txt = ""
        if must:
            fixed_txt = (f"이 보안 항목의 시험에서 확인하는 사실({', '.join(facts[f]['name'] for f in placed)})을 쓰는 Req 중 "
                         f"{', '.join(must)}는 relevant로 이미 정해졌습니다.\n")
        status = ""
        if app[sec][0] != "적용":
            status = "(이 제품의 기본 상태에서는 적용 예외 항목이지만 표에는 관행적으로 연결 Req를 적습니다.)\n"
        already = f"{assigned}는 이미 과연결로 정해졌고, 그 밖에 " if assigned else ""
        prompt = (
            f"{BRIEF}\n\nMDSR 6절 사이버보안 표의 보안 항목 {sec} '{SEC_ITEMS[sec]}'에 "
            f"연결할 소프트웨어 기능 요구사항을 고르세요.\n{status}{fixed_txt}"
            f"- extra_relevant: 이미 정해진 것 말고, 이 보안 항목의 검증이 실제로 다루는 요구사항 정확히 {n_ex}개"
            f"{'(빈 배열)' if not n_ex else ''}. "
            "보안 기능 Req뿐 아니라 이 항목이 실제로 보호·검증하는 기능 Req(표시 '(기능)')도 될 수 있습니다"
            "(예: 입력값 검증 ↔ 수면일기 기록, 권한 부여 ↔ 의료진 대시보드 조회, 타임스탬프 ↔ 처방 변경 이력).\n"
            "- overlinked_reqs: 표에는 관행적으로 함께 적히지만 이 보안 항목 검증과는 실제 관련이 약한 요구사항. "
            f"{already}정확히 {n_ov}개{'(빈 배열)' if not n_ov else ''}, relevant와 겹치지 않게.\n"
            f"\n요구사항 목록:\n{req_lines}"
        )
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        v = chat_json(msgs, server_schema(schema), prompt_version="v03-links-per-sec-v5",
                      task=f"v03/{PROFILE['id']}/links/{sec}", temperature=TEMP, seed=SEED + 300 + k, max_tokens=300,
                      validate=lambda x: check_schema(x, schema) + validate(x))
        overlinked = ([assigned] if assigned else []) + v["overlinked_reqs"]
        return ([{"req": r, "sec": sec, "relevant": True} for r in must + v["extra_relevant"]]
                + [{"req": r, "sec": sec, "relevant": False} for r in overlinked])

    with ThreadPoolExecutor(max_workers=8) as pool:
        parts = list(pool.map(lambda a: one(*a), [(s, k) for k, s in enumerate(LINK_SECS)]))
    links = [link for part in parts for link in part]
    errs = check_facts.check_links_section(links, reqs, facts)
    print(f"[links] {len(links)} links, {len(errs)} error(s)", flush=True)
    for e in errs:
        print("    -", e, flush=True)
    write_json(ROUNDS / ("links_per_sec.json" if not errs else "links_per_sec_failed.json"), links)
    if errs:
        raise SystemExit(f"links: {errs[:5]}")
    return links


def main():
    seed = seed_facts()
    v02 = read_json((PROD / PROFILE["seed_facts_from"]).resolve())
    prev_hints = {fid: [h.split("|")[1] for h in f["sites_hint"] if h.startswith("XXCS|")]
                  for fid, f in v02["facts"].items()}

    def conv_new(raw):
        merged = dict(seed)
        for f in raw["facts"]:
            fid = f.pop("id")
            if fid in merged:
                raise ValueError(f"{fid}는 기존 사실 id와 겹칩니다")
            merged[fid] = f
        return postfix_facts(auto_group(merged, set(seed)), set(seed))

    def check_new(merged):
        # 겹침 오류는 explain_collisions가 고칠 수 있는 지시로 바꿔 준다.
        errs = [e for e in check_facts.check_facts_section(merged) if not e.startswith(("value ", "surface "))]
        return explain_collisions(merged, set(seed)) + errs

    facts = generate_section("new_facts", "v03-new-facts-v2", new_facts_prompt(seed), new_fact_schema(), conv_new,
                             check_new, 9000)

    fids = list(facts)

    def conv_place(raw):
        out = {fid: dict(f) for fid, f in facts.items()}
        seen = set()
        for p in raw["placements"]:
            if p["id"] in seen:
                raise ValueError(f"{p['id']} 중복")
            seen.add(p["id"])
            out[p["id"]]["xxcs"], out[p["id"]]["in_34"] = p["xxcs"], p["in_34"]
        missing = [f for f in fids if f not in seen]
        if missing:
            raise ValueError(f"빠진 사실: {', '.join(missing)}")
        return trim_placement(out)

    facts = generate_section("placement", "v03-placement-v1", placement_prompt(facts, prev_hints),
                             placement_schema(fids), conv_place, check_facts.check_placement, 6000)

    fixed = {rid: r["title"] for rid, r in v02["reqs"].items() if rid in ALL_REQS}

    def conv_reqs(raw):
        """v0.2 Req 제목은 코드가 고정하고, 보안 사실을 쓰는 Req는 security=true로 둔다(spec.md 7절)."""
        out = {}
        for r in raw["reqs"]:
            if r["id"] in out:
                raise ValueError(f"{r['id']} 중복")
            sec = r["security"] or any(facts[u]["security"] for u in r["uses"] if u in facts)
            out[r["id"]] = {"title": fixed.get(r["id"], r["title"]), "security": sec, "uses": r["uses"]}
        return out

    reqs = generate_section("reqs", "v03-reqs-v2", reqs_prompt(facts, fixed), reqs_schema(fids), conv_reqs,
                            lambda v: check_facts.check_reqs_section(v, facts, fixed), 6000)

    links = generate_links(facts, reqs)

    # MDSR/MDDR 위치 힌트는 reqs.uses에서 붙인다(v0.2와 같은 sites_hint 형식).
    users = check_facts.users_of(reqs)
    for fid, f in facts.items():
        f["sites_hint"] = ([f"{d}|{rid}" for rid in users[fid] for d in ("MDSR", "MDDR")]
                           + (["MDSR|3.4"] if f["in_34"] else []) + [f"XXCS|{s}" for s in f["xxcs"]])
    write_json(PRODUCT / "facts.json", {"facts": facts, "reqs": reqs, "cyber": dict(CYBER_BASE)})
    write_json(PRODUCT / "links.json", {"links": links, "rules": RULES,
                                        "overlink_note": "relevant=false 비율 0.5는 실제 문서 판정 결과가 없어 정한 값(미보정)"})
    errs = check_facts.run_all(read_json(PRODUCT / "facts.json"), read_json(PRODUCT / "links.json"))
    bad = {k: v for k, v in errs.items() if v}
    print("final check:", "PASS" if not bad else bad)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
