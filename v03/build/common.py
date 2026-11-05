"""v0.3 경로와 제품 설정. 제품은 환경변수 PILOT_PRODUCT(기본 P1)로 고르고 설정은 products/<P>/profile.json에서 읽는다.

진입점이 lib를 sys.path에 넣은 뒤 import 한다.
"""

import os
from pathlib import Path

import llm
from datagen import read_json

V03 = Path(__file__).resolve().parents[1]
PRODUCT_ID = os.getenv("PILOT_PRODUCT", "P1")
PROD = V03 / "products" / PRODUCT_ID
PRODUCT = PROD / "product"
DOCS = PROD / "docs"
SHEETS = PROD / "sheets"
RUNS = V03 / "runs" / PRODUCT_ID

llm.LOG_PATH = V03 / "runs" / "gen_log.jsonl"

PROFILE = read_json(PROD / "profile.json")

SEED = 20261102

APP_REQS = [f"Req. {n}" for n in range(PROFILE["app_reqs"][0], PROFILE["app_reqs"][1] + 1)]
SERVER_REQS = [f"Req. {n}" for n in range(PROFILE["server_reqs"][0], PROFILE["server_reqs"][1] + 1)]
ALL_REQS = APP_REQS + SERVER_REQS

CYBER_BASE = PROFILE["cyber"]
RULES = PROFILE["rules"]
