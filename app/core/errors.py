"""도메인 예외 — service가 던지고 main.py 전역 핸들러가 {traceId, error}로 번역한다.

code는 01 규약 §4 카탈로그의 시맨틱 코드. HTTPException을 service에서 직접 쓰지 않는다(계층 규칙).
"""


class DomainError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "서버 내부 오류가 발생했습니다."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class UnauthorizedError(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "인증에 실패했습니다."


class UserNotFoundError(DomainError):
    status_code = 404
    code = "USER_NOT_FOUND"
    default_message = "사용자를 찾을 수 없습니다."


class NoRoutineCandidateError(DomainError):
    status_code = 409
    code = "NO_ROUTINE_CANDIDATE"
    default_message = "조건을 만족하는 루틴을 구성할 수 없습니다."


class UnsafeConstraintError(DomainError):
    status_code = 409
    code = "UNSAFE_CONSTRAINT"
    default_message = "안전하게 추천할 수 있는 루틴이 없습니다. 전문가와 상담해주세요."


class AiUnavailableError(DomainError):
    status_code = 503
    code = "AI_UNAVAILABLE"
    default_message = "AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요."


class ThreadNotFoundError(DomainError):
    status_code = 404
    code = "THREAD_NOT_FOUND"
    default_message = "대화 스레드를 찾을 수 없습니다."


class ThreadForbiddenError(DomainError):
    # 현 chat_threads PK가 (user_id, thread_id)라 남의 스레드는 조회 자체가 비어 404가 된다.
    # 계약(클라이언트 API 명세 §4)에는 남아있어 예약해 두되, 실제 발생 경로는 없다
    status_code = 403
    code = "THREAD_FORBIDDEN"
    default_message = "접근할 수 없는 대화 스레드입니다."


class ThreadQuotaExceededError(DomainError):
    status_code = 409
    code = "THREAD_QUOTA_EXCEEDED"
    default_message = "대화 스레드가 최대 개수에 도달했습니다. 기존 스레드를 삭제한 후 다시 시도해주세요."


class RateLimitedError(DomainError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요."


class ThreadFullError(DomainError):
    status_code = 409
    code = "THREAD_FULL"
    default_message = "대화가 너무 길어 더 보낼 수 없습니다. 새 스레드에서 계속해주세요."


class ExerciseNotFoundError(DomainError):
    status_code = 404
    code = "EXERCISE_NOT_FOUND"
    default_message = "운동 종목을 찾을 수 없습니다."


class VideoNotFoundError(DomainError):
    status_code = 404
    code = "VIDEO_NOT_FOUND"
    default_message = "가이드 영상이 등록되지 않은 종목입니다."
