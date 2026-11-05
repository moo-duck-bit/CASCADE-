"""모델 공통 채팅 클라이언트. backend = vllm | openai | anthropic, 같은 chat_json 인터페이스.

- JSON 스키마를 강제하고(vllm·openai: response_format json_schema, anthropic: output_config.format),
  형식이 틀리면 한 번만 다시 요청한다.
- seed·temperature·max_tokens·thinking을 백엔드 형식으로 넘긴다. anthropic은 2026-10-21부터 샘플링 파라미터가
  400이라 temperature를 보내지 않고, seed는 API에 없다(사용량에 seed_sent=False로 남긴다).
- 키는 환경변수(OPENAI_API_KEY, ANTHROPIC_API_KEY). 없으면 MissingKeyError(호출하는 쪽에서 건너뜀).
- 토큰과 금액(prices.yaml 단가표)을 호출마다 기록한다.
- 전송은 Transport 객체가 맡는다. MockTransport는 요청을 기록하고 정해 둔 응답을 돌려준다(테스트용).
MockLLM은 같은 인터페이스로 미리 정한 응답을 차례로 돌려준다(테스트용).
"""

import copy
import json
import os
import time
import urllib.request
from pathlib import Path

FORMAT_FIX = "출력을 지정한 JSON 형식으로 읽을 수 없습니다. 내용은 바꾸지 말고 형식만 고쳐서 JSON 하나만 다시 출력하세요."


class LLMFormatError(ValueError):
    pass


class MissingKeyError(RuntimeError):
    pass


BACKENDS = ("vllm", "openai", "anthropic")
KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
ENDPOINT = {"openai": "https://api.openai.com/v1/chat/completions",
            "anthropic": "https://api.anthropic.com/v1/messages"}
ANTHROPIC_VERSION = "2023-06-01"
PRICES_PATH = Path(__file__).resolve().parent / "prices.yaml"


def load_prices(path=PRICES_PATH):
    text = Path(path).read_text(encoding="utf-8")
    return json.loads("\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")))


def backend_available(backend):
    return backend == "vllm" or bool(os.environ.get(KEY_ENV.get(backend, ""), ""))


def strict_schema(schema):
    """openai strict·anthropic 구조화 출력용: 모든 객체에 additionalProperties=false, 모든 속성을 required로."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(s)
    return s


class HttpTransport:
    def __init__(self, timeout=1800):
        self.timeout = timeout

    def post(self, url, headers, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())


class MockTransport:
    """responses: 응답 dict 목록(차례로 소비) 또는 (url, headers, body) → dict 함수. 요청은 requests에 남는다."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def post(self, url, headers, body):
        self.requests.append((url, headers, copy.deepcopy(body)))
        return self.responses(url, headers, body) if callable(self.responses) else self.responses.pop(0)


PROMPTS_DIR = __import__("pathlib").Path(__file__).resolve().parent / "prompts"


def load_prompt(name, pack=None):
    """prompts/<name>.txt → (system, user 틀). 파일은 [system] / [user] 두 부분으로 나뉜다.

    system의 {domain}은 도메인 팩의 문서 세트 설명으로 바꾼다."""
    from . import pack as pack_mod
    text = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    system, user = text.split("[user]", 1)
    system = system.replace("[system]", "", 1).strip().replace("{domain}", pack_mod.load(pack).prompt_domain)
    return system, user.strip()


def validate(value, schema, path="$"):
    """작은 JSON 스키마 검사(type, required, properties, items, enum)."""
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t and not isinstance(value, types[t]):
        raise LLMFormatError(f"{path}: expected {t}")
    if t in ("integer", "number") and isinstance(value, bool):
        raise LLMFormatError(f"{path}: expected {t}")
    if "enum" in schema and value not in schema["enum"]:
        raise LLMFormatError(f"{path}: {value!r} not in enum")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                raise LLMFormatError(f"{path}: missing {k}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                validate(value[k], sub, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            validate(v, schema["items"], f"{path}[{i}]")


def _parse(text, schema):
    try:
        obj = json.loads(text) if isinstance(text, str) else text
    except json.JSONDecodeError as err:
        raise LLMFormatError(str(err)) from err
    validate(obj, schema)
    return obj


class _Base:
    def cost(self, tin, tout):
        return 0.0

    def chat_json(self, messages, schema, name):
        usage = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0.0, "format_retry": False,
                 "truncated": False, "cost_usd": 0.0}
        msgs = list(messages)
        last_err = None
        for attempt in range(2):
            t0 = time.time()
            text, tin, tout, finish = self._complete(msgs, schema, name)
            usage["calls"] += 1
            usage["tokens_in"] += tin
            usage["tokens_out"] += tout
            usage["seconds"] += time.time() - t0
            usage["truncated"] = usage["truncated"] or finish == "length"
            usage["cost_usd"] += self.cost(tin, tout)
            try:
                return _parse(text, schema), usage
            except LLMFormatError as err:
                last_err = err
                usage["format_retry"] = True
                msgs = msgs + [{"role": "assistant", "content": text if isinstance(text, str) else json.dumps(text)},
                               {"role": "user", "content": FORMAT_FIX}]
        raise LLMFormatError(f"{name}: {last_err}")


