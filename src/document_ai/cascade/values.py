"""수치+단위 정규화. "15분" "15 분" "십오 분" "15 min"을 같은 값(15|min)으로 본다.

- 한자어 수사(일~구, 십·백·천 조합)와 고유어 수사(한·두·세·네 … 스물), 시각(22:00, 오후 10시), 백분율을 읽는다.
- 시간 길이 단위(초~년) 사이 환산과 '매일'·'매시간' 같은 주기 낱말은 같은 값으로 치지 않고 '환산 일치'로 따로 표시한다.
- 숫자 없는 값(TLS 1.2 이상, AES-256)은 공백·기호를 지운 느슨한 비교(loose)로 찾는다.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

# 단위 사전은 도메인 팩 설정(domain_packs/<팩>/pack.yaml의 units)에 있다. 기본은 EC-SW 팩.
DEFAULT_PACK_FILE = Path(__file__).resolve().parent / "domain_packs" / "ec_sw" / "pack.yaml"


def read_units(path=DEFAULT_PACK_FILE):
    text = Path(path).read_text(encoding="utf-8")
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return [tuple(u) for u in json.loads(body)["units"]]


# 환산: 단위 → (기준 단위, 배수). 시간 길이는 모두 초로 맞춘다(24시간 = 1일).
CONVERT = {"sec": ("sec", 1), "min": ("sec", 60), "hour": ("sec", 3600), "day": ("sec", 86400),
           "week": ("sec", 7 * 86400), "month": ("sec", 30 * 86400), "year": ("sec", 365 * 86400)}
# '매일'처럼 숫자 없이 주기 하나를 뜻하는 말
EVERY = {"매일": "day", "매시간": "hour", "매주": "week", "매월": "month", "매달": "month", "매년": "year", "매분": "min"}
EVERY_RE = re.compile(r"(?<![가-힣])(매시간|매일|매주|매월|매달|매년|매분)")
# 고유어 기간 낱말
SPAN = {"하루": (1, "day"), "이틀": (2, "day"), "사흘": (3, "day"), "나흘": (4, "day"), "보름": (15, "day"),
        "일주일": (1, "week")}
SPAN_RE = re.compile(r"(?<![가-힣])(하루|이틀|사흘|나흘|보름|일주일)")

SINO_DIGIT = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
SINO_UNIT = {"십": 10, "백": 100, "천": 1000}
NATIVE = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
          "열": 10, "스무": 20, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50}
NATIVE_UNITS = ("count", "hour", "char", "kind", "item", "person", "case", "month")

_sino = "[일이삼사오육칠팔구십백천]+"
_native = "(?:열|스물|스무|서른|마흔|쉰)?(?:다섯|여섯|일곱|여덟|아홉|한|두|세|네)|열|스물|스무|서른|마흔|쉰"
CLOCK_RE = re.compile(r"(?<!\d)(?:(오전|오후)\s?)?(\d{1,2})(?::(\d{2})|\s?시(?:\s?(\d{1,2})\s?분)?)(?![\d간])")


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    surface: str
    number: float
    unit: str

    @property
    def key(self):
        if self.unit == "clock":
            h, m = divmod(int(round(self.number)), 60)
            return f"{h:02d}:{m:02d}|clock"
        return f"{_fmt(self.number)}|{self.unit}"

    @property
    def conv_key(self):
        if self.unit in CONVERT:
            base, mul = CONVERT[self.unit]
            return f"{_fmt(self.number * mul)}|{base}"
        return self.key


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def sino_value(word):
    total, cur = 0, 0
    for ch in word:
        if ch in SINO_DIGIT:
            cur = SINO_DIGIT[ch]
        elif ch in SINO_UNIT:
            total += (cur or 1) * SINO_UNIT[ch]
            cur = 0
        else:
            return None
    return total + cur


def native_value(word):
    if word in NATIVE:
        return NATIVE[word]
    for tens in ("열", "스물", "스무", "서른", "마흔", "쉰"):
        if word.startswith(tens) and word[len(tens):] in NATIVE:
            return NATIVE[tens] + NATIVE[word[len(tens):]]
    return None


class Normalizer:
    """단위 사전 하나에 묶인 값 읽기. 도메인 팩마다 하나씩 만든다."""

    def __init__(self, units):
        self.units = [tuple(u) for u in units]
        self.unit_of = dict(self.units)
        alt = "|".join(re.escape(u) for u, _ in sorted(self.units, key=lambda x: -len(x[0])))
        self.digit_re = re.compile(rf"(?<![\w.\-])(\d+(?:\.\d+)?)\s?({alt})(?![A-Za-z])")
        self.word_re = re.compile(rf"(?<![가-힣\w])({_sino}|{_native})\s?({alt})(?![A-Za-z])")

    def mentions(self, text):
        out, taken = [], []

        def free(s, e):
            return all(e <= a or s >= b for a, b in taken)

        for m in CLOCK_RE.finditer(text):
            ampm, h, mm, mm2 = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            is_colon = mm is not None
            if not is_colon and not ampm and not (m.group(0).rstrip().endswith("시") or mm2):
                continue
            if h > 24:
                continue
            if ampm == "오후" and h < 12:
                h += 12
            minute = int(mm or mm2 or 0)
            out.append(Mention(m.start(), m.end(), m.group(0), h * 60 + minute, "clock"))
            taken.append((m.start(), m.end()))
        for m in self.digit_re.finditer(text):
            if free(m.start(), m.end()):
                out.append(Mention(m.start(), m.end(), m.group(0), float(m.group(1)), self.unit_of[m.group(2)]))
                taken.append((m.start(), m.end()))
        for m in self.word_re.finditer(text):
            if not free(m.start(), m.end()):
                continue
            word, unit = m.group(1), self.unit_of[m.group(2)]
            v = native_value(word)
            if v is not None and unit not in NATIVE_UNITS:
                v = None
            if v is None:
                # 한 글자 한자어 수사는 '이 기능'처럼 흔한 말과 겹친다: 십·백과 띄어 쓴 단위만 받는다
                if len(word) == 1 and word not in ("십", "백") and m.group(0)[1:2] != " ":
                    continue
                if word == "이" or (len(word) == 1 and word in "일사"):
                    continue
                v = sino_value(word)
            if v:
                out.append(Mention(m.start(), m.end(), m.group(0), float(v), unit))
                taken.append((m.start(), m.end()))
        for m in EVERY_RE.finditer(text):
            if free(m.start(), m.end()):
                out.append(Mention(m.start(), m.end(), m.group(0), 1.0, EVERY[m.group(1)]))
                taken.append((m.start(), m.end()))
        for m in SPAN_RE.finditer(text):
            if free(m.start(), m.end()):
                n, unit = SPAN[m.group(1)]
                out.append(Mention(m.start(), m.end(), m.group(0), float(n), unit))
                taken.append((m.start(), m.end()))
        return sorted(out, key=lambda x: x.start)

    def key_of(self, surface):
        ms = self.mentions(surface.strip())
        return ms[0].key if len(ms) == 1 else None

    def conv_key_of(self, surface):
        ms = self.mentions(surface.strip())
        return ms[0].conv_key if len(ms) == 1 else None

    def same_value(self, a, b):
        ka, kb = self.key_of(a), self.key_of(b)
        return ka is not None and ka == kb

    def converted_equal(self, a, b):
        ka, kb = self.conv_key_of(a), self.conv_key_of(b)
        return ka is not None and ka == kb

    def is_numeric(self, surface):
        return self.key_of(surface) is not None


DEFAULT = Normalizer(read_units())
UNITS = DEFAULT.units
mentions = DEFAULT.mentions
key_of = DEFAULT.key_of
conv_key_of = DEFAULT.conv_key_of
same_value = DEFAULT.same_value
converted_equal = DEFAULT.converted_equal
is_numeric = DEFAULT.is_numeric


# 숫자 없는 값

LOOSE_DROP = re.compile(r"[\s\-_+·,/()'\"“”‘’.]")
TAIL_WORDS = ("포함", "이상", "이하", "방식", "사용", "적용", "으로", "로")


VERSION_RE = re.compile(r"(?:\bv(?=\d)|버전\s?(?=\d))", re.I)


def loose(text):
    """기호·공백·버전 표기(v1.2, 버전 1.2)를 지운 비교용 글."""
    return LOOSE_DROP.sub("", VERSION_RE.sub("", text.lower()))


def loose_core(value):
    """비교에 쓸 핵심: 기호·공백을 지우고 끝의 일반 낱말(포함, 이상, 방식 …)을 뗀다."""
    v = loose(value)
    changed = True
    while changed:
        changed = False
        for w in TAIL_WORDS:
            if v.endswith(w) and len(v) > len(w) + 2:
                v = v[: -len(w)]
                changed = True
    return v


def loose_contains(text, value):
    core = loose_core(value)
    return bool(core) and core in loose(text)
