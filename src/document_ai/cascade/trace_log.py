"""케이스 하나의 실행 기록: 단계별 수치, LLM 호출, 라운드별 지적 수, 읽은 파일, 가드 위반."""

import json
import time
from pathlib import Path


class TraceLog:
    def __init__(self, case_id, config_hash=""):
        self.case_id = case_id
        self.config_hash = config_hash
        self.t0 = time.time()
        self.stages = {}
        self.calls = []
        self.rounds = []
        self.files_read = []
        self.violations = []
        self.extra = {}
        self.part = None      # 변경이 여러 개인 요청에서 지금 처리 중인 변경 번호(라운드 기록에 붙는다)

    def stage(self, name, **fields):
        self.stages.setdefault(name, {}).update(fields)

    def llm(self, name, tokens_in=0, tokens_out=0, seconds=0.0, calls=1, **fields):
        self.calls.append({"name": name, "calls": calls, "tokens_in": tokens_in, "tokens_out": tokens_out,
                           "seconds": round(seconds, 3), **fields})

    def llm_usage(self, name, usage):
        self.llm(name, usage["tokens_in"], usage["tokens_out"], usage["seconds"], usage["calls"],
                 format_retry=usage.get("format_retry", False), truncated=usage.get("truncated", False),
                 cost_usd=usage.get("cost_usd", 0.0))

    def round(self, k, **fields):
        rec = {"round": k, **fields}
        if self.part is not None:
            rec["part"] = self.part
        self.rounds.append(rec)

    def files(self, paths, violations=()):
        self.files_read = list(paths)
        self.violations = list(violations)

    @property
    def tokens_in(self):
        return sum(c["tokens_in"] for c in self.calls)

    @property
    def tokens_out(self):
        return sum(c["tokens_out"] for c in self.calls)

    @property
    def cost_usd(self):
        return sum(c.get("cost_usd", 0.0) for c in self.calls)

    @property
    def n_calls(self):
        return sum(c["calls"] for c in self.calls)

    def to_dict(self):
        return {"case_id": self.case_id, "config_hash": self.config_hash, "stages": self.stages,
                "llm_calls": self.n_calls, "calls": self.calls, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "cost_usd": round(self.cost_usd, 6), "rounds": self.rounds, "files_read": self.files_read,
                "violations": self.violations, "seconds": round(time.time() - self.t0, 3), **self.extra}

    def write(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return path
