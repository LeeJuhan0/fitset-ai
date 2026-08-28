# 인프라 첫 apply 체크리스트

fitset-infra-tf 를 처음 apply 하고 ArgoCD 가 fitset-gitops 를 물어 stage 가 트래픽을 받기까지의 순서다. 작성일 2026-08-28, 대상 강인화.

## 1. 현재 상황

이 레포는 아직 한 번도 apply 된 적이 없다. S3 `fitset-tf-state` 에 `fitset-infra/terraform.tfstate` 가 없고 AWS 에 EKS 클러스터가 없다. 이번 apply 는 VPC, EKS, RDS 3대, ArgoCD, Pod Identity 를 전부 처음 만드는 실행이다(README 기준 약 20분).

| 항목 | 상태 |
|---|---|
| infra-tf main | `723bdcb`, fmt 와 validate 통과. 히스토리를 재작성해 force push 했음 |
| gitops main | `3cc5480`, stage 앱 5개(core-api, ai-chat-api, ml-user-api, ml-admin-api, mlflow) |
| ECR | stage 가 가리키는 태그 전부 존재, amd64 |
| SSM | `/fitset/stage/db/password`, `/fitset/stage/pg/password`, `/fitset/prod/db/password`, `/fitset/stage/ai/mysql-password` 존재 |
| S3 | `fitset-user-uploads`, `fitset-models`, `fitset-dataset` 존재 |
| DynamoDB | prod 테이블 4개 콘솔 생성본 존재, state 에는 없음. stage 사본(`*_stage`)은 apply 가 새로 만듦 |
| 루틴 데이터 | DynamoDB `routines` 25,853건(임베딩 포함). 백업은 `s3://fitset-routines-raw/snapshots/` |
| DNS | `api-stage.fitset.kro.kr` 이 옛 `hangang-alb` 를 가리킴 |

## 2. apply 전에 고친 것

pgvector RDS 의 마스터 유저명이 `admin` 이면 RDS PostgreSQL 예약어라 apply 가 `MasterUsername admin cannot be used as it is a reserved word used by the engine` 으로 실패한다. MySQL 은 `admin` 이 되지만 Postgres 는 안 된다. 2026-08-28 에 `rds.tf` 와 ai-server `config.py` 의 `pg_user` 기본값을 `fitset` 으로 맞췄다. infra-tf 는 이 변경이 main 에 있는지 확인하고 apply 한다.

## 3. 순서

### 3.1 로컬 클론 재설정

```bash
cd fitset-infra-tf
git fetch && git reset --hard origin/main
```

이유. PR 머지 커밋을 지우려고 main 을 재작성해 force push 했다. 옛 클론에서 pull 하면 지운 머지 커밋과 새 커밋이 섞이고, 그 상태로 push 하면 지운 히스토리가 되살아난다.

### 3.2 DynamoDB prod 테이블 4개 import

```bash
terraform init
for t in chat_threads chat_messages user_summaries exercise_catalog; do
  terraform import "aws_dynamodb_table.this[\"prod/$t\"]" "$t"
done
```

이유. 이 4개는 콘솔에서 먼저 만들어져 ECS prod 가 쓰고 있다. AWS 에는 있고 state 에는 없어서, import 없이 apply 하면 테라폼이 같은 이름으로 만들려다 `ResourceInUseException` 으로 실패한다. `dynamodb.tf` 는 실제 설정을 그대로 적은 것이라 import 뒤 diff 가 없다. DynamoDB 는 환경마다 한 벌이라 stage 는 `chat_threads_stage` 처럼 접미사가 붙은 새 테이블을 apply 가 만들고, ai-chat-api stage 는 `*_TABLE` env 로 그걸 가리킨다. `routines` 는 pgvector 로 이관 중이라 코드에 없고 import 도 하지 않는다.

### 3.3 plan 검토

```bash
terraform plan -out=plan.tfplan
```

이유. `-/+`(replace) 가 보이면 apply 하지 않는다. DynamoDB 는 replace 시 데이터가 사라지고 RDS 는 재생성된다. 정상이면 DynamoDB 4개는 변경 없음, 나머지는 전부 create 다.

| 변수 | 기본값 | 의미 |
|---|---|---|
| `nat_per_az` | false | NAT 1대(저렴, SPOF). AZ 마다 두려면 true |
| `enable_prod_rds` | false | prod RDS 를 만들지 않는다. prod 는 아직 hangang-rds 를 본다 |

### 3.4 apply

```bash
terraform apply plan.tfplan
aws eks update-kubeconfig --name $(terraform output -raw cluster_name) --region ap-northeast-2
```

이유. 검토한 plan 파일을 그대로 적용해야 본 것과 실행이 같다. root Application 까지 생기면 ArgoCD 가 fitset-gitops 를 스스로 동기화한다. 정상이면 plan 에서 DynamoDB 는 prod 4개 변경 없음, stage 4개 create 로 보인다.

### 3.5 ArgoCD 1차 확인

`kubectl -n argocd port-forward svc/argocd-server 8080:80` 후 stage 앱 상태를 본다. 이 시점에 기대하는 상태는 다음과 같다.

| 앱 | 기대 상태 | 이유 |
|---|---|---|
| core-api, ml-user-api, ml-admin-api | Healthy | 의존 없음 |
| ai-chat-api | Healthy | `PG_HOST` 가 아직 없어 루틴 생성만 503, 채팅은 정상 |
| mlflow | Degraded | 3.6 의 SSM 값이 없어 ExternalSecret 실패. 정상 |

ml 3종은 Pod Identity 롤을 서비스어카운트 이름으로 받는다. 롤보다 파드가 먼저 떴으면 `rollout restart` 한다. ml-admin-api 가 Pending 이면 t3.medium 노드 여유부터 본다.

