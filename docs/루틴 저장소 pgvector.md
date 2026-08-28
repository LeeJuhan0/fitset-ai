# 루틴 저장소 pgvector

루틴 검색 저장소를 DynamoDB와 인메모리 numpy에서 RDS Postgres(pgvector)로 옮기는 설계다. 이관 순서는 [루틴 pgvector 이관 로드맵](루틴%20pgvector%20이관%20로드맵.md), DDL은 `scripts/sql/routines_pgvector.sql`, 적재는 `scripts/load_routines_postgres.py`다.

## 1. 왜 옮기나

| 문제 | 인메모리 구조 | pgvector |
|---|---|---|
| 부팅 | DynamoDB 전량 Scan 수십 초, startupProbe와 충돌 | 즉시 |
| 메모리 | 파드마다 임베딩 행렬 100MB 상주, 레플리카 수만큼 배수 | 수백 MB 이하 |
| 갱신 | 배치 후 재배포 전까지 미반영 | 적재 즉시 반영 |
| 검색 지연 | 24ms | 수십 ms |

검색 지연은 오히려 조금 늘지만 요청 전체 3.6초의 1~2%라 의미가 없다. 실측 근거는 [쿼리 변환 LLM AB 실험](쿼리%20변환%20LLM%20AB%20실험.md)이다.

## 2. 왜 pgvector인가

| 선택지 | 벡터 검색과 필터 | 월 비용 | 탈락 이유 |
|---|---|---|---|
| RDS Postgres pgvector | 한 쿼리 | 약 15달러 | 채택 |
| MongoDB Atlas Vector Search | 한 쿼리 | 약 60달러 | AWS 밖, 피어링 필요, 배열 포함 필터가 어색 |
| 자체 설치 MongoDB | 없음, mongot 프리뷰 | 노드 비용 | 벡터 인덱스 없음, PVC 필요 |
| OpenSearch | 한 쿼리 | 30~700달러 | 운영 무게 |
| DynamoDB GSI | 불가 | 0 | 파티션키 등호 1개, 리스트 포함 검색 없음 |

룰 필터 5개(부위 교집합, 기피, 수준 상한, 시간 범위, 홈트 장비)가 WHERE 절로 그대로 표현되는 것이 결정 요인이다.

## 3. 스키마

테이블은 `routines` 하나다. 이 DB의 역할은 벡터 검색 엔진이라 도메인 정규화를 하지 않는다, Atlas나 OpenSearch를 썼어도 컬렉션 하나였을 자리다.

| 컬럼 묶음 | 컬럼 | 쓰임 |
|---|---|---|
| 식별 | `slug` UNIQUE | 재적재 upsert 키 |
| 룰 필터 | `level` smallint, `minutes`, `muscle_groups` text[], `equipment` text[], `bodyweight_only` | WHERE 절 |
| 랭킹 | `embedding` vector(1024), `embedding_model` | ORDER BY 코사인 |
| 응답 | `exercise_names` text[], `body` jsonb | LLM 선택 프롬프트와 응답 조립 원본 |

`body`는 지금 `RoutineStore.get_full`이 돌려주던 dict 그대로라 presenter가 그대로 쓴다. 종목별 집계가 필요하면 `jsonb_array_elements(body->'exercises')`로 펼치고, 잦아지면 materialized view나 `body` GIN 인덱스(`jsonb_path_ops`)를 그때 더한다.

## 4. 인덱스

| 인덱스 | 정의 | 이유 |
|---|---|---|
| `routines_muscle_gin` | GIN (muscle_groups) | `&&` 교집합과 기피, 선택도가 가장 높다 |
| `routines_level_minutes` | BTREE (level, minutes) | 수준 상한과 시간 범위, GIN 비트맵과 AND |
| `routines_bodyweight` | BTREE (level, minutes) WHERE bodyweight_only | 홈트 요청만 타는 부분 인덱스 |

벡터 인덱스는 두지 않는다. 25k건은 필터 후 남는 수백에서 2만 건에 대해 exact 정렬을 해도 수십 ms이고, HNSW는 필터 통과 행이 `ef_search`보다 적으면 30건을 못 채운다. 10만 건을 넘기면 HNSW와 `hnsw.iterative_scan = relaxed_order`를 함께 켠다.

## 5. 검색 쿼리

```sql
SELECT slug, exercise_names, body
FROM routines
WHERE muscle_groups && %(muscles)s
  AND NOT (muscle_groups && %(avoided)s)
  AND level <= %(level)s
  AND (minutes IS NULL OR minutes BETWEEN %(lo)s AND %(hi)s)
  AND (%(home_only)s = false OR bodyweight_only)
ORDER BY embedding <=> %(qvec)s
LIMIT 30;
```

로컬 pgvector 컨테이너에 600건을 넣고 EXPLAIN ANALYZE한 결과, `(level, minutes)` 비트맵 인덱스 두 개가 OR로 결합되고 배열 조건이 필터로 걸린 뒤 정렬까지 1ms였다. 기피 부위만으로 후보가 비면 `avoided`를 빈 배열로 한 번 더 부른다.

## 6. 적재

```
DynamoDB routines (변환 완료본, 임베딩 포함)
  → scripts/load_routines_postgres.py  (Scan, slug upsert, 500건 트랜잭션)
  → routines
```

