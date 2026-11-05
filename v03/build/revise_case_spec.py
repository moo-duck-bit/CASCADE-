"""사람이 고른 180개 시트(sheets/case_spec_180_v0.csv)를 고쳐 sheets/case_spec_180.csv 를 만든다.

고친 것(2026-11-04, 이유는 spec.md 변경 기록):
- 유형·변경·문서 조합까지 같은 중복 행을 다른 변경으로 바꿈(T1 20행, T2 16행).
- 파생 사실(F_UNLOCK_NOTIFICATION_TIME, depends_on F_ACCOUNT_LOCK_DURATION)만 바꾸는 T1 행 2개를 바꿈.
- T2는 규격상 바꿀 수 있는 속성이 5개라 단일 변경은 문서 조합 4개까지 20개뿐이다. 나머지 16개는 속성 2개 동시 변경.
- T3 추가의 연결 보안 항목을 순번 배정에서 기능 의미에 맞는 항목으로 바꾸고, 본문에 넣을 보안 요소를 메모에 적음.
- 케이스마다 기본 문서에 따로 적용하므로 새 Req 번호를 앱 Req. 19, 서버 Req. 109로 통일.
- T3_41 '제조사 업데이트 수신'은 remote_update=false와 어긋나(SI-08·SI-09 적용 사유) 기능을 바꿈.
새 T1 행의 '노리는 경로'는 make_cases.t1_parts 로 계산한다. 원본 행은 위 수정 외에는 그대로 둔다.
"""

import csv
import re

from make_cases import NEWREQ_RE, SHEETS, World, t1_parts
from datagen import SEC_ITEMS, with_unit

SRC = SHEETS / "case_spec_180_v0.csv"
DST = SHEETS / "case_spec_180.csv"
FACT_LIST = SHEETS / "fact_list.csv"
DOCSETS = {1: "MDSR", 2: "MDSR+XXCS", 3: "MDSR+MDDR+XXCS"}

# 중복·파생 행 자리 → (사실, 출발 Req, 새 값(숫자만), 문서 조합). 같은 변경을 3문서와 MDSR+XXCS 두 조합에 둔다.
T1_NEW = {
    "T1_07": ("F_ACCOUNT_LOCK_DURATION", "Req. 2", "45", 3),
    "T1_11": ("F_PASSWORD_MIN_LENGTH", "Req. 1", "12", 3),
    "T1_12": ("F_INACTIVE_ACCOUNT_LOCK_DURATION", "Req. 2", "30", 3),
    "T1_13": ("F_PASSWORD_CHANGE_INTERVAL", "Req. 108", "30", 3),
    "T1_14": ("F_BACKUP_INTERVAL", "Req. 7", "6", 3),
    "T1_15": ("F_AUDIT_LOG_RETENTION", "Req. 6", "365", 3),
    "T1_16": ("F_PII_DELETION_DEADLINE", "Req. 7", "180", 3),
    "T1_17": ("F_PRESCRIPTION_CHANGE_LOG_RETENTION", "Req. 18", "730", 3),
    "T1_18": ("F_MEDICAL_STAFF_ACCESS_LOG_RETENTION", "Req. 6", "365", 3),
    # 2문서(MDSR+MDDR)·1문서에만 있던 변경을 3문서에도 둔다(같은 변경, 문서 수만 다름)
    "T1_19": ("F_BACKUP_INTERVAL", "Req. 105", "12", 3),
    "T1_20": ("F_SYNC_INTERVAL", "Req. 101", "120", 3),
    "T1_21": ("F_LOGIN_FAILURE_LIMIT", "Req. 1", "4", 3),
    "T1_22": ("F_MEDICAL_STAFF_ACCESS_LOG_AUDIT_INTERVAL", "Req. 104", "30", 3),
    "T1_23": ("F_ACCOUNT_LOCK_DURATION", "Req. 2", "45", 2),
    "T1_24": ("F_PASSWORD_MIN_LENGTH", "Req. 1", "12", 2),
    "T1_31": ("F_INACTIVE_ACCOUNT_LOCK_DURATION", "Req. 2", "30", 2),
    "T1_35": ("F_PASSWORD_CHANGE_INTERVAL", "Req. 108", "30", 2),
    "T1_36": ("F_BACKUP_INTERVAL", "Req. 7", "6", 2),
    "T1_37": ("F_AUDIT_LOG_RETENTION", "Req. 6", "365", 2),
    "T1_38": ("F_PII_DELETION_DEADLINE", "Req. 7", "180", 2),
    "T1_39": ("F_PRESCRIPTION_CHANGE_LOG_RETENTION", "Req. 18", "730", 2),
    "T1_40": ("F_MEDICAL_STAFF_ACCESS_LOG_RETENTION", "Req. 6", "365", 2),
}

