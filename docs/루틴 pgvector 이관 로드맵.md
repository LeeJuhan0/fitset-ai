# 루틴 저장소 pgvector 이관 로드맵

인메모리 루틴 스토어(부팅 시 DynamoDB 전량 Scan + numpy 코사인)를 Postgres(pgvector) 검색으로 바꾼다. 채팅·유저 요약·종목 카탈로그 테이블은 DynamoDB 에 그대로 둔다.

## 배경

1. 실측(쿼리 변환 LLM AB 실험) 기준 룰 필터·코사인·랭킹은 요청당 24ms 로 병목이 아니다. 옮기는 이유는 속도가 아니라 부팅 수십 초, 파드당 임베딩 행렬 100MB, 재배포 없는 갱신이다.
2. k8s 이전(fitset-infra) 과정에서 파드 메모리 1Gi 를 맞추려고 부팅 로드를 먼저 뺐다(PR #44). 그 사이 루틴 생성은 503 이다.
3. 벡터 검색 엔진이 있는 저장소 중 pgvector 가 가장 싸고(db.t4g.micro), 룰 필터 5개가 WHERE 절로 그대로 표현된다.

## 목표 구조

```
요청 → 쿼리 임베딩(Bedrock 1회) → Postgres 1쿼리(룰 필터 + 코사인 상위 30) → 랜덤 5 → LLM 선택 → body 로 응답 조립
```

스키마는 `scripts/sql/routines_pgvector.sql`, 적재는 `scripts/load_routines_postgres.py`.

## 단계 (PR 당 200줄 안팎)

| 단계 | PR | 내용 | 파일 | 완료 기준 |
|---|---|---|---|---|
| 0 | 이 PR | DDL, 적재 스크립트, 로드맵 | `scripts/sql/`, `scripts/load_routines_postgres.py`, `docs/` | 스크립트 `--dry-run` 통과 |
| 1 | infra | RDS Postgres 17 db.t4g.micro (stage data 서브넷, SG 5432), SSM `/fitset/stage/pg/{host,password}`, ExternalSecret 항목 추가 | fitset-infra `terraform/rds.tf`, `gitops/charts/ai-chat-api/values*.yaml` | `psql` 접속, DDL 적용, 적재 스크립트 전량 완료(25,853건) |
| 2 | ai-server | Postgres 클라이언트. psycopg3 풀, pgvector 어댑터, 설정(`PG_HOST` 등), 미설정 시 `is_configured=False` | `app/clients/postgres.py`, `app/core/config.py`, `pyproject.toml` | 단위 테스트(연결 없이 설정 판정), `/health` 에 `SELECT 1` |
| 3 | ai-server | 검색 리포지토리. `search(muscles, avoided, level, minutes, tolerance, home_only, query_vec, limit)` 한 함수. 결과는 `(slug, exercise_names, body)` | `app/routines/repository.py` | SQL 문자열 스냅샷 테스트, 파라미터 바인딩 테스트 |
| 4 | ai-server | 서비스 전환. `_filter_candidates`·numpy·`RoutineStore` 제거, `generate_routine` ④~⑦ 을 리포지토리 호출로 교체, 기피 부위 재시도 유지 | `app/routines/service.py`, `app/main.py` | 기존 서비스 테스트를 리포지토리 목으로 통과 |
| 5 | ai-server | 통합 테스트. CI 에 `pgvector/pgvector:pg17` 서비스 컨테이너, DDL 적용 후 픽스처 50건으로 필터·정렬 검증 | `.github/workflows/ci.yml`, `tests/test_routines_pg.py` | CI 녹색 |
| 6 | ai-server | 정리. `load_routines_dynamodb.py`·`reindex_routines.py`·`embed_routines.py` 의 DynamoDB 쓰기 경로를 Postgres 로 통일하거나 삭제, `routines_scan_limit` 설정 삭제, 문서 갱신 | `scripts/`, `docs/`, `CLAUDE.md` | DynamoDB `routines` 테이블 미참조 |
| 7 | 운영 | stage 검증 후 DynamoDB `routines` 테이블 삭제, prod 전환 시 Postgres prod 인스턴스 또는 stage 공유 결정 | AWS | stage 루틴 생성 정상 |

## 각 단계 설계 메모

### 2. 클라이언트

`app/clients/mysql.py` 와 같은 꼴로 둔다. 엔진 대신 `psycopg_pool.ConnectionPool(min_size=1, max_size=5)`, 세션은 `READ ONLY`, `statement_timeout` 2초. 설정은 `pg_host`, `pg_port`, `pg_user`, `pg_password`, `pg_database` 이고 host 가 비면 루틴 생성이 지금처럼 503 으로 물러난다.

### 3. 리포지토리

SQL 은 하나다. 기피 부위 재시도는 `avoided` 를 빈 배열로 다시 부르면 되므로 분기가 없다. `LIMIT` 은 `settings.cosine_top_k`. 반환 행의 `body` 가 곧 지금 `get_full` 이 돌려주던 dict 라 presenter 는 그대로 쓴다.

### 4. 서비스

바뀌는 것은 ④~⑦ 구간뿐이다. `store.ready` 가드는 `postgres.is_configured()` 로 바뀐다. `_pick_with_llm` 은 `exercise_names` 만 쓰므로 시그니처만 조정한다.

### 5. 테스트

룰 필터 단위 테스트(`domain.passes_filters`)는 SQL 로 옮겨가므로 파이썬 쪽은 삭제하고, 같은 케이스를 SQL 통합 테스트로 옮긴다. 로컬은 `docker run -e POSTGRES_PASSWORD=x -p 5432:5432 pgvector/pgvector:pg17`.

## 바꾸지 않는 것

1. 요청당 Bedrock 쿼리 임베딩 1회와 LLM 선택 1회.
2. 채팅·유저 요약·종목 카탈로그의 DynamoDB 테이블과 리포지토리.
3. 벡터 인덱스. 10만 건을 넘기면 HNSW 와 `hnsw.iterative_scan` 을 함께 켠다.
