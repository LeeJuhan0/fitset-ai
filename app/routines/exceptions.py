from fastapi import status

from app.core.exceptions import DomainError


class NoRoutineCandidateError(DomainError):
    """조건 만족 루틴 후보 없음, 409"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="NO_ROUTINE_CANDIDATE",
            message="조건을 만족하는 루틴을 구성할 수 없습니다.",
        )


class UnsafeConstraintError(DomainError):
    """안전 가드레일 차단, 409"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="UNSAFE_CONSTRAINT",
            message="안전하게 추천할 수 있는 루틴이 없습니다. 전문가와 상담해주세요.",
        )