# T2 중복 자리 → (속성 2개, 문서 조합). 속성마다 6~7번 나오게 골랐다.
T2_NEW = {
    "T2_06": (("stores_pii", "samd"), "XXCS"),
    "T2_07": (("samd", "mobile_code"), "XXCS"),
    "T2_08": (("mobile_code", "e_signature"), "XXCS"),
    "T2_09": (("e_signature", "remote_update"), "XXCS"),
    "T2_10": (("stores_pii", "e_signature"), "XXCS"),
    "T2_11": (("samd", "remote_update"), "XXCS"),
    "T2_12": (("mobile_code", "remote_update"), "XXCS"),
    "T2_18": (("samd", "e_signature"), "MDSR+XXCS"),
    "T2_24": (("stores_pii", "remote_update"), "MDDR+XXCS"),
    "T2_30": (("stores_pii", "samd"), "MDSR+MDDR+XXCS"),
    "T2_31": (("stores_pii", "mobile_code"), "MDSR+MDDR+XXCS"),
    "T2_32": (("stores_pii", "remote_update"), "MDSR+MDDR+XXCS"),
    "T2_33": (("samd", "e_signature"), "MDSR+MDDR+XXCS"),
    "T2_34": (("mobile_code", "e_signature"), "MDSR+MDDR+XXCS"),
    "T2_35": (("mobile_code", "remote_update"), "MDSR+MDDR+XXCS"),
    "T2_36": (("e_signature", "remote_update"), "MDSR+MDDR+XXCS"),
}

# T3 추가 → (연결 보안 항목, 본문에 넣을 보안 요소). 제목을 바꾼 행은 세 번째 값에 새 제목.
T3_LINKS = {
    "T3_31": ("SI-05", "목표 취침·기상 시각 입력값의 허용 범위 검증"),
    "T3_32": ("DC-01", "섭취 기록을 기기에 암호화해 저장"),
    "T3_33": ("UC-06", "낮잠 시작·종료 시각을 타임스탬프로 기록"),
    "T3_34": ("DC-03", "리포트 전송 구간을 암호화"),
    "T3_35": ("UC-04", "의료진이 점검 결과를 수정하면 감사기록 생성"),
    "T3_36": ("SI-06", "조도 센서 값을 못 읽으면 미리 정한 기본 안내로 전환"),
    "T3_37": ("SI-05", "주말 기상 시각 입력값의 허용 범위 검증"),
    "T3_38": ("SI-01", "연결 회복 후 서버 전송 데이터의 무결성 확인"),
    "T3_39": ("UC-01", "동의한 보호자에게만 열람 권한 부여"),
    "T3_40": ("SI-05", "웨어러블에서 받은 데이터의 형식·범위 검증"),
    "T3_41": ("IA-01", "기록을 옮기기 전 본인 인증", "기기 변경 시 기록 이전"),
    "T3_42": ("SI-04", "재생 전 안내 콘텐츠 파일의 무결성 점검"),
    "T3_43": ("SI-07", "안내 시점 계산에 실패하면 내부 정보가 없는 오류 메시지"),
    "T3_44": ("SI-05", "점검 항목 응답 값 검증"),
    "T3_45": ("UC-04", "의료진이 메모를 작성·수정하면 감사기록 생성"),
    "T3_46": ("DC-01", "복용 기록을 기기에 암호화해 저장"),
    "T3_47": ("SI-05", "메모 길이와 허용 문자 검증"),
    "T3_48": ("UC-06", "깬 시점마다 타임스탬프 기록"),
    "T3_49": ("SI-06", "응답 저장에 실패하면 직전 저장 상태 유지"),
    "T3_50": ("RA-02", "점검 기록을 정기 백업 대상에 포함"),
    "T3_51": ("DC-01", "활동 기록을 기기에 암호화해 저장"),
    "T3_52": ("SI-05", "운동 종류·시간 입력값의 허용 범위 검증"),
    "T3_53": ("UC-06", "식사 시각을 타임스탬프로 기록"),
    "T3_54": ("UC-01", "수집 전에 사용자가 접근 권한을 허용"),
    "T3_55": ("SI-04", "배포 전 준비 안내 콘텐츠의 무결성 점검"),
    "T3_56": ("SI-05", "설문 응답 값 검증"),
    "T3_57": ("UC-01", "등록·동의한 보호자에게만 발송"),
    "T3_58": ("UC-04", "메모를 열람하면 감사기록 생성"),
    "T3_59": ("DC-03", "내보내는 파일을 암호화"),
    "T3_60": ("SI-05", "음량 설정값의 허용 범위 검증"),
    "T3_61": ("SI-06", "화면 설정을 못 읽으면 미리 정한 기본 화면으로 표시"),
    "T3_62": ("DC-01", "잠금 화면에서는 민감한 수면 기록을 가림"),
    "T3_63": ("RA-02", "백업이 끝나지 않았으면 안내"),
    "T3_64": ("SI-01", "전송 데이터 무결성 확인에 실패하면 알림"),
    "T3_65": ("SI-07", "처리 중 오류가 나도 응답에 내부 정보를 담지 않음"),
    "T3_66": ("IA-08", "로그인 전에 시스템 사용 안내 문구를 보여 주고 확인받음"),
    "T3_67": ("UC-06", "달성 일시를 타임스탬프로 기록"),
    "T3_68": ("UC-06", "연속 일수를 기록 타임스탬프로 계산"),
    "T3_69": ("SI-05", "휴일 일정 입력값 검증"),
    "T3_70": ("UC-06", "시간대가 바뀌어도 기록 타임스탬프 기준 유지"),
    "T3_71": ("DC-01", "타인 정보가 담길 수 있는 기록을 기기에 암호화해 저장"),
    "T3_72": ("SI-05", "점검 응답 값 검증"),
}

