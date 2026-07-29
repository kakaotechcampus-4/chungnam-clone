"""최종 조합된 system prompt의 위생 검사 (unittest, 결정적).

멘토 리뷰 반영: 각 프롬프트 조각만 보지 말고 "최종 조합된 프롬프트"를 기준으로
  (1) 정체성/역할 설명이 반복 재지정되지 않는지,
  (2) 강의용 'Week N' 라벨이 제품 프롬프트에 새지 않는지,
  (3) '…는 아직 하지 않는다' 류의 커리큘럼 범위 부정(상속-모순의 근원)이 없는지
를 매번 눈으로 보는 대신 자동으로 지킨다.

LLM/네트워크 불필요. import 전에 CONFIG를 임시 경로 + 토큰 비움으로 돌려 실제 data/·외부 호출을 격리한다.

실행: python -m unittest discover -s tests
"""

from __future__ import annotations

import dataclasses
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fixed.config as _cfg

_TMP = Path(tempfile.mkdtemp(prefix="prompt_hygiene_"))
_cfg.CONFIG = dataclasses.replace(
    _cfg.CONFIG,
    proxy_token=None,
    chroma_dir=_TMP / "chroma",
    app_db_path=_TMP / "app.sqlite3",
    external_db_path=_TMP / "external.sqlite3",
)

from student_parts.week01_wake_up_nana import week01_system_prompt
from student_parts.week02_structure_natural_language_requests import week02_system_prompt
from student_parts.week03_build_nanas_logbook import week03_system_prompt
from student_parts.week04_retrieve_nanas_memory import week04_system_prompt

# 각 주차의 "최종 조합된" system prompt (뒤 주차일수록 앞 주차 조각을 모두 누적한다).
_SYSTEM_PROMPTS = {
    "week1": week01_system_prompt,
    "week2": week02_system_prompt,
    "week3": week03_system_prompt,
    "week4": week04_system_prompt,
}

# "너는 ... {agent|에이전트|비서}" 형태의 정체성/역할 재지정 문장.
_IDENTITY = re.compile(r"너는 .{0,40}?(agent|에이전트|비서)")


class PromptHygiene(unittest.TestCase):
    def test_single_identity_no_reassignment(self) -> None:
        """조합 프롬프트에 정체성 재지정 문장은 하나(기본 Nana 비서)만 있어야 한다."""
        for name, build in _SYSTEM_PROMPTS.items():
            identity_lines = [ln.strip() for ln in build().splitlines() if _IDENTITY.search(ln)]
            self.assertEqual(len(identity_lines), 1, f"{name}: 정체성 문장 {len(identity_lines)}개 → {identity_lines}")

    def test_no_curriculum_week_labels(self) -> None:
        """강의용 'Week N' 라벨이 제품 프롬프트 텍스트에 노출되면 안 된다."""
        for name, build in _SYSTEM_PROMPTS.items():
            match = re.search(r"Week\s*\d", build())
            self.assertIsNone(match, f"{name}: 'Week N' 라벨 노출 → {match.group(0) if match else ''}")

    def test_no_self_negating_scope(self) -> None:
        """'…는 아직 하지 않는다' 류 커리큘럼 범위 부정(모순 근원)이 남아 있으면 안 된다."""
        for name, build in _SYSTEM_PROMPTS.items():
            self.assertNotIn("아직 하지 않는다", build(), f"{name}: 커리큘럼 범위 부정 잔존")


if __name__ == "__main__":
    unittest.main()
