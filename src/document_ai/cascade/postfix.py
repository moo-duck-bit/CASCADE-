"""값을 바꾼 뒤의 표기 후처리: 단위 중복("5회 회") 제거, 값 바로 뒤 조사(을/를, 이/가, 은/는, 과/와, 으로/로) 보정."""

import re

from .values import UNITS, mentions

_units = sorted({u for u, _ in UNITS if not u.isascii() or u == "%"}, key=len, reverse=True)
_unit_alt = "|".join(re.escape(u) for u in _units)
# 두 번째 단위 뒤가 낱말의 일부(회의, 회기, 분석, 일정 …)면 단위가 아니다
DUP_RE = re.compile(rf"(\d+(?:\.\d+)?\s?(?:{_unit_alt}))\s?(?:{_unit_alt})(?![의기석야동정호원리간수])")

PAIRS = {"을": ("을", "를"), "를": ("을", "를"), "이": ("이", "가"), "가": ("이", "가"),
         "은": ("은", "는"), "는": ("은", "는"), "과": ("과", "와"), "와": ("과", "와")}
PARTICLE_RE = re.compile(r"^(으로|로|을|를|이|가|은|는|과|와)(?![가-힣])")
DIGIT_BATCHIM = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True,
                 "8": True, "9": False}
DIGIT_RIEUL = {"1", "7", "8"}  # 일·칠·팔: ㄹ 받침(으로 대신 로)
LATIN_BATCHIM = set("LMNR")
TOKEN_RE = re.compile(r"[A-Za-z]+[\s\-]?\d+(?:\.\d+)*|\d+(?:\.\d+)*")


def dedupe_units(text):
    return DUP_RE.sub(lambda m: m.group(1), text)


def _batchim(ch):
    """(받침 있음, ㄹ 받침)."""
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return jong != 0, jong == 8
    if ch.isdigit():
        return DIGIT_BATCHIM[ch], ch in DIGIT_RIEUL
    if ch.isalpha():
        return ch.upper() in LATIN_BATCHIM, ch.upper() in ("L", "R")
    return None, None


def _fix_at(text, end):
    m = PARTICLE_RE.match(text[end:])
    if not m:
        return text
    has, rieul = _batchim(text[end - 1])
    if has is None:
        return text
    p = m.group(1)
    if p in ("으로", "로"):
        new = "으로" if has and not rieul else "로"
    else:
        new = PAIRS[p][0] if has else PAIRS[p][1]
    return text[:end] + new + text[end + len(p):]


def fix_particles(text):
    """값(수치+단위, 숫자 토큰) 바로 뒤에 붙은 조사만 받침에 맞춘다. 다른 낱말은 건드리지 않는다."""
    ends = sorted({m.end for m in mentions(text)} | {m.end() for m in TOKEN_RE.finditer(text)}, reverse=True)
    for end in ends:
        text = _fix_at(text, end)
    return text


def postfix(text):
    text = " ".join(text.split())
    return fix_particles(dedupe_units(text))