# 값 변경 동반 복합 중 고른 사실이 출발 Req 밖에 놓이지 않아(fact_list same_value 0) same_value 경로가 안 나오는 행.
# 새 Req 부분은 그대로 두고 값 변경만 입력 문서 안에 same_value 칸이 있는 비보안 사실로 바꾼다.
T3_VALUE_FIX = {
    "T3_55": ("F_DEFAULT_WAKE_TIME", "Req. 10", "8:00"),
    "T3_56": ("F_DEFAULT_CBT_SESSION_REMINDER", "Req. 14", "19:00"),
    "T3_57": ("F_DEFAULT_WAKE_TIME", "Req. 8", "7:30"),
    "T3_58": ("F_DEFAULT_SLEEP_LIMIT", "Req. 8", "23:00"),
    "T3_59": ("F_DEFAULT_CBT_SESSION_REMINDER", "Req. 9", "20:00"),
}

ATTR_NAME = {}  # 시트에 이미 쓰인 '속성 (이름)' 표기를 그대로 쓴다
SEC_NAME_RE = re.compile(r"연결 예정 ([A-Z]+-\d+)")
OLD_TITLE_RE = re.compile(r"새 Req (.+?) 추가")


def per_origin(text):
    """'Req. 1:7 Req. 2:4' → {'Req. 1': '7', ...}"""
    return dict(re.findall(r"(Req\. \d+):(\d+)", text))


