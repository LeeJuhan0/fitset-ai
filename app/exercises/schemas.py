"""운동 도메인 응답 모델 — 클라이언트 API 명세 §6-B.

구조는 §7 exerciseGif payload와 동일하다 — 클라가 만료된 payload 자리에
이 응답을 그대로 갈아끼울 수 있어야 하기 때문. 로직·I/O 금지.
"""
from app.core.schemas import CamelModel


class ExerciseVideoOut(CamelModel):
    exercise_id: str | None   # 백엔드 마스터 UUID — 종목 상세 이동 키 (카탈로그 미스면 null)
    slug: str
    exercise_name: str
    video_url: str
    expires_at: str | None    # ISO 8601 UTC(Z). CDN URL은 만료가 없어 null (2026-08-05)
