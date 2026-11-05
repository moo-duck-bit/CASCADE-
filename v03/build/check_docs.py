"""v0.3 렌더 문서 검사. v0.2 검사에 더해 MDSR 기준 칸이 모든 Req에서 2문단인지 본다. 출력: v03/runs/<P>/check_docs.json"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import doc_check
from common import ALL_REQS, DOCS, PRODUCT, RUNS

if __name__ == "__main__":
    sys.exit(doc_check.main(PRODUCT, DOCS, RUNS / "check_docs.json", two_criteria_reqs=ALL_REQS))
