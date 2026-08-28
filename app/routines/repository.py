"""루틴 저장소 — Postgres(pgvector) `routines` 검색과, 이관 전까지 남겨둔 DynamoDB 인메모리 스토어.

search() 가 룰 필터 5개와 코사인 상위 K 를 쿼리 1건으로 처리한다(docs/루틴 저장소 pgvector.md).
반환 행의 body 는 종전 get_full 이 돌려주던 전체 루틴 dict 라 presenter 가 그대로 쓴다.
RoutineStore 는 4단계(서비스 전환)에서 제거한다.
"""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, SmallInteger, Text, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.clients import postgres
from app.core.config import get_settings
from app.core.dynamo import get_routines_table, to_plain
from app.core.orm import SearchBase
from app.routines.domain import LEVEL_ORDER


class Routine(SearchBase):
    """routines 테이블 — 검색에 쓰는 컬럼만 선언한다 (scripts/sql/routines_pgvector.sql 과 1:1)."""

    __tablename__ = "routines"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    level: Mapped[int] = mapped_column(SmallInteger)            # 0 beginner, 1 intermediate, 2 advanced
    minutes: Mapped[int | None] = mapped_column(SmallInteger)   # NULL 이면 시간 필터 통과
    muscle_groups: Mapped[list[str]] = mapped_column(ARRAY(Text))
    bodyweight_only: Mapped[bool] = mapped_column(Boolean)
    exercise_names: Mapped[list[str]] = mapped_column(ARRAY(Text))
    body: Mapped[dict] = mapped_column(JSONB)                   # 종전 get_full 이 돌려주던 전체 루틴
    embedding = mapped_column(Vector(1024))


@dataclass(frozen=True)
class SearchFilters:
    """룰 필터 입력 — service 가 요청·프로필·기록에서 조립한다."""

    muscle_groups: list[str]
    avoided: set[str]
    level: str
    minutes: int
    tolerance: float
    home_only: bool

    def minute_window(self) -> tuple[int, int]:
        """±tolerance 를 정수 분으로 닫은 범위."""
        span = int(self.minutes * self.tolerance)
        return self.minutes - span, self.minutes + span


def search_statement(filters: SearchFilters, query_vector, limit: int):
    """룰 필터 5개 WHERE + 코사인 ORDER BY + LIMIT. 기피는 빈 배열이면 항상 참이라 재시도 분기가 없다."""
    lo, hi = filters.minute_window()
    stmt = (
        select(Routine.slug, Routine.exercise_names, Routine.body)
        .where(
            Routine.muscle_groups.overlap(list(filters.muscle_groups)),
            ~Routine.muscle_groups.overlap(sorted(filters.avoided)),
            Routine.level <= LEVEL_ORDER[filters.level],
            or_(Routine.minutes.is_(None), Routine.minutes.between(lo, hi)),
        )
        .order_by(Routine.embedding.cosine_distance(np.asarray(query_vector, dtype=np.float32)))
        .limit(limit)
    )
    if filters.home_only:
        stmt = stmt.where(Routine.bodyweight_only.is_(True))
    return stmt


def search(filters: SearchFilters, query_vector, limit: int) -> list[dict]:
    """룰 필터 통과 루틴을 쿼리 벡터와의 코사인 순으로 상위 limit 건. 동기 — 스레드에서 호출."""
    return postgres.fetch_all(search_statement(filters, query_vector, limit))


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
