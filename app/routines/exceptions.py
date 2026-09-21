from app.core.exceptions import DomainError


class NoRoutineCandidateError(DomainError):
    status_code = 409
    code = "NO_ROUTINE_CANDIDATE"
    default_message = "조건을 만족하는 루틴을 구성할 수 없습니다."


class UnsafeConstraintError(DomainError):
    status_code = 409
    code = "UNSAFE_CONSTRAINT"
    default_message = "안전하게 추천할 수 있는 루틴이 없습니다. 전문가와 상담해주세요."
