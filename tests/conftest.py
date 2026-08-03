"""테스트 공통 준비 — 모든 테스트가 실제 저장소 대신 임시 DB를 보게 한다.

파일마다 fixture를 두면 테스트를 새로 추가할 때 빠뜨리기 쉽다. 실제로 4주차 e2e에 이 설정이
없어서 공유 저장소에 테스트 row가 45건 쌓였고, 이후 실제 대화의 조회 결과까지 오염시켰다.
그래서 테스트 모듈이 import되기 전에 여기서 한 번만 경로를 바꾼다.

- 외부 공유 저장소: MCP 서버가 별도 프로세스로 뜨고 호출 시점에 os.environ을 읽으므로 환경변수로 넘긴다.
- 앱 SQLite: 환경변수 훅이 없어서, 학생 코드가 CONFIG를 읽기 전에 경로만 바꿔 끼운다.

임시 폴더는 세션이 끝나면 통째로 지운다. 개별 row를 지우는 정리가 빠져도 실제 데이터에 남지 않는다.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="kanana_test_"))
EXTERNAL_DB_PATH = TEST_DATA_DIR / "external_people.sqlite3"
APP_DB_PATH = TEST_DATA_DIR / "app.sqlite3"

os.environ["KANANA_EXTERNAL_DB_PATH"] = str(EXTERNAL_DB_PATH)

import fixed.config as _config  # noqa: E402

_config.CONFIG = dataclasses.replace(
    _config.CONFIG,
    app_db_path=APP_DB_PATH,
    external_db_path=EXTERNAL_DB_PATH,
)


@pytest.fixture(scope="session", autouse=True)
def _discard_test_data_dir():
    """세션이 끝나면 임시 데이터 폴더를 통째로 지운다."""

    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
