"""OpenAI 호환(vLLM) 서버에 JSON schema를 강제한 채팅 요청을 보낸다.

Qwen3 thinking은 끄고, 파싱·스키마 검사에 실패하면 시드를 1씩 올려 다시 묻는다. 모든 호출은 LOG_PATH에 한 줄씩 남긴다.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

LOG_PATH = None  # 호출하는 쪽에서 정한다
BASE_URL = os.getenv("PILOT_LLM_BASE_URL", "http://127.0.0.1:8100/v1")
MODEL = os.getenv("PILOT_LLM_MODEL", "qwen3-32b")
_LOG_LOCK = threading.Lock()

JSON_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
# xgrammar가 지원하지 않는 제약. 서버에는 빼고 보내고 check_schema가 로컬에서 검사한다.
SERVER_DROP = {"minLength", "maxLength", "minItems", "maxItems", "pattern", "minimum", "maximum", "format"}


class LLMError(RuntimeError):
    pass


def _post(path, body, timeout):
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _log(record):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK, LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def check_schema(value, schema, path="$"):
    """필요한 만큼만 구현한 JSON schema 검사(type, enum, required, properties, items, 길이·개수, pattern)."""
    errors = []
    t = schema.get("type")
    if (t and not isinstance(value, JSON_TYPES[t])) or (t in ("integer", "number") and isinstance(value, bool)):
        return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if t == "object":
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errors += check_schema(value[key], sub, f"{path}.{key}")
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            for key, v in value.items():
                if key not in props:
                    errors += check_schema(v, extra, f"{path}.{key}")
    if t == "array":
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errors += check_schema(v, schema["items"], f"{path}[{i}]")
    if t == "string" and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: shorter than {schema['minLength']}")
    if t == "string" and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path}: does not match {schema['pattern']}")
    return errors


def server_schema(schema):
    """SERVER_DROP 제약을 뺀 사본."""
    if isinstance(schema, dict):
        return {k: server_schema(v) for k, v in schema.items() if k not in SERVER_DROP}
    if isinstance(schema, list):
        return [server_schema(v) for v in schema]
    return schema


def chat_json(messages, schema, *, prompt_version, task, temperature=0.2, seed=0, max_tokens=2048, retries=3,
              validate=None, timeout=600.0, meta=None):
    """schema를 강제한 JSON 응답을 돌려준다. retries번까지 다시 묻고, 끝내 실패하면 LLMError."""
    last_err = ""
    convo = list(messages)
    json_schema = {"name": task.replace("/", "_")[:60], "schema": server_schema(schema), "strict": True}
    for attempt in range(retries + 1):
        attempt_seed = seed + attempt
        body = {
            "model": MODEL,
            "messages": convo,
            "temperature": temperature,
            "seed": attempt_seed,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": json_schema},
        }
        started = time.perf_counter()
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task": task,
            "prompt_version": prompt_version,
            "attempt": attempt,
            "seed": attempt_seed,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **(meta or {}),
        }
        try:
            raw = _post("/chat/completions", body, timeout)
        except (urllib.error.URLError, TimeoutError) as err:
            if isinstance(err, urllib.error.HTTPError):
                detail = err.read().decode("utf-8", "replace")[:300]
            else:
                detail = str(err)
            record.update(ok=False, error=f"http: {detail}", seconds=round(time.perf_counter() - started, 3))
            _log(record)
            last_err = record["error"]
            time.sleep(2 ** attempt)
            continue
        seconds = time.perf_counter() - started
        usage = raw.get("usage") or {}
        choice = raw["choices"][0]
        text = choice["message"].get("content") or ""
        record.update(
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            seconds=round(seconds, 3),
            finish_reason=choice.get("finish_reason"),
            served_model=raw.get("model"),
        )
        try:
            value = json.loads(text)
            errors = check_schema(value, schema)
            if not errors and validate:
                errors = validate(value)
            if errors:
                raise ValueError("; ".join(errors[:5]))
        except ValueError as err:
            record.update(ok=False, error=f"parse/validate: {str(err)[:300]}", output_preview=text[:200])
            _log(record)
            last_err = record["error"]
            # 다음 시도에는 이전 답과 오류를 붙여 고치게 한다.
            convo = list(messages) + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"다음 문제를 고쳐 같은 JSON 형식으로 다시 출력하세요: {str(err)[:500]}"},
            ]
            continue
        record.update(ok=True)
        _log(record)
        return value
    raise LLMError(f"{task}: failed after {retries + 1} attempts: {last_err}")


def server_info():
    with urllib.request.urlopen(f"{BASE_URL}/models", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))
