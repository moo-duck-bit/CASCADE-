"""정답 분리 가드. CASCADE는 케이스 input/ 폴더, 패키지 안 고정 자료(프롬프트, 규칙 표), 공개 규정 코퍼스(pilot_data/corpus/)만 읽는다.

두 겹으로 막는다.
- ReadGuard.read_text / read_bytes / check: CASCADE 코드가 파일을 읽을 때 거치는 길.
- active(): 실행 중 감사 훅(sys.addaudithook)으로 모든 open을 검사한다. 라이브러리가 몰래 여는 파일도 걸린다.
금지 목록이 허용 목록보다 우선한다. 읽은 파일과 위반은 실행마다 로그로 남긴다.
"""

import fnmatch
import os
import sys
import sysconfig
import threading
from contextlib import contextmanager
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# pilot_data 기준 경로 패턴. 정리 전 이름(gen/, verifier/, docs/gold_spec.md)과 지금 이름을 함께 막는다.
DENY = (
    "*/gold/*", "*/gold",
    "*/cases/*/meta.json", "*/cases/index.json",
    "*/docs/sites.json",
    "*/product/*.json",
    "*/pilot_data/gen/*", "*/pilot_data/lib/*", "*/pilot_data/v02/build/*", "*/pilot_data/v03/build/*",
    "*/pilot_data/verifier/*", "*/pilot_data/experiments/verifier.py",
    "*/pilot_data/v03/docs/gold_spec.md", "*/pilot_data/v03/spec.md",
    "*/pilot_data/results/*", "*/pilot_data/scoring/*", "*/pilot_data/annotations/*",
    "*/pilot_data/v02/runs/*", "*/pilot_data/v03/runs/*",
    "*/products/*/product/*", "*/products/*/product",
)


def default_corpus_dir():
    """공개 규정 코퍼스 위치. 환경변수 CASCADE_CORPUS_DIR가 있으면 그것, 없으면 작업 공간의 pilot_data/corpus."""
    env = os.environ.get("CASCADE_CORPUS_DIR")
    return Path(env).resolve() if env else PACKAGE_DIR.parents[3] / "pilot_data" / "corpus"


class GuardViolation(PermissionError):
    pass


def _system_roots():
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix}
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = sysconfig.get_paths().get(key)
        if p:
            roots.add(p)
    roots.add(str(PACKAGE_DIR.parent))  # document_ai 패키지(코드 import)
    return [Path(r).resolve() for r in roots]


_local = threading.local()
_hook_installed = False
_hook_lock = threading.Lock()


def _audit(event, args):
    if event != "open":
        return
    guard = getattr(_local, "guard", None)
    if guard is None:
        return
    path, mode = args[0], args[1] if len(args) > 1 else "r"
    if isinstance(path, int):
        return
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    writing = isinstance(mode, str) and any(ch in mode for ch in "wax+")
    guard._audit_open(Path(path), writing)


def _install_hook():
    global _hook_installed
    with _hook_lock:
        if not _hook_installed:
            sys.addaudithook(_audit)
            _hook_installed = True


class ReadGuard:
    def __init__(self, allowed, write_dirs=(), corpus_dir=None):
        self.allowed = [Path(p).resolve() for p in allowed] + [PACKAGE_DIR]
        if corpus_dir is not None:
            self.allowed.append(Path(corpus_dir).resolve())
        self.write_dirs = [Path(p).resolve() for p in write_dirs]
        self._system = _system_roots()
        self._reads = []
        self._violations = []

    @staticmethod
    def denied(path):
        s = str(Path(os.path.abspath(path))).replace(os.sep, "/")
        return any(fnmatch.fnmatch(s, pat) for pat in DENY)

    def _under(self, path, roots):
        return any(path == r or r in path.parents for r in roots)

    def check(self, path, writing=False):
        p = Path(os.path.abspath(path)).resolve()
        if self.denied(p):
            self._violations.append(str(p))
            raise GuardViolation(f"denied by answer-isolation guard: {p}")
        if writing:
            if not self._under(p, self.write_dirs):
                self._violations.append(str(p))
                raise GuardViolation(f"write outside work dir: {p}")
            return p
        if self._under(p, self.allowed) or self._under(p, self.write_dirs):
            # 코드 import(.py/.pyc)는 빼고 자료 파일만 기록한다
            if p.suffix not in (".py", ".pyc") and str(p) not in self._reads:
                self._reads.append(str(p))
            return p
        if self._under(p, self._system):
            return p
        self._violations.append(str(p))
        raise GuardViolation(f"not in read allowlist: {p}")

    def _audit_open(self, path, writing):
        _local.guard = None  # 검사 중 다시 들어오지 않게
        try:
            self.check(path, writing)
        finally:
            _local.guard = self

    def read_text(self, path, encoding="utf-8"):
        p = self.check(path)
        return p.read_text(encoding=encoding)

    def read_bytes(self, path):
        p = self.check(path)
        return p.read_bytes()

    @contextmanager
    def active(self):
        _install_hook()
        prev = getattr(_local, "guard", None)
        _local.guard = self
        try:
            yield self
        finally:
            _local.guard = prev

    def files_read(self):
        return list(self._reads)

    def violations(self):
        return list(self._violations)
