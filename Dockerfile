# FitSet AI 서버 — EKS 배포용 이미지 (ECR push 는 .github/workflows/cd.yml)
# 빌드:  docker build -t fitset-ai-server .
# 실행:  docker run -p 8000:8000 fitset-ai-server
#        (AWS 자격은 EKS Pod Identity 가 주입 — dynamodb:*, bedrock:InvokeModel)
FROM python:3.12-slim

WORKDIR /srv

# 의존성 레이어 분리 — 코드 변경 시 재설치 방지. poetry.lock 고정 버전을 시스템 파이썬에 설치한다
COPY pyproject.toml poetry.lock ./
ENV POETRY_VIRTUALENVS_CREATE=false
RUN pip install --no-cache-dir "poetry==2.5.1" \
 && poetry install --only main --no-root --no-interaction \
 && rm -rf /root/.cache/pypoetry

# 애플리케이션
COPY app/ app/
# 루틴 pgvector 스키마 마이그레이션 — 서버 실행에는 쓰이지 않는다
#   docker exec <컨테이너> alembic upgrade head
COPY alembic/ alembic/
COPY alembic.ini alembic.ini

# 루트가 아닌 고정 UID 로 실행 — k8s securityContext.runAsNonRoot 를 걸 수 있게. 런타임에 디스크 쓰기는 없다
RUN useradd --uid 10001 --create-home app
USER 10001

EXPOSE 8000

# --limit-concurrency 35 = executor 스레드(EXECUTOR_MAX_WORKERS, 기본 24) + 대기열 여유.
# 초과 요청은 즉시 503 → 클라 30s 타임아웃 전에 실패를 알린다. 스레드 수 조정 시 함께 조정.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "35"]
