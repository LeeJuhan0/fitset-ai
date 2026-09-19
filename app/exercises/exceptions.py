"""운동 종목 도메인 예외. 베이스와 공통 예외는 core/exceptions.py에 있다."""
from app.core.exceptions import DomainError


class ExerciseNotFoundError(DomainError):
    status_code = 404
    code = "EXERCISE_NOT_FOUND"
    default_message = "운동 종목을 찾을 수 없습니다."


class VideoNotFoundError(DomainError):
    status_code = 404
    code = "VIDEO_NOT_FOUND"
    default_message = "가이드 영상이 등록되지 않은 종목입니다."