class LLMClient(_Base):
    def __init__(self, base_url, model, temperature=0.0, seed=0, max_tokens=4096, timeout=1800, backend="vllm",
                 thinking=False, transport=None, api_key=None, prices=None):
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}")
        self.backend = backend
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.timeout = timeout
        self.transport = transport or HttpTransport(timeout)
        self.api_key = api_key or os.environ.get(KEY_ENV.get(backend, ""), "")
        if backend != "vllm" and not self.api_key:
            raise MissingKeyError(f"{KEY_ENV[backend]} not set: {backend} backend skipped")
        table = (prices or load_prices()).get(backend, {})
        self.price = table.get(model, table.get("*", [0.0, 0.0]))

    def cost(self, tin, tout):
        return tin * self.price[0] / 1e6 + tout * self.price[1] / 1e6

    # 요청 본문

    def request_body(self, messages, schema, name):
        if self.backend == "anthropic":
            return self._anthropic_body(messages, schema)
        if self.backend == "openai":
            body = {"model": self.model, "messages": messages, "temperature": self.temperature, "seed": self.seed,
                    "max_completion_tokens": self.max_tokens,
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": name, "schema": strict_schema(schema),
                                                        "strict": True}}}
            if self.thinking:
                body["reasoning_effort"] = "medium"
            return body
        return {"model": self.model, "messages": messages, "temperature": self.temperature, "seed": self.seed,
                "max_tokens": self.max_tokens, "chat_template_kwargs": {"enable_thinking": bool(self.thinking)},
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": name, "schema": schema, "strict": True}}}

    def _anthropic_body(self, messages, schema):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [m for m in messages if m["role"] != "system"],
                "output_config": {"format": {"type": "json_schema", "schema": strict_schema(schema)}}}
        if system:
            body["system"] = system
        if self.thinking:
            body["thinking"] = {"type": "adaptive"}
        elif self.model.startswith("claude-opus-5-5") or self.model.startswith("claude-fable"):
            body["output_config"]["effort"] = "low"  # 생각을 끌 수 없는 모델: 가장 낮은 effort로
        elif self.model.startswith("claude-sonnet-5-5"):
            body["thinking"] = {"type": "between_tools"}
        else:
            body["thinking"] = {"type": "disabled"}
        return body

    def _request(self, messages, schema, name):
        body = self.request_body(messages, schema, name)
        if self.backend == "anthropic":
            headers = {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION}
            return ENDPOINT["anthropic"], headers, body
        if self.backend == "openai":
            return ENDPOINT["openai"], {"Authorization": f"Bearer {self.api_key}"}, body
        return f"{self.base_url}/chat/completions", {}, body

    def _complete(self, messages, schema, name):
        url, headers, body = self._request(messages, schema, name)
        out = self.transport.post(url, headers, body)
        if self.backend == "anthropic":
            if out.get("stop_reason") == "refusal":
                raise LLMFormatError(f"refusal: {out.get('stop_details')}")
            text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
            u = out.get("usage") or {}
            tin = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + \
                u.get("cache_creation_input_tokens", 0)
            finish = "length" if out.get("stop_reason") == "max_tokens" else out.get("stop_reason")
            return text, tin, u.get("output_tokens", 0), finish
        choice = out["choices"][0]
        u = out.get("usage") or {}
        return choice["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0), \
            choice.get("finish_reason")


class MockLLM(_Base):
    """responses: 응답 목록(차례로 소비) 또는 (messages, name) → 응답 함수."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.model = "mock"

    def _complete(self, messages, schema, name):
        self.calls.append({"name": name, "messages": messages})
        if callable(self.responses):
            r = self.responses(messages, name)
        else:
            r = self.responses.pop(0)
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return text, sum(len(m["content"]) for m in messages) // 2, len(text) // 2, "stop"
