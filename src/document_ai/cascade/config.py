"""구성요소 on/off 플래그와 하이퍼파라미터. 제거 실험 프리셋도 여기서 만든다."""

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

CHECKS = ("V1", "V2", "V3", "V4", "V5", "V6", "V2b", "V3b", "V6b")
MODES = ("full", "ledger_like", "fake_feedback")
RETRIEVERS = ("bm25", "dense", "hybrid")
PRESETS = ("full", "no_verifier", "no_relevance", "no_value_index", "no_rules", "fake_feedback", "ledger_like",
           "no_retrieval")
# 프리셋의 공통 바탕: 개발셋 full 구성(P3~P6: 편집 연산, 규정 검색 hybrid k=10)
BASE = {"retrieval": True, "retriever": "hybrid", "retrieval_k": 10}


def _all_checks():
    return {k: True for k in CHECKS}


@dataclass(frozen=True)
class CascadeConfig:
    relevance: bool = True        # Locator 관련성 판단(LLM). 끄면 후보 전부를 관련으로 본다
    value_index: bool = True      # 정규화 값 색인. 끄면 옛 값 표기와 글자가 같은 칸만 찾는다
    rules: bool = True            # 첨부3 규칙 표 재계산. 끄면 T2 대상 항목을 LLM이 고른다
    verifier: bool = True         # 결정론 검사와 재수정 고리
    retrieval: bool = False       # 고정 코퍼스 규정 검색(P6)
    retriever: str = "hybrid"     # bm25 | dense | hybrid
    retrieval_k: int = 10
    embed_url: str = "http://127.0.0.1:8101/embed"  # 밀집 검색 질의 임베딩 서버(BGE-M3)
    verifier_checks: dict = field(default_factory=_all_checks)
    loop_k: int = 2               # 재수정 최대 횟수
    mode: str = "full"
    edit_mode: str = "ops"        # ops: 편집 연산 호출 목록 | free_json: 칸마다 글(dev1 방식)
    request_mode: str = "structured"  # structured: 구조화 문형 파서 | natural: 자연어 요청(request_nl.txt) → LLM 의도 파서
    rules_source: str = "curated"  # curated: rules_table v2 | extracted: 자동 추출 규칙 | retrieval_only: 규칙 없이 검색 조항만
    graph_mode: str = "pack"      # pack: 팩의 연결 설정 | generic: 문서에서 추론(도메인층 없음) | generic_rules: 추론 + 규칙·검색
    link_threshold: float = 0.6   # 연결 열 자동 감지 기준(묶음 안 항목 번호 칸 비율)
    sim_threshold: float = 0.7    # 유사도 엣지 기준(BGE-M3 코사인)
    domain_pack: str = "ec_sw"    # 도메인 팩 이름(domain_packs/<이름>/pack.yaml) 또는 pack.yaml 경로
    backend: str = "vllm"         # vllm | openai | anthropic
    thinking: bool = False        # 모델의 생각(추론) 모드
    model: str = "qwen3-32b"
    base_url: str = "http://127.0.0.1:8100/v1"
    temperature: float = 0.0
    seed: int = 20261103
    max_tokens: int = 4096
    token_budget: int = 60000     # 케이스당 입력+출력 토큰 상한(넘으면 고리를 멈춘다)
    ngram_threshold: float = 0.7  # ledger_like 텍스트 유사도 기준
    context_chars: int = 400      # Editor 맥락에 넣는 주변 칸 한 칸당 최대 글자 수
    auto_approve: bool = True

    def __hash__(self):
        return hash(self.hash())

    def to_dict(self):
        return dataclasses.asdict(self)

    def hash(self):
        blob = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def check_on(self, name):
        return self.verifier and self.verifier_checks.get(name, False)


def from_dict(d):
    names = {f.name for f in dataclasses.fields(CascadeConfig)}
    unknown = set(d) - names
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    d = dict(d)
    if "verifier_checks" in d:
        bad = set(d["verifier_checks"]) - set(CHECKS)
        if bad:
            raise ValueError(f"unknown verifier checks: {sorted(bad)}")
        d["verifier_checks"] = {**_all_checks(), **d["verifier_checks"]}
    if d.get("mode", "full") not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if d.get("request_mode", "structured") not in ("structured", "natural"):
        raise ValueError("request_mode must be structured or natural")
    if d.get("rules_source", "curated") not in ("curated", "extracted", "retrieval_only"):
        raise ValueError("rules_source must be curated, extracted or retrieval_only")
    if d.get("graph_mode", "pack") not in ("pack", "generic", "generic_rules"):
        raise ValueError("graph_mode must be pack, generic or generic_rules")
    if d.get("edit_mode", "ops") not in ("ops", "free_json"):
        raise ValueError("edit_mode must be ops or free_json")
    if d.get("backend", "vllm") not in ("vllm", "openai", "anthropic"):
        raise ValueError("backend must be one of vllm, openai, anthropic")
    if d.get("retriever", "hybrid") not in RETRIEVERS:
        raise ValueError(f"retriever must be one of {RETRIEVERS}")
    if int(d.get("retrieval_k", 10)) < 1:
        raise ValueError("retrieval_k must be >= 1")
    if int(d.get("loop_k", 2)) < 0:
        raise ValueError("loop_k must be >= 0")
    return CascadeConfig(**d)


def load(path):
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def preset(name, **overrides):
    if name not in PRESETS:
        raise ValueError(f"preset must be one of {PRESETS}")
    d = {
        "full": {},
        "no_verifier": {"verifier": False},
        "no_relevance": {"relevance": False},
        "no_value_index": {"value_index": False},
        "no_rules": {"rules": False},
        "fake_feedback": {"mode": "fake_feedback"},
        "ledger_like": {"mode": "ledger_like", "loop_k": 0},
        "no_retrieval": {"retrieval": False},
    }[name]
    return from_dict({**BASE, **d, **overrides})
