import re

HOLD_CHARS = "*_`#-+>["
MID_LINE = "\x00"
LINK_HOLD_LIMIT = 300

_FENCE = re.compile(r"```[^\n]*\n?")
_LINK = re.compile(r"\[([^\]\n]+)\]\((?:[^)\s]+)\)")
_RULE_LINE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_LINE_MARKER = re.compile(r"^([ \t]*)(?:#{1,6}[ \t]+|[-*+][ \t]+|>[ \t]?)", re.M)
_DOUBLE = re.compile(r"\*\*|__")
_BACKTICK = re.compile(r"`")


def _drop_emphasis_stars(text: str) -> str:
    """강조용 단일 별표 제거, 양옆 공백이나 숫자면 곱셈으로 보고 유지"""
    kept = []
    for index, char in enumerate(text):
        if char != "*":
            kept.append(char)
            continue
        before = text[index - 1] if index > 0 else " "
        after = text[index + 1] if index + 1 < len(text) else " "
        if (before.isspace() and after.isspace()) or (before.isdigit() and after.isdigit()):
            kept.append(char)
    return "".join(kept)


def strip_markdown(text: str) -> str:
    """앱 평문 표시용 마크다운 기호 제거, 번호 목록은 유지"""
    text = _FENCE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _RULE_LINE.sub("", text)
    text = _LINE_MARKER.sub(r"\1", text)
    text = _DOUBLE.sub("", text)
    text = _BACKTICK.sub("", text)
    return _drop_emphasis_stars(text)


def _hold_from(pending: str, at_line_start: bool) -> int:
    """아직 판단할 수 없는 꼬리 시작 위치"""
    cut = len(pending)
    while cut > 0 and pending[cut - 1] in HOLD_CHARS:
        cut -= 1
    newline = pending.rfind("\n", 0, cut)
    line_start = newline + 1
    starts_line = newline != -1 or at_line_start
    if starts_line and not pending[line_start:cut].strip(" \t" + HOLD_CHARS):
        cut = line_start
    fence = pending.rfind("```", 0, cut)
    if fence != -1 and "\n" not in pending[fence:]:
        cut = min(cut, fence)
    bracket = pending.rfind("[", 0, cut)
    if bracket != -1 and ")" not in pending[bracket:] and len(pending) - bracket <= LINK_HOLD_LIMIT:
        cut = min(cut, bracket)
    return cut


class MarkdownStreamFilter:
    """스트리밍 조각 마크다운 제거, 쪼개진 기호는 다음 조각까지 보류"""

    def __init__(self):
        """보류 버퍼와 직전 출력 문맥 초기화"""
        self._pending = ""
        self._context = "\n"

    def _emit(self, text: str) -> str:
        """직전 문맥을 붙여 정제 후 새 부분만 반환"""
        if not text:
            return ""
        prefix = self._context if self._context == "\n" else MID_LINE + self._context
        cleaned = strip_markdown(prefix + text)[len(prefix):]
        if cleaned:
            self._context = cleaned[-1]
        return cleaned

    def feed(self, chunk: str) -> str:
        """조각 투입, 확정된 앞부분만 정제해 반환"""
        self._pending += chunk
        cut = _hold_from(self._pending, self._context == "\n")
        ready, self._pending = self._pending[:cut], self._pending[cut:]
        return self._emit(ready)

    def flush(self) -> str:
        """스트림 종료, 보류분 정제해 반환"""
        rest, self._pending = self._pending, ""
        return self._emit(rest)


INTERNAL_TAGS = ("thread_summary", "user_summary", "user_context")
_INTERNAL_BLOCK = re.compile(r"<(" + "|".join(INTERNAL_TAGS) + r")>.*?(?:</\1>|\Z)", re.S)


def strip_internal_blocks(text: str) -> str:
    """답변에 새어 나온 내부 문맥 태그 블록 제거"""
    return _INTERNAL_BLOCK.sub("", text)


class InternalBlockFilter:
    """스트리밍 조각에서 내부 문맥 블록 제거, 태그 판단 전까지 보류"""

    def __init__(self):
        """보류 버퍼 초기화"""
        self._pending = ""

    def feed(self, chunk: str) -> str:
        """조각 투입, 내부 블록을 뺀 확정분 반환"""
        self._pending += chunk
        out = []
        while self._pending:
            start = self._pending.find("<")
            if start == -1:
                out.append(self._pending)
                self._pending = ""
                break
            out.append(self._pending[:start])
            rest = self._pending[start:]
            tag = next((name for name in INTERNAL_TAGS if rest.startswith(f"<{name}>")), None)
            if tag is not None:
                close = rest.find(f"</{tag}>")
                if close == -1:
                    self._pending = rest
                    break
                self._pending = rest[close + len(tag) + 3:]
                continue
            if any(f"<{name}>".startswith(rest) for name in INTERNAL_TAGS):
                self._pending = rest
                break
            out.append("<")
            self._pending = rest[1:]
        return "".join(out)

    def flush(self) -> str:
        """스트림 종료, 닫히지 않은 내부 블록은 버림"""
        rest, self._pending = self._pending, ""
        return strip_internal_blocks(rest)
