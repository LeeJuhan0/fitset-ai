# FitSet AI 서버 — EKS 배포용 이미지 (ECR push 는 .github/workflows/cd.yml)
# 빌드:  docker build -t fitset-ai-server .
# 실행:  docker run -p 8000:8000 fitset-ai-server
#        (AWS 자격은 EKS Pod Identity 가 주입 — dynamodb:*, bedrock:InvokeModel)
FROM python:3.12-slim

# uv 바이너리만 복사 — uv.lock의 고정 버전 그대로 설치해 로컬과 배포를 일치시킨다
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

# 의존성 레이어 분리 — 코드 변경 시 재설치 방지. 프로젝트 자신은 제외(--no-emit-project)해
# app/ 복사 전에도 설치가 성립한다 (packages=["app"]인 휠 빌드가 여기서 돌면 실패)
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req.txt \
 && uv pip install --system --no-cache -r /tmp/req.txt

# 애플리케이션 + 종목 마스터 데이터 (exercise-metadata만 필요)
COPY app/ app/
COPY data/exercise-metadata.ko.json data/exercise-metadata.ko.json
# 배치 스크립트 — 같은 이미지를 CronJob·Job 이 command 만 바꿔 실행한다. 서버 실행에는 쓰이지 않는다
#   python scripts/sync_exercise_catalog.py        종목 카탈로그 일 1회 동기화
#   python scripts/load_routines_postgres.py ...   루틴 pgvector 적재(DDL 포함, 멱등)
# 무엇이 들어가는지는 .dockerignore 의 허용 목록이 정한다
COPY scripts/ scripts/

# 루트가 아닌 고정 UID 로 실행 — k8s securityContext.runAsNonRoot 를 걸 수 있게. 런타임에 디스크 쓰기는 없다
RUN useradd --uid 10001 --create-home app
USER 10001

EXPOSE 8000

# --limit-concurrency 35 = executor 스레드(EXECUTOR_MAX_WORKERS, 기본 24) + 대기열 여유.
# 초과 요청은 즉시 503 → 클라 30s 타임아웃 전에 실패를 알린다. 스레드 수 조정 시 함께 조정.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "35"]
