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

AI 서버는 이 API의 데이터를 **루틴 추천과 챗봇 응답**에 사용한다. 쓰기·삭제 호출 없음 — 전 API 읽기 전용.

| 데이터 | 쓰는 곳 |
|---|---|
| 부상·기피 부위 | 추천 — 하드 필터, 해당 부위 타겟 운동 제외 |
| 사용자 수준 | 추천 — 숙련도 필터, 난이도 상한 |
| 운동 목적 | 추천 — 세트×렙 스킴 변형 |
| 신체 정보 | 추천 — 무게 추천 보조 (기록 없는 종목 초기값) / 챗봇 — `bmi` 차트의 키 |
| 최근 세션·세트 기록 | 추천 — 근육피로 계산(부위별 최근 부하·경과일), 종목별 무게 추천 / 챗봇 — 볼륨·밸런스·종목 순위 차트 |
| 체중 측정 이력 (§4.4) | 챗봇 — 체중 변화·BMI 차트 |
| 종목별 수행 세트 (§4.5) | 챗봇 — 종목 PR 추이 차트 |
| 세션 요약 (§4.6) | 챗봇 — 운동 시간·빈도·요일 차트 |
| 종목 수행 방법 (§4.3) | 챗봇 — 운동 가이드 답변 |

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
| 설명 | 종목 마스터 단건을 반환한다. AI 서버가 루틴 payload 구성 시 썸네일 URL 등 마스터 정보를 조회·비교(검증)하고, 챗봇 운동 가이드(`responseScheme=exerciseGif`) 답변의 수행 방법 문장을 작성하는 용도. |
| 연관 요구사항 | AI 루틴 추천 · 루틴 payload `thumbnailUrl` 채움 · 챗봇 운동 가이드 |

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
    "thumbnailUrl": "https://cdn.fitset.example/exercises/barbell-bench-press/thumb.jpg",
    "videoKey": "exercises/barbell-bench-press/guide.mp4",
    "instructions": [
      "벤치에 누워 견갑을 모으고 바를 어깨너비보다 조금 넓게 잡는다.",
      "바를 가슴 중앙까지 통제하며 내린다.",
      "가슴을 밀어내듯 바를 수직으로 밀어 올린다."
    ]
  }
}
```

응답 필드

| 필드 | 타입 | Null | 설명 | ERD 매핑 |
|---|---|---|---|---|
| `traceId` | UUID | N | 요청 추적 ID | — |
| `data.slug` | String | N | 종목 식별자 — slug | `exercise.slug` |
| `data.name` | String | N | 종목 이름 — **한글명** (metadata `name_ko` 기준) | `exercise.name` |
| `data.primaryMuscles` | Array\<String\> | N (1개 이상) | 주요 근육 — MUSCLE enum | `exercise_muscle` role=primary |
| `data.secondaryMuscles` | Array\<String\> | N (빈 배열 가능) | 보조 근육 — MUSCLE enum | `exercise_muscle` role=secondary |
| `data.equipment` | String | N | 필요 장비 — EQUIP enum | `equipment.name` |
| `data.difficulty` | String | N | 난이도 — DIFFICULTY enum | `exercise.difficulty` |
| `data.thumbnailUrl` | String | Y | 종목 썸네일 이미지 URL — 최대 500자. 미등록 시 `null` | `exercise.thumbnail_url` |
| `data.videoKey` | String | Y | 수행 영상의 **S3 오브젝트 키** — 최대 500자. 미등록 시 `null` (2026-07-29 추가) | `exercise.video_url` |
| `data.instructions` | Array\<String\> | N (빈 배열 가능) | 수행 방법 — 단계별 문장 배열 (2026-07-29 추가) | `exercise.instructions` json |

- AI 서버 사용처 ①: 루틴 응답의 `exercises[].thumbnailUrl`을 이 값으로 채우고, 보유 마스터(metadata)와 썸네일 정보를 비교·검증한다.
- AI 서버 사용처 ②: 챗봇 운동 가이드 답변의 근거 — `instructions`를 LLM 컨텍스트에 넣어 수행 방법을 설명한다. **`instructions`가 없으면 LLM이 동작 설명을 지어내게 되므로** 이 두 필드 추가가 가이드 품질의 전제 조건이다 (협의 포인트 ⑪)
- `instructions`는 ERD상 `json` 컬럼 — 와이어에서는 **문자열 배열**로 고정한다. 객체 형태(`{step, text}`)로 저장돼 있다면 백엔드가 평탄화해 내보낸다
- **`videoKey`는 완성된 URL이 아니라 S3 오브젝트 키다** (2026-07-29 확정). 영상은 비공개 버킷에 있고 재생 URL은 **AI 서버가 요청 시점에 presigned GET으로 서명**해 클라이언트 API §7 `exerciseGif` payload에 담는다. 백엔드가 미리 서명해서 주면 안 되는 이유: 서명은 1시간짜리인데 이 값은 마스터 데이터라 캐시·재사용되므로, 전달되는 동안 이미 만료된다. 위치(키)는 백엔드, 서명은 사용 시점에 AI 서버 — 책임을 나눈다
- ERD 컬럼명이 `video_url`이라 와이어 필드명(`videoKey`)과 어긋난다. 저장값이 전체 URL이면 백엔드가 버킷·도메인 프리픽스를 떼고 키만 내보낸다 — 컬럼명 정리는 협의 포인트 ⑫
- `thumbnailUrl`은 그대로 **공개 URL**이다 (목록·카드에서 다량 노출돼 서명 비용이 크고, 민감도도 낮다). 서명 대상은 영상뿐

오류 응답

| HTTP | code | 상황 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `EXERCISE_NOT_FOUND` | 존재하지 않는 slug |

## 4-B. 차트용 시계열 조회 API (2026-07-29 추가)

챗봇이 `responseScheme=chart`로 답할 때 쓰는 시계열 3종. §4.2와 목적이 다르다 — §4.2는 **최근 N일 raw**(추천용, 기본 14~28일)이고, 아래 3종은 **장기 구간(수개월)**을 얇은 행으로 받는다.

설계 원칙은 §4.2와 동일하게 유지한다: **집계는 AI 서버가 한다.** 백엔드는 원본 행을 기간·대상으로 잘라서 줄 뿐, 평균·최대·볼륨·주간 버킷을 계산하지 않는다 (계산식이 AI 서버 소유라 룰 변경 시 백엔드 수정이 없도록 — §4.2 설명과 같은 이유).

### 공통 Query Parameter

| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `from` | Date (`YYYY-MM-DD`) | N | 조회 시작일 (inclusive). 기본 = `to` − 180일 |
| `to` | Date (`YYYY-MM-DD`) | N | 조회 종료일 (inclusive). 기본 = 오늘 |
| `limit` | Integer | N | 최대 반환 행 수. 기본·상한은 API별 표기 — 초과분은 **최신순으로 잘라낸다** |

- `from` > `to` → `400 INVALID_REQUEST`
- 구간 상한(예: 2년)은 협의 포인트 ⑧
- 세 API 모두 정렬은 **시각 오름차순**(오래된 것 → 최신) — 차트 x축 순서와 같아 클라·AI 양쪽에서 재정렬이 필요 없다
- 기간 내 데이터 없음 → `200` + 빈 `items` (에러 아님)

### 4.4 체중 추이 조회

#### 기본 정보

| 항목 | 값 |
|---|---|
| Method | GET |
| URL | `/internal/users/{userId}/body-weights` |
| 권한 | 내부 서버 |
| 설명 | `body_weight_log`의 측정 이력을 기간으로 잘라 반환한다. §4.1 프로필의 `weightKg`는 **최신 1건**뿐이라 추이 차트를 그릴 수 없어 별도로 둔다. |
| 연관 요구사항 | AI 챗봇 체중 변화 차트 |
| 근거 테이블 | `body_weight_log` (ERD §유저) |

#### 요청

Path Parameter: `userId` (UUID, 필수)
Query Parameter: 공통 3종 (`limit` 기본 365 · 상한 1000)
Request Body — 없음

#### 응답

성공 응답 — 200 OK

```json
{
  "traceId": "01JXYZ",
  "data": {
    "items": [
      { "measuredAt": "2026-05-02T00:00:00Z", "weightKg": 74.8 },
      { "measuredAt": "2026-06-01T00:00:00Z", "weightKg": 73.5 },
      { "measuredAt": "2026-07-20T00:00:00Z", "weightKg": 72.4 }
    ]
  }
}
```

응답 필드

| 필드 | 타입 | Null | 설명 | ERD 매핑 |
|---|---|---|---|---|
| `data.items[].measuredAt` | DateTime | N | 측정 시각 | `body_weight_log.measured_at` |
| `data.items[].weightKg` | Number | N | 측정 체중(kg) | `body_weight_log.weight_kg` `decimal(5,2)` |

- 같은 날 복수 측정이 있어도 **합치거나 평균 내지 않는다** — 원본 행 그대로. 일 단위 정리는 AI 서버가 한다

오류

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | `from` > `to` · `limit` 범위 밖 |
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `USER_NOT_FOUND` | 존재하지 않는 userId |

### 4.5 종목별 수행 세트 조회

#### 기본 정보

| 항목 | 값 |
|---|---|
| Method | GET |
| URL | `/internal/users/{userId}/exercises/{slug}/sets` |
| 권한 | 내부 서버 |
| 설명 | 특정 종목 1개의 수행 세트를 기간으로 잘라 시계열로 반환한다. PR(개인 최고) 추이 차트용. §4.2로도 계산은 가능하나 **전 종목 raw를 수개월치 받아야 해** 페이로드가 과도하다 — 종목·기간으로 미리 좁힌다. |
| 연관 요구사항 | AI 챗봇 종목 PR 차트 |
| 근거 테이블 | `workout` × `workout_exercise` × `workout_set` × `exercise` (ERD §로그) |

#### 요청

Path Parameter

| 이름 | 타입 | 필수 | 설명 | 예시 |
|---|---|---|---|---|
| `userId` | UUID | Y | 유저 식별자 | `550e8400-...` |
| `slug` | String | Y | 종목 식별자 — `exercise.slug` | `barbell-bench-press` |

Query Parameter: 공통 3종 (`limit` 기본 500 · 상한 2000)
Request Body — 없음

#### 응답

성공 응답 — 200 OK. **세션 중첩 없이 평탄한 세트 목록**으로 준다 (세션 묶음은 `workoutId`·`performedAt`으로 AI 서버가 복원).

```json
{
  "traceId": "01JXYZ",
  "data": {
    "items": [
      {
        "workoutId": "c1d2...uuid",
        "performedAt": "2026-06-02T09:00:00Z",
        "orderIndex": 0,
        "weight": 60,
        "reps": 10,
        "durationSec": 32,
        "restSec": 90
      },
      {
        "workoutId": "c1d2...uuid",
        "performedAt": "2026-06-02T09:00:00Z",
        "orderIndex": 1,
        "weight": 62.5,
        "reps": 8,
        "durationSec": 30,
        "restSec": null
      }
    ]
  }
}
```

응답 필드

| 필드 | 타입 | Null | 설명 | ERD 매핑 |
|---|---|---|---|---|
| `data.items[].workoutId` | UUID | N | 세션 식별자 — 같은 세션 세트 묶기용 | `workout.workout_id` |
| `data.items[].performedAt` | DateTime | N | 세션 시작 시각 — 차트 x축 기준 | `workout.started_at` |
| `data.items[].orderIndex` | Integer | N | 세션 내 세트 순서 (0-based) | `workout_set.order_index` |
| `data.items[].weight` | Number | Y | 수행 중량(kg). **맨몸·미기록은 `null`** | `workout_set.weight_kg` |
| `data.items[].reps` | Integer | N | 수행 횟수 | `workout_set.reps` |
| `data.items[].durationSec` | Integer | Y | 세트 수행 시간(초). 미기록은 `null` | `workout_set.duration_sec` |
| `data.items[].restSec` | Integer | Y | 세트 후 휴식 시간(초). 마지막 세트·미기록은 `null` | `workout_set.rest_sec` |

- **`0` → `null` 변환은 백엔드 책임**: ERD상 `weight_kg`·`duration_sec`·`rest_sec`는 `NOT NULL default 0`이라 "0으로 수행"과 "미기록"이 구분되지 않는다. 응답에서는 미기록을 `null`로 내보낸다 (ERD 정합성 체크 ③·④ — 협의 포인트 ⑤와 같은 결론)
- 정렬은 `performedAt` → `orderIndex` 오름차순

오류

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | `from` > `to` · `limit` 범위 밖 |
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `USER_NOT_FOUND` | 존재하지 않는 userId |
| 404 | `EXERCISE_NOT_FOUND` | 존재하지 않는 slug — **기록이 0건인 것과 구분**(기록 0건은 200 + 빈 items) |

### 4.6 세션 요약 목록 조회

#### 기본 정보

| 항목 | 값 |
|---|---|
| Method | GET |
| URL | `/internal/users/{userId}/workout-sessions` |
| 권한 | 내부 서버 |
| 설명 | 세션 단위 메타만 **종목·세트 없이** 반환한다. 운동 시간 추이·운동 빈도 차트용. §4.2와 달리 중첩이 없어 수개월치도 가볍다. |
| 연관 요구사항 | AI 챗봇 운동 시간·빈도 차트 |
| 근거 테이블 | `workout` (+ 개수만 `workout_exercise`·`workout_set`) |

#### 요청

Path Parameter: `userId` (UUID, 필수)
Query Parameter: 공통 3종 (`limit` 기본 200 · 상한 1000)
Request Body — 없음

#### 응답

성공 응답 — 200 OK

```json
{
  "traceId": "01JXYZ",
  "data": {
    "items": [
      {
        "id": "c1d2...uuid",
        "routineId": "a9b8...uuid",
        "startedAt": "2026-07-20T09:00:00Z",
        "endedAt": "2026-07-20T10:05:00Z",
        "activeDurationSeconds": 3180,
        "exerciseCount": 5,
        "setCount": 18
      }
    ]
  }
}
```

응답 필드

| 필드 | 타입 | Null | 설명 | ERD 매핑 |
|---|---|---|---|---|
| `data.items[].id` | UUID | N | 세션 식별자 | `workout.workout_id` |
| `data.items[].routineId` | UUID | Y | 시작한 루틴 — 빈 운동이면 `null` | `workout.routine_id` |
| `data.items[].startedAt` | DateTime | N | 시작 시각 — 차트 x축 기준 | `workout.started_at` |
| `data.items[].endedAt` | DateTime | N | 종료 시각 | `workout.ended_at` |
| `data.items[].activeDurationSeconds` | Integer | N | **순수 운동 시간(초)** — 휴식·이탈 제외. 시간 차트의 기본 지표 | `workout.active_duration_seconds` |
| `data.items[].exerciseCount` | Integer | N | 수행 종목 수 | `count(workout_exercise)` |
| `data.items[].setCount` | Integer | N | 총 세트 수 | `count(workout_set)` |

- `endedAt − startedAt`(경과 시간)과 `activeDurationSeconds`(순수 운동 시간)는 다르다 — 어느 쪽을 그릴지는 AI 서버가 정한다. 두 값 모두 원본이라 집계 원칙에 어긋나지 않는다
- `exerciseCount`·`setCount`는 조인 카운트라 엄밀히는 집계지만, **행 수 세기**여서 룰이 개입하지 않는다. 이것까지 raw로 받으면 세션 중첩 전체(=§4.2)를 다시 받아야 해 API를 나눈 의미가 사라진다 (협의 포인트 ⑨)

오류

| HTTP | code | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | `from` > `to` · `limit` 범위 밖 |
| 401 | `UNAUTHORIZED` | 내부 인증 실패 (인증 도입 시) |
| 404 | `USER_NOT_FOUND` | 존재하지 않는 userId |

### 4-B.1 차트별 사용 API 매핑

AI 서버 챗봇이 그리는 차트(클라이언트 API 명세 §7 `chart` payload)와 데이터 출처.
**수록 기준**: §7 payload로 표현 가능한 것만 — `chartType`은 `line`\|`bar` 2종뿐이고 `x`는 문자열 배열, `series[].values`는 숫자 배열이다. 따라서 산점도·파이·누적영역·이중 y축(단위가 다른 두 지표 겹치기)은 대상에서 제외했다.

| # | 차트 (`metric`) | 설명 | 형태 | x축 | 사용 API | AI 서버가 하는 집계 |
|---|---|---|---|---|---|---|
| 1 | `bodyWeight` | 체중 변화 | line | 날짜 | §4.4 | 일 단위 중복 정리, 결측 구간 연결 |
| 2 | `exercisePr` | 종목 PR 추이 | line | 날짜 | §4.5 | 세션별 최대 e1RM(Epley) 또는 최대 중량 |
| 3 | `workoutDuration` | 운동 시간 변화 | line | 주차 | §4.6 | `activeDurationSeconds` → 분 변환, 주 단위 평균 |
| 4 | `workoutFrequency` | 운동 빈도 | bar | 주차 | §4.6 | 주·월 단위 세션 수 카운트 |
| 5 | `muscleVolume` | 부위별 볼륨 추이 | line | 주차 | §4.2 | `weight × reps` 합, 주동근 매핑 후 부위별·주별 합산 |
| 6 | `muscleBalance` | 부위별 볼륨 비중 | bar | 부위 한글명 | §4.2 | 구간 전체 볼륨을 주동근별로 합산 (부위 12종 중 상위 N) |
| 7 | `topExercises` | 많이 한 종목 순위 | bar | 종목 한글명 | §4.2 | 종목별 세트 수 카운트 → 내림차순 상위 N |
| 8 | `weekdayFrequency` | 요일별 운동 횟수 | bar | 월~일 | §4.6 | `startedAt` 요일 추출 후 카운트 |
| 9 | `bmi` | BMI 추이 | line | 날짜 | §4.4 + §4.1 | `weightKg / (heightM²)` — 키는 프로필에서 1회 조회 |

추가 4종(#6~#9)의 선정 근거 — **유저가 가장 자주 물을 법한 질문**에 대응한다:

| 예상 질문 | 대응 metric |
|---|---|
| "나 상하체 밸런스 어때?" · "등만 너무 많이 한 거 아냐?" | `muscleBalance` |
| "내가 제일 많이 한 운동 뭐야?" | `topExercises` |
| "나 주로 무슨 요일에 운동해?" · "주말에 안 하지?" | `weekdayFrequency` |
| "내 BMI 어떻게 변했어?" · "정상 체중이야?" | `bmi` |

- `muscleBalance`·`topExercises`는 **비중·순위라 x축이 시간이 아니다** — 시계열 3종(§4.4~§4.6)으로는 못 만들고 §4.2의 종목·세트 raw가 필요하다. §4.2 `days` 상한이 90이면 최대 3개월 구간까지 가능 (협의 포인트 ③)
- `muscleBalance`는 파이 차트가 자연스럽지만 §7에 파이가 없어 **bar(내림차순 정렬)로 표현**한다. 비중을 %로 낼지 절대 볼륨(kg)으로 낼지는 `yLabel`로 구분한다
- `bmi`는 §4.4 체중 시계열에 §4.1 프로필의 `heightCm` 1건을 곱해 만든다 — **API 추가 불필요**. 단 `heightCm`이 `null`이면 차트를 만들 수 없어 텍스트로 강등한다
- `muscleVolume`·`muscleBalance`·`topExercises`가 §4.2에 몰려 있다. 장기 구간이 필요해지면 §4.5를 종목 없이(전 종목) 확장하는 안을 검토 (협의 포인트 ⑩)

## 5. API 목록

| Method | Path | 설명 | 근거 |
|---|---|---|---|
| GET | `/internal/users/{userId}/profile` | 유저 프로필 조회 | AI 루틴 추천 |
| GET | `/internal/users/{userId}/workouts` | 최근 운동 기록 조회 (raw) | AI 루틴 추천 · 부위별 볼륨 차트 |
| GET | `/internal/exercises/{slug}` | 종목 정보 조회 (썸네일 포함) | 루틴 payload 썸네일 채움·검증 · 운동 가이드 |
| GET | `/internal/users/{userId}/body-weights` | 체중 추이 조회 | 챗봇 체중 변화 차트 (§4.4) |
| GET | `/internal/users/{userId}/exercises/{slug}/sets` | 종목별 수행 세트 조회 | 챗봇 종목 PR 차트 (§4.5) |
| GET | `/internal/users/{userId}/workout-sessions` | 세션 요약 목록 조회 | 챗봇 운동 시간·빈도 차트 (§4.6) |

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
| ⑦ | 순서 컬럼 0-based | ~~`position`·`set_number`(1-based) 혼재~~ → **해소(2026-07-29 ERD 갱신)**: 4개 테이블 전부 `order_index`(0-based)로 통일되어 내부·클라이언트 API의 `orderIndex`와 무변환 대응. 1-based 가정 코드가 남아있는지만 확인 |
| ⑧ | 시계열 API 구간 상한 | §4.4~§4.6의 `from`~`to` 최대 폭. 2년 제안 — 초과 시 `400`으로 막을지, 조용히 잘라낼지 |
| ⑨ | §4.6 카운트 필드 | `exerciseCount`·`setCount`는 조인 카운트라 "집계는 AI 서버" 원칙의 예외다. 백엔드가 내주는 게 맞는지, 아니면 필드를 빼고 필요 시 §4.2를 쓸지 |
| ⑩ | 전 종목 세트 시계열 | `muscleBalance`·`topExercises`·`muscleVolume`이 §4.2(최근 N일)에 묶여 장기 구간을 못 그린다. §4.5를 `slug` 없이 전 종목으로 확장할지(페이로드 급증) vs §4.2 `days` 상한을 올릴지 |
| ⑪ | §4.3 `instructions`·`videoKey` | 챗봇 운동 가이드 답변의 근거. ERD `exercise.instructions`(json)·`video_url`은 이미 `NOT NULL`이나 현 §4.3 응답에 미포함 — **추가 필요**. `instructions` json의 실제 저장 형태(문자열 배열 vs 객체 배열) 확인 후 와이어는 문자열 배열로 평탄화 |
| ⑫ | 영상 저장 위치·키 규약 | ① 가이드 영상이 실제로 비공개 S3에 있는지(공개 CDN이면 presign 자체가 불필요) ② 키 규약 `exercises/{slug}/guide.mp4` 확정 ③ ERD 컬럼 `video_url`이 전체 URL을 담고 있다면 키만 내보내는 변환을 백엔드가 할지, 컬럼을 `video_key`로 바꿀지 |

## 8. 변경 이력

| 일자 | 내용 | 작성자 |
|---|---|---|
| 2026-07-22 | 초안 작성 — 프로필·운동 기록 내부 조회 2종 제안 | @이주한 |
| 2026-07-22 | 내부 API 버저닝 제거(Base URL `/internal`) · 세트에 `durationSec`·`restSec` 추가(협의 포인트 ⑤ 요청사항으로 확정) | @이주한 |
| 2026-07-22 | enum을 팀 카탈로그 확정값으로 교체(GOAL/DIFFICULTY/MUSCLE, camelCase key) · EQUIP 참고 수록 | @이주한 |
| 2026-07-22 | 내부 목록 응답에서 `page` 객체 제거 — 커서 미사용 확정, 필요 시 하위호환 추가로 도입 | @이주한 |
| 2026-07-25 | §4.3 종목 정보 조회 API 추가 (thumbnailUrl 포함) · 종목 식별자=slug, name=한글명 확정 반영(협의 포인트 ④ 종결) | @이주한 |
| 2026-07-25 | §4.3 필드명 `exerciseId` → `slug`로 변경 (경로·응답 모두) · §4.2 기록 응답의 종목 참조 필드명도 `slug`로 통일 (04 명세 협의 필요) | @이주한 |
| 2026-07-29 | 챗봇 차트용 시계열 API 3종 신설 — §4.4 체중 추이 · §4.5 종목별 수행 세트 · §4.6 세션 요약. 공통 `from`/`to`/`limit` 규약과 "집계는 AI 서버" 원칙 명시 (협의 포인트 ⑧⑨⑩) | @이주한 |
| 2026-07-29 | §4.3에 `videoKey`·`instructions` 추가 — 챗봇 운동 가이드 답변 근거 (협의 포인트 ⑪) · 응답 필드표에 ERD 매핑 열 추가 | @이주한 |
| 2026-07-29 | §4.3 영상 필드를 `videoUrl` → **`videoKey`(S3 오브젝트 키)**로 변경 — presigned GET 서명은 AI 서버가 요청 시점에 수행(백엔드 선서명 시 전달 중 만료). 키 규약·컬럼명 정리는 협의 포인트 ⑫ | @이주한 |
| 2026-07-29 | §4-B.1 차트 매핑 표 신설 — 총 9종 metric과 사용 API·집계 책임 정리. ERD 갱신(`order_index` 0-based 통일) 반영해 협의 포인트 ⑦ 종결 | @이주한 |