def main():
    w = World()
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    with open(FACT_LIST, encoding="utf-8-sig", newline="") as f:
        flist = {r["사실"]: r for r in csv.DictReader(f)}
    header_i = next(i for i, r in enumerate(lines) if r and r[0] == "case_id")
    header = lines[header_i]
    rows = [dict(zip(header, r)) for r in lines[header_i + 1:] if r]
    by_id = {r["case_id"]: r for r in rows}
    for r in rows:
        what = r["무엇을 바꾸나"]
        if what.startswith(("stores_pii", "samd", "mobile_code", "e_signature", "remote_update")):
            ATTR_NAME.setdefault(what.split(" (")[0], what)
    sec_names = SEC_ITEMS

    for cid, (fid, rid, nv, n) in T1_NEW.items():
        r, fact = by_id[cid], w.facts[fid]
        docs = set(DOCSETS[n].split("+"))
        impact, _, _, _ = t1_parts(w, fid, rid, docs, nv)
        order = ("origin", "same_id", "trace", "same_value")
        paths = [p for p in order if p in {e["path"] for e in impact}]
        fl = flist[fid]
        tr, sv = per_origin(fl["출발 Req별 trace 행 수"])[rid], per_origin(fl["출발 Req별 same_value 칸 수"])[rid]
        from_ = f"원래 {by_id[cid]['무엇을 바꾸나'].split(' (')[0]} {by_id[cid]['변경']}"
        notes = [f"trace {tr}. same_value {sv}.", "같은 변경을 문서 수만 달리한 행이 있음."]
        old_s, new_s = with_unit(fact, fact["value"]), with_unit(fact, nv)
        cand = [c for c in fl["바꿀 후보(보안 약화 아닌 것만)"].split(" / ") if c.split("(")[0].strip() == new_s]
        if not cand:
            raise SystemExit(f"{cid}: {new_s} is not a change candidate of {fid}")
        if "다른 사실 값과 같음" in cand[0]:
            notes.append("새 값이 다른 사실의 현재 값과 같음(fact_list 표시).")
        if w.dependents(fid):
            notes.append(f"파생 사실 {', '.join(w.dependents(fid))} 칸도 정답(new_value 없음).")
        reason = "파생 사실만 바꾸던 행" if "UNLOCK" in r["무엇을 바꾸나"] else "중복 행"
        notes.append(f"[수정] {reason} 대체({from_}).")
        r.update({
            "무엇을 바꾸나": f"{fid} ({fact['name']}), 출발 {rid}",
            "변경": f"{old_s}→{new_s}",
            "노리는 경로": ", ".join(paths),
            "엮인 문서 수": f"{n} ({DOCSETS[n]})",
            "메모": " ".join(notes),
        })

    for cid, ((a1, a2), docset) in T2_NEW.items():
        r = by_id[cid]
        olds = [str(w.cyber[a]).lower() for a in (a1, a2)]
        news = [str(not w.cyber[a]).lower() for a in (a1, a2)]
        r.update({
            "무엇을 바꾸나": f"{ATTR_NAME[a1]} + {ATTR_NAME[a2]}",
            "변경": f"{olds[0]}→{news[0]}, {olds[1]}→{news[1]}",
            "노리는 경로": "rule",
            "엮인 문서 수": f"{len(docset.split('+'))} ({docset})",
            "메모": "속성 2개 동시 변경. 약화 아님(보안 항목이 새로 적용됨). [수정] 중복 행 대체.",
        })

    for cid, spec in T3_LINKS.items():
        r = by_id[cid]
        sec, hook = spec[0], spec[1]
        old_sec = SEC_NAME_RE.search(r["메모"]).group(1)
        comp, old_rid = NEWREQ_RE.search(r["무엇을 바꾸나"]).groups()
        new_rid = "Req. 19" if comp == "앱" else "Req. 109"
        r["무엇을 바꾸나"] = r["무엇을 바꾸나"].replace(f"새 {comp} {old_rid}", f"새 {comp} {new_rid}")
        fixes = [f"번호 {old_rid}→{new_rid}"] if old_rid != new_rid else []
        if sec != old_sec:
            fixes.append(f"연결 {old_sec}({sec_names[old_sec]})는 순번 배정이라 바꿈")
        if len(spec) > 2:
            old_title = OLD_TITLE_RE.search(r["변경"]).group(1)
            r["변경"] = r["변경"].replace(f"새 Req {old_title} 추가", f"새 Req {spec[2]} 추가")
            fixes.append(f"제목 '{old_title}'은 remote_update=false와 어긋나(SI-08·SI-09 적용 사유) 바꿈")
        if cid in T3_VALUE_FIX:
            fid, rid, nv = T3_VALUE_FIX[cid]
            fact = w.facts[fid]
            old_what, old_change = r["무엇을 바꾸나"].split(" + ", 1)[1], r["변경"].rsplit(", ", 1)[1]
            r["무엇을 바꾸나"] = f"{r['무엇을 바꾸나'].split(' + ')[0]} + {fid}, 출발 {rid}"
            r["변경"] = f"{r['변경'].rsplit(', ', 1)[0]}, {with_unit(fact, fact['value'])}→{with_unit(fact, nv)}"
            fixes.append(f"값 변경 {old_what.split(',')[0]} {old_change}는 same_value 칸이 없어(fact_list 0) 바꿈")
        head = r["메모"].split(" 연결 예정")[0] if r["유형"] == "T3-추가복합" else ""
        head = head.rstrip(".") + ". " if head else ""
        r["메모"] = (f"{head}연결 {sec}({sec_names[sec]}). 본문에 넣을 보안 요소: {hook}. 제목은 초안. "
                     f"[수정] {'; '.join(fixes)}.")

    with open(DST, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        for r in lines[:header_i]:
            wr.writerow(r)
        wr.writerow(["# 2026-11-04 수정: 중복 36행 대체, 파생 사실 단독 변경 2행 대체, T2 16행은 속성 2개 동시 변경, "
                     "T3 추가 연결 항목을 기능 의미로 재배정, 새 Req 번호를 앱 Req. 19·서버 Req. 109로 통일. "
                     "원본은 case_spec_180_v0.csv, 수정 코드는 build/revise_case_spec.py"] + [""] * (len(header) - 1))
        wr.writerow(header)
        for r in rows:
            wr.writerow([r[h] for h in header])
    print(f"wrote {DST} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
