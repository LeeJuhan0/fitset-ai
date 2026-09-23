from fastapi import status

from app.core.exceptions import DomainError


class ExerciseNotFoundError(DomainError):
    """운동 종목 없음, 404"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="EXERCISE_NOT_FOUND",
            message="운동 종목을 찾을 수 없습니다.",
        )


class VideoNotFoundError(DomainError):
    """가이드 영상 미등록 종목, 404"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="VIDEO_NOT_FOUND",
            message="가이드 영상이 등록되지 않은 종목입니다.",
        )
