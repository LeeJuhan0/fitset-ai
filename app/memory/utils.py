from app.memory.schemas import UserPreferences

PLACE_NAMES = {"gym": "헬스장", "home": "집", "outdoor": "야외", "mixed": "여러 곳"}
EXERCISE_FIELDS = ("preferred_exercises", "avoided_exercises", "rejected_exercises")


def render_preferences(preferences: UserPreferences) -> str | None:
    """운동 편향 구조를 채팅 문맥용 한국어 줄로"""
    lines = []
    if preferences.preferred_exercises:
        lines.append(f"선호 종목: {', '.join(preferences.preferred_exercises)}")
    if preferences.avoided_exercises:
        lines.append("기피 종목: " + ", ".join(_with_reason(item.name, item.reason) for item in preferences.avoided_exercises))
    if preferences.rejected_exercises:
        lines.append("추천 거절: " + ", ".join(_with_reason(item.name, item.reason) for item in preferences.rejected_exercises))
    environment = preferences.environment
    if environment.place or environment.equipment:
        place = PLACE_NAMES.get(environment.place or "", "")
        lines.append("운동 환경: " + ", ".join(part for part in [place, *environment.equipment] if part))
    if preferences.training_style:
        lines.append(f"선호 방식: {preferences.training_style}")
    if preferences.specific_goals:
        lines.append(f"세부 목표: {', '.join(preferences.specific_goals)}")
    return "\n".join(lines) or None


def _with_reason(name: str, reason: str | None) -> str:
    """종목명 뒤 괄호 이유"""
    return f"{name}({reason})" if reason else name


def grounded(preferences: UserPreferences, user_text: str, previous: UserPreferences | None) -> UserPreferences:
    """유저 문장에 근거 있는 종목만 남긴 운동 편향"""
    kept = {
        field: [item for item in getattr(preferences, field) if _is_grounded(_name(item), user_text, _names(previous, field))]
        for field in EXERCISE_FIELDS
    }
    return preferences.model_copy(update=kept)


def _is_grounded(name: str, user_text: str, previous: set[str]) -> bool:
    """이전 편향에 있거나 유저 문장에 이름 단어가 나온 종목"""
    if name in previous:
        return True
    if name.endswith("운동"):
        return False
    return any(len(word) >= 2 and word in user_text for word in name.split())


def _name(item) -> str:
    """문자열이나 종목 객체의 종목명"""
    return item if isinstance(item, str) else item.name


def _names(preferences: UserPreferences | None, field: str) -> set[str]:
    """이전 편향 한 필드의 종목명 집합"""
    if preferences is None:
        return set()
    return {_name(item) for item in getattr(preferences, field)}
