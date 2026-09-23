from enum import StrEnum


class StreamKind(StrEnum):
    DELTA = "delta"
    DONE = "done"
    ERROR = "error"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
