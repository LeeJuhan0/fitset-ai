# FitSet AI 서버

운동 앱 FitSet의 AI 백엔드 — AI 채팅(스레드)과 루틴 생성·추천. FastAPI + LangGraph.

인증은 아래 「인증 및 토큰(JWT) 규약」 섹션 참조 — 이 서버가 SSM 공개키로 직접 검증한다.
저장소는 **DynamoDB 온디맨드**(채팅·유저 요약·루틴, 2026-07-25 확정 — DocumentDB 미사용).
루틴 파이프라인: S3 원본 → 변환 배치 → DynamoDB `routines`(변환 완료본, PK=slug) → 부팅 시 Scan 인메모리 로드, 미스 시 GetItem 폴백. 룰 필터 검색은 항상 인메모리. **인메모리 스토어는 부팅 시점 스냅샷 — 배치가 routines를 갱신해도 재시작·재배포 전까지 반영되지 않는다**(무효화 메커니즘 없음, 2026-07-29 감사 F16).

코드를 읽거나 수정하기 전에 반드시 참고할 것:

- [`docs/코드 아키텍처.md`](docs/코드%20아키텍처.md) — 디렉토리 구조, 계층 규칙, 데이터 규칙
- [`docs/코드 컨벤션.md`](docs/코드%20컨벤션.md) — 임포트·타입·docstring 등 파이썬 코드 규약
- [`docs/document-structure.md`](docs/document-structure.md) — DynamoDB 테이블 구조(ERD)와 payload JSON 규약
- [`docs/백엔드 ERD.md`](docs/백엔드%20ERD.md) — 백엔드(Spring·MySQL) DB 참조 사본 + AI 서버 관점 정합성 체크

## 인증 및 토큰(JWT) 규약 (2026-07-25 확정)

