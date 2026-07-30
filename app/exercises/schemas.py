"""운동 도메인 응답 모델 — 클라이언트 API 명세 §6-B.

구조는 §7 exerciseGif payload와 동일하다 — 클라가 만료된 payload 자리에
이 응답을 그대로 갈아끼울 수 있어야 하기 때문. 로직·I/O 금지.
"""
from app.core.schemas import CamelModel


class ExerciseVideoOut(CamelModel):
    slug: str
    exercise_name: str
    video_url: str
    expires_at: str   # ISO 8601 UTC(Z) — 클라가 만료 전 재발급 판단에 쓴다
