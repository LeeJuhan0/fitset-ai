# 클라이언트 API 명세 (AI Routine & Chat)

정본: [09. AI 서비스 클라이언트 API 명세](https://asmhangang.atlassian.net/wiki/spaces/FIT/pages/27230212) (Confluence, 초안 2026-07-24).
이 문서는 서버 구현 관점 요약 — 충돌 시 Confluence가 우선.

## 공통

- Base URL: `https://api.fitset.kro.kr/ai/v1` (예: `https://api.fitset.kro.kr/ai/v1/threads`) — 단일 호스트 `api.fitset.kro.kr`에서 ALB 리스너 규칙이 Host 헤더 `api.fitset.kro.kr` + 경로 `/ai/*`를 AI 서버 대상 그룹으로 라우팅하고, 코드가 `/ai/v1` 프리픽스로 버저닝한다 (2026-08-02 전환, 기존 호스트 분리안 폐기). 나머지 경로는 백엔드로 간다. 호출 주체는 앱 클라이언트
- 인증: `Authorization: Bearer {accessToken}` — **API Gateway 미사용, ELB는 TLS 종료만** 담당하고 각 수신 서버가 SSM 공개키(RS256)로 JWT를 직접 검증해 `sub`를 userId로 사용 (비즈니스 규칙 §9, 2026-07-25 확정). 클라가 userId를 보내는 API는 없음 (사칭 방지)
- 응답: 팀 규약 `{traceId, data}` / `{traceId, error{code, message, details}}`
- 목록 응답은 `data.items`, `page` 객체 없음 (스레드 최대 5개·메시지 전체 로드라 불필요)
- LLM 포함 API(루틴 생성, 채팅)는 클라 타임아웃 30s 권장
- 와이어는 camelCase (`responseScheme`, `exerciseGif`) — 내부 DB는 snake_case, 표현 계층에서 변환

## API 목록

| Method | Path | 설명 | 성공 |
| --- | --- | --- | --- |
| POST | `/ai/v1/routines` | AI 루틴 생성 (홈 고정 워크로드) | 200 |
| GET | `/ai/v1/threads` | 스레드 목록 (최대 5, 최근 활동순, 만료 제외) | 200 |
| POST | `/ai/v1/threads` | 스레드 생성 (5개 초과 시 최구 활동 스레드 삭제 후 생성) | 201 |
| DELETE | `/ai/v1/threads/{threadId}` | 스레드+소속 메시지 전체 삭제, 복구 불가 | 204 |
| GET | `/ai/v1/threads/{threadId}/messages` | 대화 전체 시간순 조회 (payload 포함) | 200 |
| POST | `/ai/v1/threads/{threadId}/messages` | 메시지 전송 → 챗봇 응답 (MVP 동기 JSON 1회) | 200 |
| GET | `/ai/v1/exercises/{slug}/video` | 운동 가이드 영상 presigned URL 재발급 | 200 |

## 1. POST /ai/v1/routines

홈 "AI 루틴 생성" 화면 입력값만 받는다. 신체 정보·**운동 목적(goal)**·기록은 서버가 내부 API(08)로 조회 — **goal은 요청에서 제거, 프로필 조회값 사용 (2026-07-29 확정)**. 대화 스레드를 생성하지 않는다.

요청:

```json
{
  "level": "intermediate",        // DIFFICULTY enum: beginner|intermediate|advanced (필수)
  "muscleGroups": ["chest", "shoulders", "triceps"],  // MUSCLE enum, 1~12개 (필수)
  "minutes": 50,                  // 10~180 (필수)
  "context": "어깨 통증 있음",     // 자유 텍스트 최대 200자 (선택) — LLM 조건 추출용
  "includeWarmup": true           // 워밍업 세트 포함 여부 (필수) — 종목별 첫 세트 경량 구성
}
```

응답 `data.routine`:

```json
{
  "slug": "upper-body-dumbbell-50",   // 베이스 루틴 참조 — LLM 재구성 시에도 원본 후보 slug 유지 (nullable)
  "name": "상체 집중 50분",
  "estimatedMinutes": 50,
  "exercises": [
    {
      "slug": "dumbbell-bench-press",   // 종목 식별자 = slug (uuid 미사용, 2026-07-25 확정)
      "exerciseName": "덤벨 벤치프레스",  // 한글명 (종목 마스터 name_ko)
      "thumbnailUrl": "https://cdn.fitset.example/exercises/dumbbell-bench-press/thumb.jpg",  // 최대 500자, 미등록 null
      "orderIndex": 0,
      "sets": [ { "orderIndex": 0, "weight": 12.0, "reps": 15 } ]
    }
  ]
}
```

비즈니스 규칙:

1. 프로필 `avoidBodyParts`는 하드 필터로 적용하되 **요청 `muscleGroups`와 겹치는 부위는 클라 요청 우선**(실효 기피 = `avoidBodyParts − muscleGroups`, 2026-07-25 확정). 기피(제외) 정보는 **요청 필드가 아니라 내부 API 프로필 조회값**이며 `null`(미설정)일 수 있음 — 비즈니스 규칙 §5의 "제외 운동"은 이 경로로 흡수
2. 세트 `weight`는 최근 기록 기반 무게 추천값, 기록 없으면 신체 정보 기반 초기값. 맨몸은 `null`
3. 파이프라인: 후보 룰 필터 → 룰 점수 정렬 → LLM 최종 구성
4. `exercises[]`는 04 명세 루틴 구조와 호환 — 클라가 그대로 백엔드 `POST /routines`(루틴 저장)에 사용. AI 서버는 쓰기 없음
5. **goal은 요청 필드가 아니다** (2026-07-29 확정) — 내부 API 프로필(08 §4.1 `data.goal`)을 쓴다. 프로필 미입력(`null`)이면 `hypertrophy` 기본(기본값 처리는 AI 서버 책임 — 08 §4.1 규약). 홈 화면은 목적 선택 UI를 두지 않는다

오류: 400 `INVALID_REQUEST` / 401 / 404 `USER_NOT_FOUND` / 409 `NO_ROUTINE_CANDIDATE` / 429 `RATE_LIMITED` (유저별 분당 상한, 2026-07-29) / 503 `AI_UNAVAILABLE`

## 2. GET /ai/v1/threads

```json
{ "items": [ { "threadId": "oid", "title": "어깨 재활 루틴 상담", "lastMessageAt": "2026-07-23T09:12:00Z" } ] }
```

- `title`: 첫 발화 기반 자동 생성, 첫 메시지 전이면 `null`. `lastMessageAt`: 빈 스레드면 `null`

## 3. POST /ai/v1/threads

본문 없음 → `201` `{ "threadId": "oid", "createdAt": "..." }`

- 유저당 활성 스레드 최대 5개, 초과 시 마지막 활동이 가장 오래된 스레드 삭제 후 생성 (협의 포인트 ③: 409 반환 대안 논의 중)

## 4. DELETE /ai/v1/threads/{threadId}

스레드 1건과 **소속 메시지 전체**를 삭제한다. 복구 불가 — 클라는 삭제 전 확인 다이얼로그 필수.

Path Parameter:

| 이름 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `threadId` | String (ULID) | Y | 삭제 대상 스레드 — `GET /ai/v1/threads`의 `threadId` |

요청 본문 없음.

응답: `204 No Content` — **본문 없음**. traceId는 `{traceId, data}` 대신 `X-Trace-Id` 응답 헤더로 전달 (전 응답 공통, `main.py` traceId 미들웨어)

비즈니스 규칙:

1. **소유권**: 대상은 JWT `sub`(userId)의 스레드로 한정. 클라는 userId를 보내지 않는다(§공통) — 서버가 `chat_threads` PK `(user_id, thread_id)`로 직접 조회
2. **삭제 범위·순서**: `chat_threads` 항목 삭제 → `chat_messages` Query(threadId) → BatchWriteItem 25개 단위 삭제 (document-structure 삭제 경로 ①). 메시지 삭제가 부분 실패해도 **스레드 항목이 지워졌으면 204** — 남은 고아 메시지는 일 1회 배치가 정리(삭제 경로 ②)하므로 클라 재시도 불필요
3. **만료 스레드**: `expires_at < now`(TTL 지연으로 항목이 남아있는 경우)는 조회 단계에서 부재로 간주 → `404 THREAD_NOT_FOUND`
4. **자동 삭제와 무관**: 스레드 5개 초과 시 LRU 자동 삭제(§3)는 서버 내부 처리이며 이 API를 거치지 않는다

오류:

| HTTP | code | 상황 |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | JWT 없음·서명 검증 실패·만료·`type != access` |
| 403 | `THREAD_FORBIDDEN` | 타 유저 스레드 접근 — **현 스키마에선 도달 불가**, 아래 주석 참조 |
| 404 | `THREAD_NOT_FOUND` | 스레드 없음 · 이미 삭제됨 · TTL 만료 |

- `403`은 `chat_threads` PK가 `(user_id, thread_id)` 복합키라 **남의 스레드 조회 결과가 "부재"와 구분되지 않는다** — 타 유저 threadId를 넣어도 GetItem이 비어 404가 된다. 존재 여부 노출 방지 측면에선 404가 오히려 안전하므로, 403은 계약상 예약만 하고 실제로는 내보내지 않는다 (§9 협의 포인트 ⑨)
- 재시도 시 두 번째 호출이 404가 되는 점(비멱등)도 §9 협의 포인트 ⑨

## 5. GET /ai/v1/threads/{threadId}/messages

```json
{
  "items": [
    { "messageId": "oid", "role": "user", "content": "...", "responseScheme": null, "payload": null, "createdAt": "..." },
    { "messageId": "oid", "role": "assistant", "content": "...", "responseScheme": "routine", "payload": { }, "createdAt": "..." }
  ]
}
```

- user 메시지는 `responseScheme`·`payload` 모두 `null`
- 오류: 401 / 403 / 404 (스레드 없음·만료)

## 6. POST /ai/v1/threads/{threadId}/messages

요청: `{ "content": "이 루틴에서 스쿼트 빼줘" }` (1~1000자)

응답:

```json
{
  "message": {
    "messageId": "oid", "role": "assistant", "content": "...",
    "responseScheme": "routine", "payload": { }, "createdAt": "..."
  },
  "threadTitle": "하체 루틴 조정"   // 제목 생성·변경 시에만 값, 이외 null — 목록 화면 갱신용
}
```

오류: 400(길이) / 401 / 403 / 404(→ 클라는 새 스레드 생성 유도) / 409 `THREAD_FULL`(스레드당 메시지 상한 도달 → 새 스레드 생성 유도, 2026-07-29) / 429 `RATE_LIMITED`(유저별 분당 상한) / 503 `AI_UNAVAILABLE`

## 6-B. GET /ai/v1/exercises/{slug}/video

운동 가이드 영상의 **presigned URL 재발급**. `exerciseGif` payload의 URL이 만료됐을 때 클라가 호출한다.

Path Parameter:

| 이름 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `slug` | String | Y | 종목 식별자 — payload의 `slug` 그대로 |

요청 본문 없음. 응답:

```json
{
  "traceId": "01JXYZ",
  "data": {
    "slug": "barbell-bench-press",
    "exerciseName": "바벨 벤치프레스",
    "videoUrl": "https://fitset-media.s3.ap-northeast-2.amazonaws.com/exercises/...?X-Amz-Signature=...",
    "expiresAt": "2026-07-29T12:20:00Z"
  }
}
```

- 응답 구조는 `exerciseGif` payload와 **동일** — 클라가 payload 자리에 그대로 갈아끼울 수 있다
- 서명만 새로 만드는 호출이라 LLM을 타지 않는다. 30s 타임아웃 권장 대상 아님
- 스레드·메시지와 무관한 종목 단위 조회지만, 액세스 토큰 인증은 동일하게 요구한다
- 오류: 401 / 404 `EXERCISE_NOT_FOUND`(없는 slug) / 404 `VIDEO_NOT_FOUND`(종목은 있으나 영상 미등록)

## 7. responseScheme · payload 계약

`responseScheme`이 payload의 존재·구조를 완전히 결정한다. `text`인데 payload가 있거나 `chart`인데 없으면 서버 버그.

| responseScheme | content | payload |
| --- | --- | --- |
| `text` | 답변 전문 | 항상 `null` (키는 유지) |
| `chart` | 차트 설명 문장 | 아래 chart payload |
| `exerciseGif` | 운동 설명 | 아래 exerciseGif payload — **S3 presigned URL 직접 포함** (2026-07-29 변경) |
| `routine` | 추천 이유 | §1 `data.routine`과 동일 구조 — 추천 시점 스냅샷 (원본 수정·삭제와 무관하게 보존) |

exerciseGif payload:

```json
{
  "slug": "barbell-bench-press",
  "exerciseName": "바벨 벤치프레스",
  "videoUrl": "https://fitset-media.s3.ap-northeast-2.amazonaws.com/exercises/barbell-bench-press/guide.mp4?X-Amz-Signature=...",
  "expiresAt": "2026-07-29T11:20:00Z"
}
```

- `videoUrl`은 **AI 서버가 발급한 S3 presigned GET URL**이다 (유효 1시간). 종전처럼 클라가 종목 상세 API로 따로 받아오지 않는다
- **저장된 대화를 다시 열면 이 URL은 이미 만료돼 있다** — 메시지는 영구 보존인데 서명은 1시간짜리다. 클라는 `expiresAt`이 지났거나 재생이 403으로 실패하면 §6-B 재발급 API를 호출해 새 URL을 받는다. `payload`를 덮어쓸 필요는 없다(표시용 임시 값)
- 영상이 없는 종목이면 `videoUrl`·`expiresAt`이 `null` — 클라는 썸네일·텍스트만 보여준다
- `slug`·`exerciseName`은 그대로 유지 — 재발급 호출 키이자 종목 상세 화면 이동용

chart payload:

```json
{
  "chartType": "line",              // line | bar — 색·스타일은 클라 소유, 서버는 데이터만
  "metric": "muscleVolume",         // 아래 9종 — 클라가 축 포맷·단위 표기를 분기할 때 사용 (표시 문구는 title/xLabel/yLabel이 정본)
  "title": "최근 4주 가슴 볼륨",
  "xLabel": "주차", "yLabel": "볼륨(kg)",
  "x": ["6/29", "7/6", "7/13", "7/20"],
  "series": [ { "name": "가슴", "values": [4200, 4650, 4400, 5100] } ]
}
```

- `x`와 각 `series[].values`는 **길이가 같다**. 결측 구간은 `null`이 아니라 해당 x를 빼거나 `0`으로 채운다 (클라가 구멍 처리를 안 해도 되게)
- `series`는 1개 이상. 현재 9종 metric은 전부 단일 series이며, 다중 series는 추후 확장 시 도입한다

지원 metric (서버가 채우는 값 — 클라는 미지의 값이 와도 `title`·축 라벨만으로 렌더 가능해야 한다):

| metric | 설명 | chartType | x축 | yLabel 예 |
| --- | --- | --- | --- | --- |
| `bodyWeight` | 체중 변화 | line | 날짜 | 체중(kg) |
| `bmi` | BMI 추이 | line | 날짜 | BMI |
| `exercisePr` | 특정 종목 PR 추이 | line | 날짜 | 추정 1RM(kg) |
| `workoutDuration` | 운동 시간 변화 | line | 주차 | 운동 시간(분) |
| `workoutFrequency` | 운동 빈도 | bar | 주차 | 횟수 |
| `weekdayFrequency` | 요일별 운동 횟수 | bar | 요일 | 횟수 |
| `muscleVolume` | 부위별 볼륨 추이 | line | 주차 | 볼륨(kg) |
| `muscleBalance` | 부위별 볼륨 비중 | bar | 부위 | 볼륨(kg) 또는 비중(%) |
| `topExercises` | 많이 한 종목 순위 | bar | 종목 | 세트 수 |

- 데이터 출처·집계 책임은 [내부 API 명세 §4-B.1](백엔드%20내부%20API%20명세.md) — 백엔드는 원본 행만 주고 버킷팅·합산은 AI 서버가 한다
- 그릴 데이터가 부족하면(기록 0건, `bmi`인데 프로필 키 미입력 등) **차트를 만들지 않고 `responseScheme=text`로 강등**한다 — 빈 차트를 내보내지 않는다

## 8. 에러 코드 (01 카탈로그 외 신규)

| code | HTTP | 상황 |
| --- | --- | --- |
| `THREAD_NOT_FOUND` | 404 | 스레드 없음, TTL 만료 |
| `THREAD_FORBIDDEN` | 403 | 타 유저 스레드 접근 |
| `NO_ROUTINE_CANDIDATE` | 409 | 조건 만족 루틴 구성 불가 |
| `AI_UNAVAILABLE` | 503 | LLM 호출 실패 — 클라는 재시도 UI |
| `RATE_LIMITED` | 429 | 요청 빈도 초과 — 메시지 전송·루틴 생성에 유저별 분당 상한 적용 (2026-07-29 구현) |
| `THREAD_FULL` | 409 | 스레드당 메시지 상한(서버 설정, 기본 100) 도달 — 클라는 새 스레드 생성 유도 |
| `EXERCISE_NOT_FOUND` | 404 | 존재하지 않는 종목 slug (§6-B) |
| `VIDEO_NOT_FOUND` | 404 | 종목은 있으나 가이드 영상 미등록 (§6-B) |

## 9. 협의 포인트 (미확정)

1. ~~Gateway 라우팅 prefix `/ai` — 버저닝(`/ai/v1`) 여부~~ → 확정(2026-08-02): 단일 호스트 + ALB 경로 라우팅 — `api.fitset.kro.kr`의 `/ai/*`가 AI 서버, 코드 프리픽스는 `/ai/v1`. 2026-07-25의 호스트 분리안(`ai.example.com`)은 폐기
2. 스트리밍 — MVP 동기 JSON, SSE 도입 시점·게이트웨이 SSE 통과 설정
3. 스레드 5개 초과 — 자동 삭제(+`deletedThreadId` 반환) vs 409 후 유저 직접 삭제
4. 레이트리밋 — 메시지 분당·루틴 생성 일일 상한, Gateway 적용 여부
5. `responseScheme` 표기 — 와이어 camelCase(`exerciseGif`), 내부 DB snake(`exercise_gif`)
6. `context` 자유 텍스트 — 프롬프트 인젝션 대비 (길이 제한 + 시스템 프롬프트 격리)
7. 가이드 영상 presign — ① TTL 1시간이 적절한지(짧으면 재발급 잦고, 길면 URL 유출 시 노출 창이 커짐) ② S3 직접 presign vs CloudFront signed URL(CDN 캐시 이점) ③ 키 규약 `exercises/{slug}/guide.mp4` 확정 및 확장자(mp4·gif·webp) 혼재 처리
8. 메시지 전송(§6) 멱등성 — 클라 30s 타임아웃 후 재시도하면 user 메시지 중복 저장 + LLM 이중 과금 + assistant 응답 2개. 클라가 생성한 `messageId`(UUID)를 요청 본문에 포함하고 서버가 조건부 저장(attribute_not_exists)으로 중복을 흡수하는 안 — **요청 스키마 변경**이라 클라와 협의 필요 (CS 감사 F3, 2026-07-29)
9. 스레드 삭제(§4)의 `403`·멱등성 — ① `THREAD_FORBIDDEN`을 계약에 남길지(현 PK 설계상 도달 불가, 404로 흡수) ② 이미 삭제된 스레드 재삭제를 404로 둘지 204(멱등)로 바꿀지. 클라 재시도·오프라인 큐 유무에 따라 결정
