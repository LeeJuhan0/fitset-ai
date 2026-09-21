from app.exercises import repository as exercise_catalog
from app.exercises.exceptions import ExerciseNotFoundError, VideoNotFoundError


async def get_video(slug: str, fallback: bool = False) -> dict:
    """카탈로그 기반 가이드 영상 URL 조회 부재 시 404"""
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
        "expiresAt": None,
    }
