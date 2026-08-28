"""루틴 저장소 — Postgres(pgvector) `routines` 검색과, 이관 전까지 남겨둔 DynamoDB 인메모리 스토어.

search() 가 룰 필터 5개와 코사인 상위 K 를 쿼리 1건으로 처리한다(docs/루틴 저장소 pgvector.md).
반환 행의 body 는 종전 get_full 이 돌려주던 전체 루틴 dict 라 presenter 가 그대로 쓴다.
RoutineStore 는 4단계(서비스 전환)에서 제거한다.
"""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.clients import postgres
from app.core.config import get_settings
from app.core.dynamo import get_routines_table, to_plain
from app.routines.domain import LEVEL_ORDER

# 룰 필터 5개가 WHERE 절 5줄로 1:1 대응된다. 기피는 빈 배열이면 항상 참이라 재시도 분기가 없다.
SEARCH_SQL = """
SELECT slug, exercise_names, body
FROM routines
WHERE muscle_groups && %(muscles)s
  AND NOT (muscle_groups && %(avoided)s)
  AND level <= %(level)s
  AND (minutes IS NULL OR minutes BETWEEN %(lo)s AND %(hi)s)
  AND (%(home_only)s = false OR bodyweight_only)
ORDER BY embedding <=> %(query)s
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class SearchFilters:
    """룰 필터 입력 — service 가 요청·프로필·기록에서 조립한다."""

    muscle_groups: list[str]
    avoided: set[str]
    level: str
    minutes: int
    tolerance: float
    home_only: bool

    def bind(self) -> dict:
        """WHERE 절 바인딩 값. 시간 범위는 ±tolerance 를 정수 분으로 닫는다."""
        span = int(self.minutes * self.tolerance)
        return {
            "muscles": list(self.muscle_groups),
            "avoided": sorted(self.avoided),
            "level": LEVEL_ORDER[self.level],
            "lo": self.minutes - span,
            "hi": self.minutes + span,
            "home_only": self.home_only,
        }


def search(filters: SearchFilters, query_vector, limit: int) -> list[dict]:
    """룰 필터 통과 루틴을 쿼리 벡터와의 코사인 순으로 상위 limit 건. 동기 — 스레드에서 호출."""
    params = filters.bind()
    params["query"] = np.asarray(query_vector, dtype=np.float32)
    params["limit"] = limit
    return postgres.fetch_all(SEARCH_SQL, params)


def _parse_embedding(raw, dimension: int) -> np.ndarray | None:
    if raw is None:
        return None
    vector = np.frombuffer(bytes(raw), dtype="<f4")
    if vector.shape[0] != dimension:
        return None
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return None
    return vector / norm


def _to_light(item: dict) -> dict:
    """전체 항목 → 라이트 항목 — 룰 필터 필드 + LLM 프롬프트용 종목명 요약만 남긴다."""
    minutes = item.get("minutes_per_routine")
    return {
        "slug": item["slug"],
        "level": item.get("level", "beginner"),
        "goal": item.get("goal"),
        "muscle_groups": list(item.get("muscle_groups", [])),
        "equipment": list(item.get("equipment", [])),
        "minutes_per_routine": int(minutes) if minutes else None,
        "exercise_names": [e["exercise_name"] for e in item.get("exercises", [])],
    }


class RoutineStore:
    """라이트 인메모리 루틴 + 정규화 임베딩 행렬. ready 전에는 서빙 불가(헬스체크 연동)."""

    def __init__(self):
        self.routines: list[dict] = []
        self.vectors: np.ndarray | None = None
        self.ready = False

    def load(self) -> None:
        """전량 Scan → 라이트 리스트 + (N, dim) 정규화 임베딩 행렬 구성. 동기 — 스레드에서 호출."""
        settings = get_settings()
        table = get_routines_table()
        routines: list[dict] = []
        vector_chunks: list[np.ndarray] = []
        kwargs: dict = {}
        while True:
            page = table.scan(**kwargs)
            for item in page["Items"]:
                embedding = _parse_embedding(item.get("embedding"), settings.embed_dimension)
                vector_chunks.append(
                    embedding if embedding is not None
                    else np.zeros(settings.embed_dimension, dtype=np.float32)
                )
                routines.append(_to_light(item))
            if settings.routines_scan_limit and len(routines) >= settings.routines_scan_limit:
                routines = routines[: settings.routines_scan_limit]
                vector_chunks = vector_chunks[: settings.routines_scan_limit]
                break
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

        self.routines = routines
        self.vectors = np.vstack(vector_chunks) if vector_chunks else np.zeros(
            (0, settings.embed_dimension), dtype=np.float32
        )
        self.ready = True

    def get_full(self, slug: str) -> dict | None:
        """전체 루틴(세트 상세 포함) 단건 조회 — 최종 선택된 루틴에만 사용. GetItem 1회."""
        item = get_routines_table().get_item(Key={"slug": slug}).get("Item")
        if item is None:
            return None
        item.pop("embedding", None)
        item.pop("embedding_model", None)
        return to_plain(item)


@lru_cache
def get_routine_store() -> RoutineStore:
    return RoutineStore()
