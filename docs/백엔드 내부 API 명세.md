# 내부 API 명세 (Internal · 유저 데이터 조회)

## 1. 문서 정보

| 항목 | 내용 |
|---|---|
| 프로젝트명 | FitSet |
| 문서 범위 | 내부 API — 유저 프로필·운동 기록·종목 정보 조회 (AI 루틴 추천용) |
| API 버전 | 미사용 — 내부 API는 버저닝 없음 (소비자가 AI 서버뿐이라 계약 변경은 배포 협의로 관리) |
| Base URL | 환경별 호스트 + `/internal` |
| 호출 주체 | AI 챗봇 서버 (서버간 내부망 호출 · 읽기 전용) |
| 인증 방식 | 미정 — 협의 포인트 ① (내부망 무인증 or 내부 고정 토큰) |
| Content-Type | `application/json` (성공·오류 공통) |
| 문서 상태 | **초안 · 백엔드 협의 전** |
| 참조 문서 | [01. API 설계 규약](https://asmhangang.atlassian.net/wiki/spaces/FIT/pages/11141148) · [04. 워크아웃 API 명세](https://asmhangang.atlassian.net/wiki/spaces/FIT/pages/11468801) |
| 최종 수정일 | 2026년 7월 25일 |
| 작성자 | @이주한 |

## 2. 공통 규칙

전체 공통 계약은 **01. API 설계 규약**을 정본으로 한다. 외부(앱) API와 다른 점만 아래에 명시한다.

### 2.1 요청 헤더

```
Content-Type: application/json
X-Internal-Token: {token}   ← 인증 방식 확정 시 (협의 포인트 ①)
```

- 사용자 Access Token 없음 — 서버간 호출이므로 대상 유저는 Path Parameter `userId`로 지정한다.

### 2.2 데이터 형식

| 항목 | 규칙 |
|---|---|
| 날짜·시간 | ISO 8601 UTC · `2026-07-10T06:30:00Z` |
| 식별자 | UUID 문자열 |
| JSON 프로퍼티 | camelCase |
| 성공 응답 | 모든 응답에 `traceId` 포함 · 성공은 `data`, 실패는 `error` |
| 오류 응답 | 간소화 오류 응답 (01 규약 §4) |

### 2.3 사용 목적 (참고)

AI 서버는 이 API의 데이터를 루틴 추천에만 사용한다. 쓰기·삭제 호출 없음.

| 데이터 | 추천에서 쓰는 곳 |
|---|---|
| 부상·기피 부위 | 하드 필터 — 해당 부위 타겟 운동 제외 |
| 사용자 수준 | 숙련도 필터 — 난이도 상한 |
| 운동 목적 | 세트×렙 스킴 변형 |
| 신체 정보 | 무게 추천 보조 (기록 없는 종목 초기값) |
| 최근 세션·세트 기록 | 근육피로 계산(부위별 최근 부하·경과일), 종목별 무게 추천 |

## 3. 공통 응답 형식

### 3.1 단건 성공 응답

```json
{
  "traceId": "01JXYZ",
  "data": {
    "level": "intermediate",
    "goal": "hypertrophy"
  }
}
```

### 3.2 목록 성공 응답

```json
{
  "traceId": "01JXYZ",
  "data": {
    "items": [
      { "id": "550e8400-e29b-41d4-a716-446655440000", "startedAt": "2026-07-10T15:00:00Z" }
    ]
  }
}
```

- 01 규약의 목록 형식과 달리 **`page` 객체 없음** — 기간 한정 조회에 소비자가 AI 서버 하나뿐이라 커서 페이지네이션을 쓰지 않는다. 추후 필요 시 `page` 필드 추가는 하위호환 변경이므로 그때 도입한다.

### 3.3 오류 응답

```json
{
  "traceId": "01JXYZ",
  "error": {
    "code": "USER_NOT_FOUND",
    "message": "사용자를 찾을 수 없습니다.",
    "details": []
  }
}
```

## 4. API 상세 명세

### 4.1 유저 프로필 조회

#### 기본 정보

| 항목 | 값 |
|---|---|
| Method | GET |
| URL | `/internal/users/{userId}/profile` |
| 권한 | 내부 서버 |
| 설명 | 온보딩·신체정보에서 수집된 유저 프로필을 반환한다. AI 서버가 추천 요청 처리 시마다 1회 호출. |
| 연관 요구사항 | AI 루틴 추천 |

#### 요청

Path Parameter

| 이름 | 타입 | 필수 | 설명 | 예시 |
|---|---|---|---|---|
| `userId` | UUID | Y | 유저 식별자 | `550e8400-...` |

Query Parameter — 없음
Request Body — 없음

#### 응답

성공 응답 — 200 OK

```json
{
  "traceId": "01JXYZ",
  "data": {
    "heightCm": 175.0,
    "weightKg": 72.4,
    "gender": "MALE",
    "birthDate": "1995-04-12",
    "goal": "hypertrophy",
    "level": "intermediate",
    "avoidBodyParts": ["shoulders", "forearms"]
  }
}
```

응답 필드

| 필드 | 타입 | Null | 설명 |
|---|---|---|---|
| `traceId` | UUID | N | 요청 추적 ID |
| `data.heightCm` | Number | Y | 키 (cm) |
| `data.weightKg` | Number | Y | **최신** 몸무게 (kg) — 추이 전체는 불필요 |
| `data.gender` | String | Y | `MALE` \| `FEMALE` |
| `data.birthDate` | Date | Y | 생년월일 `YYYY-MM-DD` |
| `data.goal` | String | Y | 운동 목적 — GOAL enum (§4.1 하단) |
| `data.level` | String | Y | 사용자 수준 — DIFFICULTY enum (§4.1 하단) |
| `data.avoidBodyParts` | Array\<String\> | N (빈 배열 가능) | 부상·기피 부위 — MUSCLE enum 배열 (§4.1 하단) |

- Null 허용 필드는 유저 미입력 시 `null` — 기본값 처리는 AI 서버 책임.

Enum — **팀 enum 카탈로그 확정값** (key는 camelCase 소문자, 라벨은 표시용)

| enum | key (라벨) |
|---|---|
| GOAL | `hypertrophy`(근비대) · `weightLoss`(체중감량) · `strength`(근력향상) · `endurance`(체력유지) |
| DIFFICULTY | `beginner`(초급) · `intermediate`(중급) · `advanced`(고급) |
| MUSCLE | `back`(등) · `biceps`(이두근) · `calves`(종아리) · `chest`(가슴) · `core`(코어) · `forearms`(전완근) · `glutes`(둔근) · `hamstrings`(햄스트링) · `quadriceps`(대퇴사두근) · `shoulders`(어깨) · `trapezius`(승모근) · `triceps`(삼두근) |
| EQUIP (참고) | `band`(밴드) · `barbell`(바벨) · `bodyweight`(맨몸) · `cableMachine`(케이블 머신) · `dumbbell`(덤벨) · `kettlebell`(케틀벨) · `machine`(머신) — 본 API 응답엔 미포함, 장비 필터 도입 시 사용 (협의 포인트 ⑥) |

- `gender`(`MALE`/`FEMALE`)만 카탈로그에 없어 코드값 미확정 — 협의 포인트 ②

오류 응답

| HTTP | code | 상황 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `USER_NOT_FOUND` | 존재하지 않는 userId |

### 4.2 최근 운동 기록 조회

#### 기본 정보

| 항목 | 값 |
|---|---|
| Method | GET |
| URL | `/internal/users/{userId}/workouts` |
| 권한 | 내부 서버 |
| 설명 | 최근 N일 이내 커밋된 세션을 종목·세트 포함 전체 구조(raw)로 최신순 반환한다. 집계는 AI 서버가 수행한다(피로도·볼륨 계산식이 AI 서버 룰 소유이므로 룰 변경 시 백엔드 수정이 없도록). |
| 연관 요구사항 | AI 루틴 추천 |

#### 요청

Path Parameter

| 이름 | 타입 | 필수 | 설명 | 예시 |
|---|---|---|---|---|
| `userId` | UUID | Y | 유저 식별자 | `550e8400-...` |

Query Parameter

| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `days` | Integer | N (기본 14) | 오늘부터 과거 N일 이내 세션. 상한 협의 — 협의 포인트 ③ |

Request Body — 없음

#### 응답

성공 응답 — 200 OK. 항목 구조는 **04. 워크아웃 API 명세 §4.3 세션 상세**와 동일하되, 세트에 `durationSec`·`restSec` 2필드가 추가된다 (협의 포인트 ⑤ — 04 명세의 커밋·수정 API에도 저장 필드 추가 필요).

```json
{
  "traceId": "01JXYZ",
  "data": {
    "items": [
      {
        "id": "c1d2...uuid",
        "startedAt": "2026-07-20T09:00:00Z",
        "endedAt": "2026-07-20T10:05:00Z",
        "exercises": [
          {
            "id": "le1...uuid",
            "slug": "barbell-bench-press",
            "exerciseName": "바벨 벤치프레스",
            "orderIndex": 0,
            "sets": [
              { "id": "s1...uuid", "orderIndex": 0, "weight": 60, "reps": 10, "durationSec": 32, "restSec": 90 },
              { "id": "s2...uuid", "orderIndex": 1, "weight": 60, "reps": 10, "durationSec": 37, "restSec": 90 },
              { "id": "s3...uuid", "orderIndex": 2, "weight": 55, "reps": 9, "durationSec": 31, "restSec": null }
            ]
          }
        ]
      }
    ]
  }
}
```

응답 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `traceId` | UUID | 요청 추적 ID |
| `data.items` | Array | 세션 목록 (startedAt 최신순) |
| `data.items[].id` | UUID | 세션 식별자 |
| `data.items[].startedAt` | DateTime | 시작 시각 — 근육피로 경과일 계산 기준 |
| `data.items[].endedAt` | DateTime | 종료 시각 |
| `data.items[].exercises` | Array | 운동기록 목록 (orderIndex 오름차순) |
| `data.items[].exercises[].id` | UUID | 식별자 |
| `data.items[].exercises[].slug` | String | 마스터 종목 참조 — slug (협의 포인트 ④ 확정). 04 워크아웃 명세의 `exerciseId` 필드명도 `slug`로 변경 협의 필요 |
| `data.items[].exercises[].exerciseName` | String | 종목 이름 (표시·디버깅용, 로직은 ID만 사용) |
| `data.items[].exercises[].orderIndex` | Integer | 수행 순서 |
| `data.items[].exercises[].sets` | Array | 세트 목록 (orderIndex 오름차순) |
| `data.items[].exercises[].sets[].id` | UUID | 식별자 |
| `data.items[].exercises[].sets[].orderIndex` | Integer | 수행 순서 |
| `data.items[].exercises[].sets[].weight` | Number | 수행 중량(kg) — 맨몸 운동 처리 방식 협의 포인트 ⑤ |
| `data.items[].exercises[].sets[].reps` | Integer | 수행 횟수 |
| `data.items[].exercises[].sets[].durationSec` | Integer (Null 허용) | 세트 수행 시간(초). 미기록 세트는 `null` |
| `data.items[].exercises[].sets[].restSec` | Integer (Null 허용) | 세트 후 휴식 시간(초). 마지막 세트·미기록은 `null` |

- 기간 내 기록 없음 → 200 + 빈 `items` (에러 아님)
- `page` 객체 없음 — 커서 페이지네이션 미사용 (§3.2)

오류 응답

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | `days` 음수·상한 초과 등 (`error.details` 동반) |
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `USER_NOT_FOUND` | 존재하지 않는 userId |

### 4.3 종목 정보 조회

#### 기본 정보

| 항목 | 값 |
|---|---|
| Method | GET |
| URL | `/internal/exercises/{slug}` |
| 권한 | 내부 서버 |
| 설명 | 종목 마스터 단건을 반환한다. AI 서버가 루틴 payload 구성 시 썸네일 URL 등 마스터 정보를 조회·비교(검증)하는 용도. |
| 연관 요구사항 | AI 루틴 추천 · 루틴 payload `thumbnailUrl` 채움 |

#### 요청

Path Parameter

| 이름 | 타입 | 필수 | 설명 | 예시 |
|---|---|---|---|---|
| `slug` | String | Y | 종목 식별자 — **필드명도 `slug`로 통일** (2026-07-25 확정, UUID·exerciseId 표기 미사용) | `barbell-bench-press` |

Query Parameter — 없음
Request Body — 없음

#### 응답

성공 응답 — 200 OK

```json
{
  "traceId": "01JXYZ",
  "data": {
    "slug": "barbell-bench-press",
    "name": "바벨 벤치프레스",
    "primaryMuscles": ["chest"],
    "secondaryMuscles": ["shoulders", "triceps"],
    "equipment": "barbell",
    "difficulty": "intermediate",
    "thumbnailUrl": "https://cdn.fitset.example/exercises/barbell-bench-press/thumb.jpg"
  }
}
```

응답 필드

| 필드 | 타입 | Null | 설명 |
|---|---|---|---|
| `traceId` | UUID | N | 요청 추적 ID |
| `data.slug` | String | N | 종목 식별자 — slug |
| `data.name` | String | N | 종목 이름 — **한글명** (metadata `name_ko` 기준) |
| `data.primaryMuscles` | Array\<String\> | N (1개 이상) | 주요 근육 — MUSCLE enum |
| `data.secondaryMuscles` | Array\<String\> | N (빈 배열 가능) | 보조 근육 — MUSCLE enum |
| `data.equipment` | String | N | 필요 장비 — EQUIP enum |
| `data.difficulty` | String | N | 난이도 — DIFFICULTY enum |
| `data.thumbnailUrl` | String | Y | 종목 썸네일 이미지 URL — 최대 500자. 미등록 시 `null` |

- AI 서버 사용처: 루틴 응답의 `exercises[].thumbnailUrl`을 이 값으로 채우고, 보유 마스터(metadata)와 썸네일 정보를 비교·검증한다.

오류 응답

| HTTP | code | 상황 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `EXERCISE_NOT_FOUND` | 존재하지 않는 slug |

## 5. API 목록

| Method | Path | 설명 | 근거 |
|---|---|---|---|
| GET | `/internal/users/{userId}/profile` | 유저 프로필 조회 | AI 루틴 추천 |
| GET | `/internal/users/{userId}/workouts` | 최근 운동 기록 조회 (raw) | AI 루틴 추천 |
| GET | `/internal/exercises/{slug}` | 종목 정보 조회 (썸네일 포함) | 루틴 payload 썸네일 채움·검증 |

## 6. 에러 코드

- 기존 코드: `INVALID_REQUEST` · `UNAUTHORIZED` — 01. API 설계 규약 §4의 코드를 그대로 쓴다.
- 신규 제안: `USER_NOT_FOUND` — 내부 API는 Access Token이 아닌 Path의 userId로 대상을 지정하므로, 대상 부재를 표현할 코드가 필요하다 (01 카탈로그 추가 협의).
- 신규 제안: `EXERCISE_NOT_FOUND` — §4.3 종목 조회의 대상 부재 표현.

## 7. 협의 포인트

| # | 항목 | 내용 |
|---|---|---|
| ① | 내부 인증 | 내부망 무인증인지, 내부 고정 토큰(`X-Internal-Token`) 등을 둘지 |
| ② | enum 코드값 | ~~goal/level/bodyPart~~ → 팀 enum 카탈로그(GOAL/DIFFICULTY/MUSCLE/EQUIP)로 확정 반영(2026-07-22). `gender` 코드값만 미확정 |
| ③ | `days` 상한 | 기본 14 제안, 최대치(예: 90) — 종목별 마지막 수행이 14일 밖일 수 있어 여유 필요 |
| ④ | 종목 식별자 체계 | ~~UUID 여부~~ → **확정(2026-07-25): 종목 식별자 = slug, 필드명도 `slug`로 통일, `name` = 한글명(metadata `name_ko`)**. 매핑 테이블 불필요 |
| ⑤ | 세트 스키마 확장 | **요청사항(확정)**: 본 내부 API 응답에 세트별 `durationSec`·`restSec` 포함 — 무게 추천·강도 판단에 사용. 현 04. 워크아웃 명세의 세트 필드는 `weight`·`reps`뿐이므로 커밋(POST)·수정(PUT) API와 저장 스키마에 두 필드 추가가 선행되어야 함(앱 UI에는 이미 존재하는 값). 소급 불가한 과거 기록은 `null`. 맨몸 운동의 `weight` 표현(null vs 0)은 협의 필요 |
| ⑥ | 보유 장비 | user_equipment 데이터가 백엔드에 존재하는지. 없으면 장비 필터는 v1 제외 또는 추천 요청 파라미터로 대체 |

## 8. 변경 이력

| 일자 | 내용 | 작성자 |
|---|---|---|
| 2026-07-22 | 초안 작성 — 프로필·운동 기록 내부 조회 2종 제안 | @이주한 |
| 2026-07-22 | 내부 API 버저닝 제거(Base URL `/internal`) · 세트에 `durationSec`·`restSec` 추가(협의 포인트 ⑤ 요청사항으로 확정) | @이주한 |
| 2026-07-22 | enum을 팀 카탈로그 확정값으로 교체(GOAL/DIFFICULTY/MUSCLE, camelCase key) · EQUIP 참고 수록 | @이주한 |
| 2026-07-22 | 내부 목록 응답에서 `page` 객체 제거 — 커서 미사용 확정, 필요 시 하위호환 추가로 도입 | @이주한 |
| 2026-07-25 | §4.3 종목 정보 조회 API 추가 (thumbnailUrl 포함) · 종목 식별자=slug, name=한글명 확정 반영(협의 포인트 ④ 종결) | @이주한 |
| 2026-07-25 | §4.3 필드명 `exerciseId` → `slug`로 변경 (경로·응답 모두) · §4.2 기록 응답의 종목 참조 필드명도 `slug`로 통일 (04 명세 협의 필요) | @이주한 |
