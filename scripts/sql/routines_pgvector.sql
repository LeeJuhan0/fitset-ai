-- 루틴 검색 저장소 — DynamoDB routines 를 대체하는 Postgres(pgvector) 스키마.
-- 테이블은 routines 하나다. 이 DB 의 역할은 벡터 검색 엔진이라 도메인 정규화를 하지 않는다.
-- 종목별 집계가 필요하면 jsonb_array_elements(body->'exercises') 로 펼친다.
--
-- 적용:  psql "$PG_DSN" -f scripts/sql/routines_pgvector.sql
-- 적재:  uv run --with boto3 --with "psycopg[binary]" --with pgvector python scripts/load_routines_postgres.py

CREATE EXTENSION IF NOT EXISTS vector;

-- 검색 테이블. 룰 필터 컬럼, 임베딩, 응답 본문을 한 행에 둔다.
CREATE TABLE IF NOT EXISTS routines (
  id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug            text         NOT NULL UNIQUE,
  name            text,
  description     text,
  goal            text,
  level           smallint     NOT NULL,           -- 0 beginner, 1 intermediate, 2 advanced
  minutes         smallint,                        -- NULL 이면 시간 필터를 통과한다
  muscle_groups   text[]       NOT NULL,
  equipment       text[]       NOT NULL,
  bodyweight_only boolean      NOT NULL,           -- equipment 가 bodyweight 뿐인지 적재 시 계산
  exercise_count  smallint     NOT NULL,
  exercise_names  text[]       NOT NULL,           -- LLM 선택 프롬프트용 한글 종목명
  body            jsonb        NOT NULL,           -- exercises[{slug, exercise_name, thumbnail_url, order_index, sets[{order_index, reps, weight}]}]
  embedding       vector(1024) NOT NULL,
  embedding_model text         NOT NULL,
  source          text,
  created_at      timestamptz  NOT NULL DEFAULT now(),
  updated_at      timestamptz  NOT NULL DEFAULT now()
);

-- 부위 교집합(&&)과 기피(NOT &&) 필터. 선택도가 가장 높아 플래너가 먼저 탄다.
CREATE INDEX IF NOT EXISTS routines_muscle_gin    ON routines USING gin (muscle_groups);
-- 수준 상한과 시간 범위. GIN 비트맵과 AND 로 결합된다.
CREATE INDEX IF NOT EXISTS routines_level_minutes ON routines (level, minutes);
-- 홈트 유저 요청만 타는 부분 인덱스.
CREATE INDEX IF NOT EXISTS routines_bodyweight    ON routines (level, minutes) WHERE bodyweight_only;

-- 벡터 인덱스는 두지 않는다. 25k 건은 필터 후 exact 정렬이 수십 ms 이고,
-- HNSW 는 필터 통과 행이 ef_search 보다 적으면 30건을 못 채운다.
-- 10만 건을 넘기면 아래를 켜고 hnsw.iterative_scan = relaxed_order 를 함께 설정한다.
-- CREATE INDEX routines_embedding_hnsw ON routines USING hnsw (embedding vector_cosine_ops);


-- 서비스 검색 쿼리 (참고).
-- SELECT slug, exercise_names, body
-- FROM routines
-- WHERE muscle_groups && %(muscles)s
--   AND NOT (muscle_groups && %(avoided)s)
--   AND level <= %(level)s
--   AND (minutes IS NULL OR minutes BETWEEN %(lo)s AND %(hi)s)
--   AND (%(home_only)s = false OR bodyweight_only)
-- ORDER BY embedding <=> %(qvec)s
-- LIMIT 30;