Bedrock을 다시 부르지 않으므로 비용이 없고 25,853건에 1분 안쪽이다. 몇 번을 돌려도 결과가 같다. 이관이 끝나면 원천(S3 캐글) 변환 배치가 DynamoDB 대신 Postgres에 바로 쓰도록 바꾼다.

## 7. RDS 인스턴스

| 항목 | 값 |
|---|---|
| 엔진 | PostgreSQL 17, pgvector 확장은 `CREATE EXTENSION vector` |
| 인스턴스 | db.t4g.micro, gp3 20GB |
| 배치 | fitset-infra `terraform/rds.tf`, stage data 서브넷, SG 5432는 app-stage CIDR만 |
| 자격 | SSM `/fitset/stage/pg/host`, `/fitset/stage/pg/password`, ExternalSecret으로 주입 |
| 앱 연결 | SQLAlchemy `postgresql+psycopg` 엔진, pool_size 5, 세션 `default_transaction_read_only=on`, statement_timeout 2초. 엔티티 `routines.repository.Routine`(`pgvector.sqlalchemy.Vector`), 쿼리는 `select()` 조립 |

크기는 임베딩 105MB에 본문 30MB다. shared_buffers 256MB에 검색 테이블이 다 들어가진 않지만 필터 후 접근하는 행만 읽으므로 문제 없다.

## 8. 바꾸지 않는 것

1. 요청당 Bedrock 쿼리 임베딩 1회와 LLM 선택 1회.
2. 채팅, 유저 요약, 종목 카탈로그의 DynamoDB 테이블.
3. 백엔드 MySQL 직조회(NL2SQL)와 그 엔티티.

## 9. DBML

```dbml
Project fitset_routines {
  database_type: 'PostgreSQL'
  Note: 'pgvector 확장 필요. 테이블은 routines 하나, 역할은 벡터 검색 엔진'
}

Table routines {
  id              integer      [pk, increment]
  slug            text         [unique, not null, note: '루틴 식별자, 재적재 upsert 키']
  name            text
  description     text         [note: '원천 프로그램 설명, 임베딩 텍스트의 일부']
  goal            text         [note: 'hypertrophy, strength, weight_loss, endurance']
  level           smallint     [not null, note: '0 beginner, 1 intermediate, 2 advanced. 룰 필터 level <= 요청']
  minutes         smallint     [note: '세트 수 기반 재산출 분. NULL 이면 시간 필터 통과']
  muscle_groups   text[]       [not null, note: '룰 필터 && 교집합, NOT && 기피']
  equipment       text[]       [not null]
  bodyweight_only boolean      [not null, note: 'equipment 가 bodyweight 뿐. 홈트 유저 필터']
  exercise_count  smallint     [not null]
  exercise_names  text[]       [not null, note: 'LLM 선택 프롬프트용 한글 종목명, 조인 없이 읽는다']
  body            jsonb        [not null, note: 'exercises[{slug, exercise_name, thumbnail_url, order_index, sets[{order_index, reps, weight}]}]. 응답 조립 원본']
  embedding       vector       [not null, note: 'vector(1024), cohere embed-v4, L2 정규화. ORDER BY embedding <=> query']
  embedding_model text         [not null, note: '재임베딩 판별 태그']
  source          text         [note: 'kaggle']
  created_at      timestamptz  [not null, default: `now()`]
  updated_at      timestamptz  [not null, default: `now()`]

  indexes {
    muscle_groups           [type: gin,   name: 'routines_muscle_gin',    note: '배열 교집합, 기피. 선택도 최상']
    (level, minutes)        [type: btree, name: 'routines_level_minutes', note: '수준 상한, 시간 범위. GIN 비트맵과 AND']
    (level, minutes)        [type: btree, name: 'routines_bodyweight',    note: 'WHERE bodyweight_only 부분 인덱스']
    embedding               [type: hnsw,  name: 'routines_embedding_hnsw', note: '보류. 10만 건 초과 시 vector_cosine_ops 로 생성, hnsw.iterative_scan = relaxed_order 동반']
  }
}

```

## 10. 인덱스 최적화 상태

지금 상태는 "필터 인덱스는 완료, 벡터 인덱스는 의도적으로 없음"이다.

| 구간 | 상태 | 근거 |
|---|---|---|
| 룰 필터 | 완료 | GIN 과 (level, minutes) 비트맵이 결합돼 필터 통과 행만 힙에서 읽는다. 600건 EXPLAIN 에서 1ms |
| 코사인 정렬 | exact, 인덱스 없음 | 필터 후 남는 162~20,654건에 대해 1024차원 내적. 최악 2만 건도 수십 ms |
| 벡터 인덱스 | 보류 | HNSW 는 필터 통과 행이 ef_search(기본 40)보다 적으면 30건을 못 채운다. 25k 규모에서 이득이 없다 |

25,853건 전량에서 다시 EXPLAIN ANALYZE 해 봐야 확정된다. 확인할 값은 두 가지다.

1. 필터 통과 2만 건 케이스(부위 넓게, 수준 advanced, 시간 범위 넓게)의 실행 시간. 100ms 를 넘기면 HNSW 를 켠다.
2. `Heap Blocks` 대비 shared_buffers. t4g.micro 는 256MB 라 임베딩 105MB 를 포함한 테이블이 캐시에 다 안 올라갈 수 있고, 그러면 첫 요청 지연이 디스크 읽기에 좌우된다. `pg_prewarm` 또는 인스턴스 한 단계 상향으로 대응한다.
