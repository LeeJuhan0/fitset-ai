from app.memory.schemas import UserPreferences

PLACE_NAMES = {"gym": "헬스장", "home": "집", "outdoor": "야외", "mixed": "여러 곳"}


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
