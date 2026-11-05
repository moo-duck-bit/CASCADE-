"""v0.3 문서 3종 본문을 항목 단위로 LLM에 쓰게 한다(JSON schema 강제). v0.2와 같고 MDSR 기준은 정확히 2문단이다.

출력: v03/products/<P>/product/content.json (항목별 캐시: v03/runs/<P>/content_cache)
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from common import ALL_REQS, PRODUCT, PROFILE, RUNS, SEED
from datagen import (LINK_SECS, SEC_ITEMS, base_applicability, make_validator, read_json, run_cached, token_lines,
                     write_json)
from llm import chat_json

TEMP = 0.3
# 캐시된 항목은 그대로 두고 실패한 항목만 새 버전 프롬프트로 만들었다(gen_log의 prompt_version으로 구분).
VERSION = {"mdsr": "v03-content-mdsr-v1", "mdsr34": "v03-content-mdsr34-v1.2", "mddr": "v03-content-mddr-v1",
           "xxcs": "v03-content-xxcs-v1.2"}
N_CRIT = 2

SYSTEM = (
    "당신은 의료기기 소프트웨어 인허가 문서(요구사항명세서, 설계명세서, 보안검증보고서)를 쓰는 기술 문서 작성자입니다. "
    f"{PROFILE['system_role']}의 문서를 씁니다. "
    "규제 문서 문체(평서형 '~한다', '~하여야 한다', 시험 결과는 '~을 확인함')로 씁니다. "
    "사실 값이 들어갈 자리에는 반드시 주어진 {{F_...}} 토큰을 그대로 씁니다. "
    "숫자, 횟수, 기간, 빈도, 버전, 알고리즘 이름을 직접 쓰지 않습니다. "
    "토큰은 '15분', '5회', 'AES-256'처럼 단위까지 포함한 표현으로 바뀌므로, "
    "토큰 바로 뒤에 단위(회, 분, 자, 일, 시간, 번, 개 등)를 붙이지 말고 조사(을/를, 이/가, 의, 동안, 이상 등)만 붙입니다. "
    "실제 회사·제품·서비스 이름을 쓰지 않습니다. 출력은 지정된 JSON 하나만 냅니다."
)


def seed_for(key):
    return SEED + int(hashlib.sha256(key.encode()).hexdigest()[:6], 16)


def ask(prompt, schema, version, task, seed_key, max_tokens, validate):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    return chat_json(msgs, schema, prompt_version=version, task=f"v03/{PROFILE['id']}/content/{task}",
                     temperature=TEMP, seed=seed_for(seed_key), max_tokens=max_tokens, validate=validate)


def gen_mdsr(rid, reqs, facts):
    uses = reqs[rid]["uses"]
    schema = {
        "type": "object", "required": ["설명", "목적", "기준"], "additionalProperties": False,
        "properties": {
            "설명": {"type": "string", "minLength": 10},
            "목적": {"type": "string", "minLength": 5},
            "기준": {"type": "array", "minItems": N_CRIT, "maxItems": N_CRIT,
                   "items": {"type": "string", "minLength": 5}},
        },
    }
    prompt = (
        f"MDSR 소프트웨어 기능 요구사항 {rid} '{reqs[rid]['title']}'의 표 칸을 쓰세요.\n"
        f"- 설명: 요구사항 문장 1~2개 ('~하여야 한다').\n- 목적: 한 문장.\n- 기준: 확인 가능한 판정 기준 정확히 {N_CRIT}개.\n"
        f"- 아래 토큰을 모두 한 번 이상 설명 또는 기준에 넣으세요. 다른 토큰·숫자는 쓰지 않습니다.\n{token_lines(uses, facts)}"
    )
    return ask(prompt, schema, VERSION["mdsr"], f"mdsr/{rid}", "mdsr" + rid, 900, make_validator(uses, uses, facts))


def gen_mdsr34(facts):
    fids = [fid for fid, f in facts.items() if f["in_34"]]
    schema = {
        "type": "object", "required": ["문단"], "additionalProperties": False,
        "properties": {
            "문단": {"type": "array", "minItems": 2, "maxItems": 8, "items": {"type": "string", "minLength": 10}},
        },
    }
    prompt = (
        "MDSR 3.4절(일반적 제한)에 들어갈 '보안 개요' 문단 2~8개를 쓰세요. "
        "인증, 세션, 암호화, 감사로그, 백업 등 보안 제한을 요약합니다. 요구사항 번호는 쓰지 않습니다.\n"
        "아래 토큰을 모두 한 번 이상 넣으세요. 토큰이 많으므로 문단 하나에 관련 토큰 2~4개를 묶어 쓰고, 문단은 최대 8개입니다. "
        f"다른 토큰·숫자는 쓰지 않습니다.\n{token_lines(fids, facts)}"
    )
    return ask(prompt, schema, VERSION["mdsr34"], "mdsr34", "mdsr34", 1200, make_validator(fids, fids, facts))


def gen_mddr(rid, reqs, facts):
    uses = reqs[rid]["uses"]
    schema = {
        "type": "object", "required": ["설계"], "additionalProperties": False,
        "properties": {
            "설계": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "minLength": 10}},
        },
    }
    comp = "모바일 애플리케이션" if int(rid.split()[1]) < 100 else "서버"
    prompt = (
        f"MDDR '요구사항을 반영한 설계 사항'의 {rid} '{reqs[rid]['title']}' ({comp}) 설계 bullet 1~3개를 쓰세요.\n"
        f"- 구현 방식을 설명하는 문장('~하도록 구현한다', '~한다').\n"
        f"- 아래 토큰을 모두 한 번 이상 넣으세요. 다른 토큰·숫자는 쓰지 않습니다.\n{token_lines(uses, facts)}"
    )
    return ask(prompt, schema, VERSION["mddr"], f"mddr/{rid}", "mddr" + rid, 700, make_validator(uses, uses, facts))


def gen_xxcs(sec, facts, reqs, links):
    fids = [fid for fid, f in facts.items() if sec in f["xxcs"]]
    rel = [link["req"] for link in links if link["sec"] == sec and link["relevant"]]
    schema = {
        "type": "object", "required": ["시험방법", "확인결과", "비고"], "additionalProperties": False,
        "properties": {
            "시험방법": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "string", "minLength": 8}},
            "확인결과": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string", "minLength": 8}},
            "비고": {"type": "string"},
        },
    }
    ctx = ", ".join(f"{r} {reqs[r]['title']}" for r in rel) or "(없음)"
    prompt = (
        f"XXCS 소프트웨어 보안검증보고서의 시험결과 표를 쓰세요. 보안 항목: {sec} {SEC_ITEMS[sec]}.\n"
        f"- 시험방법: 이 보안 항목 본래의 검증 절차 2~4단계 ('~한다').\n- 확인결과: 결과 문장 1~4개 ('~을 확인함').\n"
        f"- 비고: 특이사항이 없으면 빈 문자열.\n- 관련 기능(참고): {ctx}\n"
        f"- 토큰 규칙:\n{token_lines(fids, facts)}"
    )
    if fids:
        prompt += ("\n- 위 토큰을 모두 시험방법 또는 확인결과에 넣으세요. "
                   "시험방법·확인결과는 각각 최대 4문장이므로 토큰이 많으면 한 문장에 2개 이상 묶어 쓰세요. "
                   "다른 토큰·숫자는 쓰지 않습니다.")
    validate = make_validator(fids, fids, facts)
    # 빈 비고는 '빈 문장'으로 걸리지 않게 빼고 검사한다.
    return ask(prompt, schema, VERSION["xxcs"], f"xxcs/{sec}", "xxcs" + sec, 900,
               lambda v: validate({k: v[k] for k in ("시험방법", "확인결과", "비고") if v[k] != ""}))


def main():
    fj = read_json(PRODUCT / "facts.json")
    lj = read_json(PRODUCT / "links.json")
    facts, reqs, links = fj["facts"], fj["reqs"], lj["links"]
    app = base_applicability(lj["rules"], fj["cyber"])
    jobs = {("mdsr", r): (gen_mdsr, (r, reqs, facts)) for r in ALL_REQS}
    jobs |= {("mddr", r): (gen_mddr, (r, reqs, facts)) for r in ALL_REQS}
    jobs |= {("xxcs", s): (gen_xxcs, (s, facts, reqs, links)) for s in LINK_SECS if app[s][0] == "적용"}
    jobs[("mdsr34", "3.4")] = (gen_mdsr34, (facts,))
    results = run_cached(jobs, RUNS / "content_cache")
    content = {
        "mdsr": {r: results[("mdsr", r)] for r in ALL_REQS},
        "mdsr34": results[("mdsr34", "3.4")]["문단"],
        "mddr": {r: results[("mddr", r)]["설계"] for r in ALL_REQS},
        "xxcs": {s: results[("xxcs", s)] for s in LINK_SECS if ("xxcs", s) in results},
        "applicability": {s: {"status": a, "rule": rid} for s, (a, rid) in app.items()},
    }
    write_json(PRODUCT / "content.json", content)
    print(f"wrote {PRODUCT / 'content.json'} ({len(results)} items)")


if __name__ == "__main__":
    main()
