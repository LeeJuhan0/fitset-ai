"""운동 도메인 서비스 — 가이드 영상 재생 URL 발급 (클라이언트 API 명세 §6-B).

챗봇 payload에 담기는 URL과 재발급 API가 같은 함수를 쓴다 — 응답 구조가 동일해야
클라가 payload 자리에 그대로 갈아끼울 수 있기 때문이다.

URL 출처는 카탈로그 캐시의 CloudFront URL 하나뿐이다 (2026-08-12 내부 API 파기 —
구 폴백이던 종목 마스터 videoUrl은 소멸, ERD상 exercise 테이블에도 URL 컬럼이 없다).
무서명·무기한이라 expiresAt이 null이고 저장된 대화를 다시 열어도 그대로 재생된다.
"""
from app.core.errors import ExerciseNotFoundError, VideoNotFoundError
from app.exercises import repository as exercise_catalog


async def get_video(slug: str, fallback: bool = False) -> dict:
    """가이드 영상 재생 URL — 카탈로그 CDN. 재발급(fallback=True)도 같은 출처를 다시 읽는다."""
    name = exercise_catalog.exercise_name(slug)
    if name is None:
        raise ExerciseNotFoundError()

    video_url = exercise_catalog.cdn_video_url(slug)
    if video_url is None:
        raise VideoNotFoundError()

    return {
        "exerciseId": exercise_catalog.exercise_id(slug),
        "slug": slug,
        "exerciseName": name,
        "videoUrl": video_url,
        "expiresAt": None,    # 공개 CloudFront — 만료 없음
    }
