# FitSet AI Server

[Google Play에서 FitSet 다운로드](https://play.google.com/store/apps/details?id=com.teamhangang.fitset&hl=ko)

자동 수집된 운동 기록 데이터 위에서 LangGraph 에이전트 기반 챗봇 코칭과 루틴 추천을 SSE 스트리밍으로 제공하는 AI 서버입니다.

## 클라우드 아키텍처

![클라우드 아키텍처](docs/img/readme/cloud-architecture.png)

## 쿠버네티스 구성, EKS

![EKS 구성](docs/img/readme/eks-architecture.png)

## 시스템 아키텍처

![시스템 아키텍처](docs/img/readme/system-architecture.png)

## 기술 스택

| 구분 | 기술 |
|------|------|
| 서버 | Python, FastAPI |
| ML | PyTorch, MLflow, Core ML, ONNX |
| LLM | AWS Bedrock, LangGraph |
| DB | MySQL(RDS), PostgreSQL(RDS, pgvector), DynamoDB(NoSQL) |
| 인프라 | EKS, ALB Controller, External Secrets, Route 53, VPC, NAT Gateway, S3, CloudFront, SSM |
| 배포 | Docker, ECR, Kustomize, GitHub Actions |
| 관측 | Datadog, Sentry, Slack |

## AI 에이전트 채팅 처리 플로우

![AI 에이전트 채팅 처리 플로우](docs/img/readme/chat-agent-flow.png)

## 백엔드 ERD, MySQL

[dbdiagram 보기](https://dbdiagram.io/d/6a44ddf036d348d120425f1f)

![백엔드 ERD](docs/img/readme/mysql-erd.svg)

## 챗봇 문서 구조, DynamoDB

[dbdiagram 보기](https://dbdiagram.io/d/6a61a5d1067336e1ded8131a)

![챗봇 문서 구조](docs/img/readme/chat-nosql.png)

## 코드 아키텍처

| 경로 | 역할 |
|------|------|
| app/main.py | 앱 조립, 미들웨어, traceId, 헬스체크 |
| app/deps.py | 요청 traceId 의존성 |
| app/core/ | 설정, 암호화, DynamoDB, ORM, 예외, 로깅, 레이트리밋 |
| app/clients/ | Bedrock, MySQL, Postgres, Slack 클라이언트 |
| app/auth/ | JWT 검증, SSM 공개키 |
| app/chat/ | 채팅 스레드, 메시지, SSE, 대화 요약 |
| app/memory/ | 유저 운동 편향 요약 |
| app/users/ | 프로필, 기피 부위, 체중 이력 조회 |
| app/workouts/ | 운동 기록 조회 |
| app/exercises/ | 종목 카탈로그, 가이드 영상 |
| app/routines/ | 루틴 생성, 무게 추천, prod 동기화 |
| app/rag/ | 요청 분석, 임베딩, 룰 필터 검색, LLM 선택 |
| app/evidence/ | 논문 근거 청크, 임베딩 적재 |
| app/charts/ | 기록 집계, 차트 페이로드 |
| app/agent/ | LangGraph 그래프, 가드레일, 프롬프트, 마크다운 필터 |
| app/agent/tools/ | 기록 조회, 루틴, 차트, 종목, 근거 툴 |
| alembic/ | Postgres 마이그레이션 |
| deploy/k8s/ | Kustomize base, dev·prod 오버레이 |
| scripts/ | 논문 근거 청크 생성 |
| tests/ | 패키지별 단위, 통합 테스트 |

| 층 | 역할 |
|------|------|
| router | HTTP 라우팅, 형식 검증, 응답 직렬화 |
| service | 유스케이스 조율 |
| domain | 순수 업무 규칙, 엔티티 |
| repository | DynamoDB, MySQL, Postgres 접근 |
| clients | 외부 시스템 호출 |
| core | 공유 인프라 |

## 깃 컨벤션

| 항목 | 규칙 | 예시 |
|------|------|------|
| 커밋 메시지 | 타입(스코프): 요약 | fix(routines): C 계층 초기값 현실화 |
| 커밋 타입 | feat, fix, docs, test, refactor, ci, chore | |
| 브랜치 | 타입/케밥 케이스 | feat/thread-quota-409 |
| 이슈 연결 | 제목 끝 (#이슈번호) | test(charts): metric 9종 통합 (#36) |
| PR | 템플릿 작성, 요약, API 변경, 결정과 근거, 테스트 | |
| 머지 | develop 머지 시 dev 자동 배포, main은 CD 수동 실행으로 prod 배포 | |
