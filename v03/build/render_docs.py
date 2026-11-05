"""v0.3 제품 문서 3종을 렌더한다. 출력: v03/products/<P>/docs/*.docx, sites.json, site_index.md"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import render
from common import APP_REQS, DOCS, PRODUCT, PROFILE, SERVER_REQS

if __name__ == "__main__":
    render.main(PRODUCT, DOCS, APP_REQS, SERVER_REQS, PROFILE)