출처: [fitset 서버 비즈니스 규칙 §9](https://asmhangang.atlassian.net/wiki/spaces/FIT/pages/24379394/fitset)

### 트래픽 구조

- **API Gateway 미사용.** ELB(ALB)가 **HTTPS 종료(TLS termination)만** 담당 — ELB 뒤 내부 구간은 HTTP 평문.
- 토큰 검증은 각 수신 서버(백엔드·AI 서버)가 직접 수행한다. ELB는 인증에 관여하지 않는다.

### 토큰 발급

- JWT는 **자바 스프링 백엔드가 발급**한다.
- RSA 키쌍은 백엔드가 보유하며, **개인키는 백엔드 외부로 절대 나가지 않는다.**
- **공개키만 SSM Parameter Store에 게시** — 이 서버는 부팅 시 SSM에서 로드해 서명을 검증한다.

### SSM 파라미터

| 항목 | 값 |
| --- | --- |
| 이름 | `/fitset/auth/jwt-public-key` |
| 타입 | String |
| 값 형식 | PEM (X.509 SubjectPublicKeyInfo, `-----BEGIN PUBLIC KEY-----` 블록) |
| 리전 | ap-northeast-2 |
| 키 로테이션 | 값 갱신 시 각 서버 재로드 필요 — 로테이션 절차는 추후 협의 |

- 환경 분리가 필요해지면 `/fitset/{env}/auth/jwt-public-key` 형태로 확장 (예: `/fitset/prod/auth/jwt-public-key`).

### 서명·클레임

| 항목 | 값 |
| --- | --- |
| 서명 알고리즘 | **RS256** (RSA + SHA-256) |
| `sub` | `user_id` — DB `BINARY(16)`(UUIDv7)의 표준 UUID 문자열 표현 |
| `type` | `access` — access token 식별자 |

- 검증 측 필수 절차: ① RS256 서명 검증(SSM 공개키) → ② `exp` 만료 확인 → ③ `type == "access"` 확인 → ④ `sub`를 userId로 사용.
- `type`이 `access`가 아닌 토큰(예: refresh)으로 API를 호출하면 `401 UNAUTHORIZED`.

## 현재 구현 범위 (2026-07-25)

**대상**: 클라이언트 API §1 AI 루틴 생성(`POST /v1/routines`) + 백엔드 내부 API 3종(프로필·운동기록·종목) 호출 클라이언트.
**스택**: FastAPI + uvicorn(WAS), LangGraph/LangChain, **Bedrock**(LLM·임베딩), DynamoDB(boto3), SSM.
**LLM: Amazon Nova 2 Lite(`global.amazon.nova-2-lite-v1:0`, $0.30/$2.50)** — 2026-07-29 비용 절감 확정(Haiku 4.5 대비 입력 1/3.3·출력 1/2). **반드시 global 프로필** — 1세대 Nova는 apac 프로필 강제인데 조직 SCP가 APAC 리전을 차단해 사용 불가(실측), global 라우팅만 SCP를 통과한다. 품질 문제 시 `LLM_MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0`으로 즉시 롤백. 챗봇·루틴·제목·요약이 모델 하나를 공유한다(Converse API라 모델 무관 구조).
**임베딩 모델 고정: `global.cohere.embed-v4:0`** (다국어, 1024d float32, `input_type` 문서=`search_document`/쿼리=`search_query`) — 서울 리전은 global inference profile로만 호출 가능. 모델 변경 시 `scripts/embed_routines.py --force` 전량 재계산 필수(임베딩 공간 호환 안 됨).
호출 주체는 앱 클라이언트. 네트워크 인프라는 Terraform으로 별도 진행(범위 밖).

루틴 생성 요청 처리 플로우:

1. **인증**: `Authorization: Bearer` → SSM 공개키(RS256)로 검증 → `sub` = userId (위 인증 절차 참조)
2. **유저 컨텍스트**: 내부 API로 프로필(기피부위·수준·**목표**) 먼저 조회 — **goal은 요청이 아니라 프로필 값**(2026-07-29 확정, null이면 hypertrophy 기본). 이어서 최근 운동기록 조회와 **6의 쿼리 변환 LLM을 병렬 실행** (변환이 goal에 의존하게 되어 프로필만 직렬화, 변환 LLM ~1초와의 병렬성은 유지)
3. **기록 통계**: 선호 운동·평소 강도(종목별 무게) 정립 → 세트 `weight` 추천값 산출 — 규칙은 [`docs/무게 추천.md`](docs/무게%20추천.md) (Epley e1RM 3계층 폴백: 실측 → 주동근 유추×0.8 → 성별·체중×레벨 계수×목표 계수)
4. **조건 결합**: 요청(level·muscleGroups·minutes·context) + 프로필 goal 기반 필터 조건 구성, 프로필 기피부위는 항상 하드 필터 — 기피(제외) 정보는 요청이 아닌 **내부 API 프로필 조회값**(`null` 가능, 비즈니스 규칙 §5 "제외 운동" 흡수). 응답 최상위 베이스 루틴 참조 필드명은 **`slug`** (routineUrl 아님, 2026-07-25 통일)
5. **후보 생성**: 인메모리 루틴에서 **룰 필터 통과 전체**를 후보로. 메모리는 **라이트 로드**(부팅 시 Scan — 필터 필드·종목명 요약·임베딩만, 실측 ~350MB) — 세트 상세가 담긴 전체 루틴은 **최종 선택된 1건만 GetItem**으로 조회 (2026-07-25 리팩터, 1GB 태스크 가능). 룰 필터 확정 규칙 (2026-07-25):
   - **부위(muscleGroups)**: 요청 부위와 루틴 `muscle_groups`의 **교집합이 있으면 통과** (`∩ ≠ ∅`)
   - **기피 부위(avoidBodyParts)**: 프로필 조회값 — 단 **요청 muscleGroups와 겹치는 부위는 클라 요청 우선**(기피에서 제외). 실효 기피 = `avoidBodyParts − muscleGroups`, 루틴 `muscle_groups`에 실효 기피 부위가 하나라도 포함되면 제외 (`null`/빈 배열이면 미적용)
   - **장비**: 최근 기록에서 **맨몸(bodyweight) 종목 비율 ≥ 70%면 홈트 유저로 판정 → `equipment ⊆ {bodyweight}` 루틴만** 후보. 그 미만(기록 없음 포함)은 **머신 보유 가정 → 장비 필터 없음**
   - **수준(level)**: 루틴 level ≤ 요청 level (수준 상한)
   - **시간(minutes)**: 루틴 `minutes_per_routine`이 요청 ±20% 이내 (제안 기본값 — 후보 부족 시 완화)
6. **랭킹**: 유저 요청·컨텍스트를 "운동 루틴 묘사" 문장으로 변환(LLM) → 쿼리 임베딩(Bedrock `global.cohere.embed-v4:0`, 1024d, `input_type=search_query`) → 룰 필터 통과 **전체**와 코사인 유사도 전량 계산(인메모리 numpy) → **탑30** → 그중 **랜덤(또는 유사도 가중) 5개 샘플** → 탑5의 구성·묘사를 LLM에 제시해 **일등 1개 최종 선택**. 랜덤 샘플이 응답 다양성 담당. 요청당 Bedrock 호출 = 쿼리 변환 LLM 1회 + 쿼리 임베딩 1회 + 최종 선택 LLM 1회
7. **응답 변환**: 4.1 스키마(slug·한글명·`thumbnailUrl`·weight 추천값 포함, `includeWarmup`이면 종목별 첫 세트 경량화)로 반환. 후보 0개면 `409 NO_ROUTINE_CANDIDATE`

**폴백 규칙** (LLM 실패로 503을 내지 않기 위한 단계별 강등):

- 쿼리 변환 LLM 실패 → 프로필 goal + 요청 필드(muscleGroups·minutes 등)를 **템플릿 문자열로 조립**해 묘사문 대체
- 최종 선택 LLM 실패 or 탑5 밖 응답 → **코사인 1위 반환**
- `503 AI_UNAVAILABLE`은 쿼리 임베딩까지 실패한 경우에만

## 팀 공통 열거형 (Enum)

서버가 검증·저장하는 고정 열거형. 저장은 영문 key.
출처: [fitset 서버 비즈니스 규칙 및 제약사항](https://asmhangang.atlassian.net/wiki/spaces/FIT/pages/24379394/fitset)

### 운동 부위 (MUSCLE)

| key | 라벨 |
| --- | --- |
| `back` | 등 |
| `biceps` | 이두근 |
| `calves` | 종아리 |
| `chest` | 가슴 |
| `core` | 코어 |
| `forearms` | 전완근 |
| `glutes` | 둔근 |
| `hamstrings` | 햄스트링 |
| `quadriceps` | 대퇴사두근 |
| `shoulders` | 어깨 |
| `trapezius` | 승모근 |
| `triceps` | 삼두근 |

### 운동 장비 (EQUIP)

| key | 라벨 |
| --- | --- |
| `band` | 밴드 |
| `barbell` | 바벨 |
| `bodyweight` | 맨몸 |
| `cableMachine` | 케이블 머신 |
| `dumbbell` | 덤벨 |
| `kettlebell` | 케틀벨 |
| `machine` | 머신 |

### 난이도 (DIFFICULTY)

| key | 라벨 |
| --- | --- |
| `beginner` | 초급 |
| `intermediate` | 중급 |
| `advanced` | 고급 |

### 운동 목적 (GOAL)

| key | 라벨 |
| --- | --- |
| `hypertrophy` | 근비대 |
| `weightLoss` | 체중감량 |
| `strength` | 근력향상 |
| `endurance` | 체력유지 |

### 운동명 (EXERCISE, slug ↔ 한글명 206종)

종목 식별자는 **slug**, 이름은 **한글명**(metadata `name_ko`)을 쓴다 — **필드명도 `slug`로 통일**(`exerciseId`/`exercise_id` 표기 미사용), 이름 필드는 한글명 (2026-07-25 확정).
정본 파일: `~/Downloads/metadata.ko (1).json` (영문 metadata.json + `*_ko` 한글 필드 확장판, slug 동일).

slug는 대체로 `{장비}-{동작}` 패턴이지만 **영문명 slugify와 일치하지 않는 예외**가 있으므로 이름에서 slug를 기계적으로 역산하지 말 것:

- `machine-face-pulls` → 영문명 "Cable Rope Face Pulls"
- `machine-front-military-press` → 영문명 "Machine Plate Loaded Front Military Press"
- `lunge-walking` → 영문명 "Walking Lunge"
- `parralel-bar-dips` → 오타(parallel 아님)가 slug에 고정됨. 원본 그대로 사용.

metadata 표기 변환: 근육은 Capitalized(`Back`)→소문자(`back`), 장비는 `Cable Machine`→`cableMachine`(그 외 소문자화), 난이도는 표기 동일.

```
abdominals-stretch-variation-four | 복근 스트레칭 변형 4
abdominals-stretch-variation-one | 복근 스트레칭 변형 1
abdominals-stretch-variation-three | 복근 스트레칭 변형 3
abdominals-stretch-variation-two | 복근 스트레칭 변형 2
band-curl | 밴드 컬
band-high-face-pull | 밴드 하이 페이스 풀
band-hip-abduction | 밴드 힙 어브덕션
band-kneeling-pulldown | 밴드 니링 풀다운
band-lateral-raise | 밴드 래터럴 레이즈
band-pullover | 밴드 풀오버
band-romanian-deadlift | 밴드 루마니안 데드리프트
band-row | 밴드 로우
band-seated-pulldown | 밴드 시티드 풀다운
band-shrug | 밴드 슈러그
band-single-arm-lateral-raise | 밴드 싱글 암 래터럴 레이즈
band-wood-chopper | 밴드 우드 초퍼
barbell-banded-back-squat | 바벨 밴디드 백 스쿼트
barbell-behind-the-back-30-degree-shrug | 바벨 비하인드 더 백 30도 슈러그
barbell-bench-press | 바벨 벤치프레스
barbell-bent-over-row | 바벨 벤트오버 로우
barbell-clean-and-press | 바벨 클린 앤 프레스
barbell-close-grip-bench-press | 바벨 클로즈그립 벤치프레스
barbell-curl | 바벨 컬
barbell-deadlift | 바벨 데드리프트
barbell-drag-curl | 바벨 드래그 컬
barbell-front-rack-step-up-knee-drive | 바벨 프론트 랙 스텝업 니 드라이브
barbell-high-incline-bench-press | 바벨 하이 인클라인 벤치 프레스
barbell-incline-bench-press | 바벨 인클라인 벤치 프레스
barbell-muscle-snatch | 바벨 머슬 스내치
barbell-overhead-press | 바벨 오버헤드 프레스
barbell-power-snatch | 바벨 파워 스내치
barbell-pullover | 바벨 풀오버
barbell-rack-pull | 바벨 랙 풀
barbell-shrug | 바벨 슈러그
barbell-snatch | 바벨 스내치
barbell-spinal-jefferson-curl | 바벨 스파이널 제퍼슨 컬
barbell-split-squat | 바벨 스플릿 스쿼트
barbell-squat | 바벨 스쿼트
barbell-step-up-knee-drive | 바벨 스텝업 니 드라이브
barbell-stiff-leg-deadlifts | 바벨 스티프 레그 데드리프트
barbell-thruster | 바벨 스러스터
barbell-upright-row | 바벨 업라이트 로우
barbell-wrist-curl | 바벨 리스트 컬
bench-dips | 벤치 딥스
bodyweight-alternating-lateral-lunge | 맨몸 얼터네이팅 래터럴 런지
bodyweight-alternating-reverse-lunges | 맨몸 얼터네이팅 리버스 런지
bodyweight-box-squat | 맨몸 박스 스쿼트
bodyweight-deadlift | 맨몸 데드리프트
bodyweight-donkey-calf-raise | 맨몸 덩키 카프 레이즈
bodyweight-elevated-push-up | 맨몸 인클라인 푸시업
bodyweight-hip-abduction | 맨몸 힙 어브덕션
bodyweight-knee-push-ups | 맨몸 무릎 푸시업
bodyweight-reverse-lunge | 맨몸 리버스 런지
bodyweight-russian-twist | 맨몸 러시안 트위스트
bodyweight-spinal-jefferson-curl | 맨몸 척추 제퍼슨 컬
bodyweight-squat | 맨몸 스쿼트
box-jump | 박스 점프
bulgarian-split-squat | 불가리안 스플릿 스쿼트
burpee | 버피
cable-30-degree-shrug | 케이블 30도 슈러그
cable-bar-curl | 케이블 바 컬
cable-bar-face-pull | 케이블 바 페이스 풀
cable-bar-pushdown | 케이블 바 푸시다운
cable-bench-chest-fly | 케이블 벤치 체스트 플라이
cable-bench-press | 케이블 벤치 프레스
cable-bench-straight-leg-kickback | 케이블 벤치 스트레이트 레그 킥백
cable-chest-press | 케이블 체스트 프레스
cable-decline-bench-press | 케이블 디클라인 벤치 프레스
cable-incline-bench-press | 케이블 인클라인 벤치 프레스
cable-overhead-press | 케이블 오버헤드 프레스
cable-pec-fly | 케이블 펙 플라이
cable-rope-kneeling-face-pull | 케이블 로프 니링 페이스 풀
cable-rope-pushdown | 케이블 로프 푸시다운
cable-row-bar-standing-row | 케이블 로우 바 스탠딩 로우
cable-seated-rope-face-pull | 케이블 시티드 로프 페이스 풀
cable-side-bend | 케이블 사이드 벤드
cable-single-arm-neutral-grip-row | 케이블 싱글 암 뉴트럴 그립 로우
cable-single-arm-rope-pushdown | 케이블 싱글 암 로프 푸시다운
cable-single-arm-underhand-grip-row | 케이블 한팔 언더핸드 그립 로우
cable-standing-low-to-high-wood-chopper | 케이블 스탠딩 로우 투 하이 우드 초퍼
cable-standing-single-arm-chest-press | 케이블 스탠딩 한팔 체스트 프레스
cable-supinating-row | 케이블 수피네이팅 로우
cable-wood-chopper | 케이블 우드 초퍼
chin-ups | 친업
decline-push-up | 디클라인 푸시업
diamond-push-ups | 다이아몬드 푸시업
dumbbell-alternating-forward-lunge | 덤벨 얼터네이팅 포워드 런지
dumbbell-bench-press | 덤벨 벤치 프레스
dumbbell-bulgarian-split-squat | 덤벨 불가리안 스플릿 스쿼트
dumbbell-chest-fly | 덤벨 체스트 플라이
dumbbell-concentration-curl | 덤벨 컨센트레이션 컬
dumbbell-cross-body-romanian-deadlift | 덤벨 크로스 바디 루마니안 데드리프트
dumbbell-curl | 덤벨 컬
dumbbell-decline-bench-press | 덤벨 디클라인 벤치 프레스
dumbbell-decline-chest-fly | 덤벨 디클라인 체스트 플라이
dumbbell-decline-skullcrusher | 덤벨 디클라인 스컬크러셔
dumbbell-feet-elevated-glute-bridge | 덤벨 발 높인 글루트 브릿지
dumbbell-figure-four-heels-elevated-hip-thrust | 덤벨 피겨 포 발뒤꿈치 높인 힙 스러스트
dumbbell-front-raise | 덤벨 프론트 레이즈
dumbbell-goblet-alternating-curtsy-lunge | 덤벨 고블릿 얼터네이팅 커시 런지
dumbbell-goblet-bulgarian-split-squat | 덤벨 고블릿 불가리안 스플릿 스쿼트
dumbbell-goblet-forward-lunge | 덤벨 고블릿 포워드 런지
dumbbell-goblet-reverse-lunge | 덤벨 고블릿 리버스 런지
dumbbell-goblet-split-squat | 덤벨 고블릿 스플릿 스쿼트
dumbbell-goblet-squat | 덤벨 고블릿 스쿼트
dumbbell-hammer-curl | 덤벨 해머 컬
dumbbell-heels-elevated-hip-thrust | 덤벨 힐 엘리베이티드 힙 쓰러스트
dumbbell-incline-bench-press | 덤벨 인클라인 벤치 프레스
dumbbell-incline-chest-fly | 덤벨 인클라인 체스트 플라이
dumbbell-incline-curl | 덤벨 인클라인 컬
dumbbell-incline-front-raise | 덤벨 인클라인 프론트 레이즈
dumbbell-incline-hammer-curl | 덤벨 인클라인 해머 컬
dumbbell-lateral-raise | 덤벨 레터럴 레이즈
dumbbell-laying-reverse-fly | 덤벨 라잉 리버스 플라이
dumbbell-leg-curl | 덤벨 레그 컬
dumbbell-preacher-curl | 덤벨 프리처 컬
dumbbell-rear-delt-fly | 덤벨 리어 델트 플라이
dumbbell-row-bilateral | 덤벨 로우 양팔
dumbbell-row-unilateral | 덤벨 로우 한 팔
dumbbell-russian-twist | 덤벨 러시안 트위스트
dumbbell-seated-overhead-press | 덤벨 시티드 오버헤드 프레스
dumbbell-seated-overhead-tricep-extension | 덤벨 시티드 오버헤드 트라이셉 익스텐션
dumbbell-seated-rear-delt-fly | 덤벨 시티드 리어 델트 플라이
dumbbell-seated-shrug | 덤벨 시티드 슈러그
dumbbell-shrug | 덤벨 슈러그
dumbbell-side-bend | 덤벨 사이드 벤드
dumbbell-single-arm-chest-press | 덤벨 한팔 체스트 프레스
dumbbell-single-arm-clean-and-press | 덤벨 한팔 클린 앤 프레스
dumbbell-single-arm-row | 덤벨 한팔 로우
dumbbell-single-leg-calf-raise | 덤벨 한다리 카프 레이즈
dumbbell-situp | 덤벨 싯업
dumbbell-skullcrusher | 덤벨 스컬크러셔
dumbbell-spinal-jefferson-curl | 덤벨 척추 제퍼슨 컬
dumbbell-standing-single-arm-curl | 덤벨 스탠딩 한 팔 컬
dumbbell-standing-single-arm-hammer-curl | 덤벨 스탠딩 한 팔 해머 컬
dumbbell-sumo-squat | 덤벨 스모 스쿼트
dumbbell-superman | 덤벨 슈퍼맨
dumbbell-thruster | 덤벨 스러스터
dumbbell-tricep-kickback | 덤벨 트라이셉 킥백
dumbbell-upright-row | 덤벨 업라이트 로우
dumbbell-wrist-curl | 덤벨 리스트 컬
dumbbell-wrist-extension | 덤벨 리스트 익스텐션
elbow-side-plank | 엘보 사이드 플랭크
ez-bar-preacher-curl | EZ바 프리처 컬
ez-bar-reverse-preacher-curl | EZ바 리버스 프리처 컬
forward-lunge | 포워드 런지
good-mornings | 굿모닝
hand-plank | 핸드 플랭크
hanging-knee-raises | 행잉 니 레이즈
incline-push-up | 인클라인 푸시업
inverted-row | 인버티드 로우
jump-squats | 점프 스쿼트
kettlebell-alternating-curtsy-lunge | 케틀벨 얼터네이팅 커시 런지
kettlebell-assisted-bulgarian-split-squat | 케틀벨 어시스티드 불가리안 스플릿 스쿼트
kettlebell-bench-press | 케틀벨 벤치 프레스
kettlebell-calf-raise | 케틀벨 카프 레이즈
kettlebell-curl | 케틀벨 컬
kettlebell-farmers-carry | 케틀벨 파머스 캐리
kettlebell-front-raise | 케틀벨 프론트 레이즈
kettlebell-goblet-curl | 케틀벨 고블릿 컬
kettlebell-gorilla-row | 케틀벨 고릴라 로우
kettlebell-hip-thrust | 케틀벨 힙 쓰러스트
kettlebell-incline-bench-press | 케틀벨 인클라인 벤치 프레스
kettlebell-push-press | 케틀벨 푸시 프레스
kettlebell-romanian-deadlift | 케틀벨 루마니안 데드리프트
kettlebell-row | 케틀벨 로우
kettlebell-row-single | 케틀벨 로우 (싱글)
kettlebell-seated-overhead-press | 케틀벨 시티드 오버헤드 프레스
kettlebell-shrug | 케틀벨 슈러그
kettlebell-single-arm-row | 케틀벨 원암 로우
kettlebell-spinal-jefferson-curl | 케틀벨 제퍼슨 컬
kettlebell-sumo-deadlift | 케틀벨 스모 데드리프트
kettlebell-swing | 케틀벨 스윙
kettlebell-thruster | 케틀벨 스러스터
kettlebell-windmill | 케틀벨 윈드밀
landmine-t-bar-rows | 랜드마인 티바 로우
lunge-walking | 워킹 런지
machine-45-degree-back-extension | 머신 45도 백 익스텐션
machine-cable-v-bar-push-downs | 머신 케이블 V바 푸시다운
machine-chest-press | 머신 체스트 프레스
machine-crunch | 머신 크런치
machine-dips | 머신 딥스
machine-face-pulls | 케이블 로프 페이스 풀
machine-front-military-press | 머신 플레이트 로디드 프론트 밀리터리 프레스
machine-leg-extension | 머신 레그 익스텐션
machine-leg-press | 머신 레그 프레스
machine-neutral-row | 머신 뉴트럴 로우
machine-pec-fly | 머신 펙 플라이
machine-plate-loaded-leg-extension | 머신 플레이트 로디드 레그 익스텐션
machine-plate-loaded-t-bar-row | 머신 플레이트 로디드 티바 로우
machine-pulldown | 머신 풀다운
machine-seated-cable-row | 머신 시티드 케이블 로우
machine-underhand-row | 머신 언더핸드 로우
mountain-climber | 마운틴 클라이머
narrow-pulldown | 내로우 풀다운
parralel-bar-dips | 평행봉 딥스
plate-forward-lunge | 플레이트 포워드 런지
pull-ups | 풀업
push-up | 푸시업
single-legged-romanian-deadlifts | 싱글 레그 루마니안 데드리프트
smith-machine-close-grip-bench-press | 스미스 머신 클로즈 그립 벤치 프레스
smith-machine-incline-bench-press | 스미스 머신 인클라인 벤치 프레스
smith-machine-standing-shrugs | 스미스 머신 스탠딩 슈러그
smith-machine-sumo-romanian-deadlift | 스미스 머신 스모 루마니안 데드리프트
supermans | 슈퍼맨
wall-sit | 월 싯
```