### 3.6 mlflow DB 와 SSM

stage MySQL RDS(`fitset-rds-stage`)에 `mlflow` 데이터베이스를 만들고 SSM 에 접속 URI 를 넣는다.

```bash
# 클러스터 안 임시 파드에서
mysql -h <rds_stage_endpoint> -u admin -p -e "CREATE DATABASE mlflow;"
aws ssm put-parameter --name /fitset/stage/ml/mlflow-db-url --type SecureString \
  --value "mysql+pymysql://admin:<pw>@<rds_stage_endpoint>:3306/mlflow"
kubectl -n fitset-stage rollout restart deploy/mlflow
```

이유. mlflow 의 ExternalSecret 이 이 키를 읽는데 SSM 에 없다. 이미지는 sqlite 스냅샷을 굽고 있지만 k8s 에서는 `BACKEND_STORE_URI` 를 RDS 로 주입해 무상태로 뜬다.

### 3.7 stage 종목 카탈로그 채우기

```bash
# 클러스터 안 임시 파드(3.8 과 같은 파드)에서
EXERCISE_CATALOG_TABLE=exercise_catalog_stage python scripts/sync_exercise_catalog.py
```

이유. `exercise_catalog` 는 백엔드 공개 API 를 일 1회 배치로 캐시한 파생 테이블이라 새로 만든 stage 사본은 비어 있다. 채우기 전에는 종목 조회가 빈 결과를 낸다. 채팅 테이블 3개는 비어 있는 게 정상이다.

### 3.8 pgvector DDL 과 루틴 적재

ai-server 이미지로 임시 파드를 띄워 실행한다. 이미지에 `scripts/`, psycopg, pgvector, boto3 가 들어 있고, SA `ai-chat-api` 를 쓰면 Pod Identity 로 DynamoDB 읽기가 된다.

```bash
kubectl -n fitset-stage run loader --rm -it --overrides='{"spec":{"serviceAccountName":"ai-chat-api"}}' \
  --image=729743892772.dkr.ecr.ap-northeast-2.amazonaws.com/fitset-ai-server:3cada1de9da3f64c2a5e6e33d1c719ee8d783ae9 -- bash
# 파드 안
export PG_DSN="postgresql://fitset:<pw>@<rds_pgvector_stage_endpoint>:5432/fitset"
psql "$PG_DSN" -f scripts/sql/routines_pgvector.sql
SRC=s3://fitset-routines-raw/snapshots/routines-strict-20260825.jsonl.gz
python scripts/load_routines_postgres.py --source $SRC --dry-run
python scripts/load_routines_postgres.py --source $SRC
psql "$PG_DSN" -c "select count(*) from routines;"   # 25853
```

이유. RDS 는 인터넷 경로가 없어 클러스터 안에서만 접근된다. 루틴 25,853건은 캐글 원본을 8/25 엄격 규칙으로 변환해 임베딩까지 붙인 것으로, DynamoDB `routines` 와 S3 스냅샷 양쪽에 같은 내용이 있다. `--source` 로 S3 스냅샷을 읽으면 DynamoDB 권한이 필요 없고 `routines` 테이블을 나중에 지워도 그대로 쓸 수 있다. `--source` 를 빼면 DynamoDB 를 Scan 한다. slug upsert 라 다시 돌려도 안전하다.

### 3.9 gitops 에 pgvector 연결과 이미지 갱신

fitset-gitops `gitops/workloads/ai-chat-api/overlays/stage/` 에서 두 가지를 바꿔 push 한다.

1. `patch-deployment.yaml` 의 주석 처리된 `PG_HOST` 를 `terraform output -raw rds_pgvector_stage_endpoint` 값으로 연다.
2. `kustomization.yaml` 의 `newTag` 를 `3cada1de9da3f64c2a5e6e33d1c719ee8d783ae9` 로 올린다.

이유. `/health` 는 `PG_HOST` 가 있을 때만 Postgres 를 ping 하고 실패하면 503 이라, 적재 전에 넣으면 readiness 가 떨어져 트래픽을 못 받는다. 그래서 3.8 뒤에 넣는다. 새 태그는 `PG_HOST` 빈 값 처리 fix 가 들어간 현재 main 이다.

### 3.10 DNS 전환

kro.kr 관리 페이지에서 `api-stage.fitset.kro.kr` CNAME 을 새 stage ALB 로 바꾼다. ALB 주소는 `kubectl -n fitset-stage get ingress` 로 본다.

이유. 레코드는 이미 있고 전부 옛 `hangang-alb` 를 가리킨다. core-api(JWKS 발급)와 ai-chat-api 가 같은 호스트를 쓰므로 한 번에 넘어간다. stage 는 prod 와 독립이라 먼저 검증할 수 있다.

### 3.11 최종 확인

```bash
curl -s https://api-stage.fitset.kro.kr/health
curl -s https://api-stage.fitset.kro.kr/ai/health
kubectl -n fitset-stage exec deploy/ai-chat-api -- curl -s localhost:8000/health   # {"status":"ok"}
```

## 4. 하지 말 것

1. 2절의 유저명 변경이 main 에 없으면 apply 하지 않는다.
2. plan 에 `-/+` 가 있으면 apply 하지 않는다.
3. `MYSQL_HOST` 에 옛 hangang 주소를 남기지 않는다. 같은 `10.0.0.0/16` 대역이라 fitset VPC 안으로 오라우팅되어 타임아웃난다.
4. ArgoCD 설정 변경은 fitset-gitops 의 values 로 한다. `terraform apply` 나 `helm upgrade` 로 건드리지 않는다.
5. 적재(3.8) 전에 `PG_HOST` 를 넣지 않는다.
