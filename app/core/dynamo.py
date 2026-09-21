from pynamodb.models import Model

from app.core.config import get_settings


class DynamoModel(Model):
    """DynamoDB 모델 공통 베이스 논리 테이블명 선언"""


def bind_tables() -> None:
    """전체 모델에 환경별 테이블명과 접속 설정 바인딩"""
    settings = get_settings()
    for model in DynamoModel.__subclasses__():
        meta = model.Meta
        if not hasattr(meta, "logical_name"):
            meta.logical_name = meta.table_name
        meta.table_name = getattr(settings, f"{meta.logical_name}_table")
        meta.region = settings.aws_region
        meta.connect_timeout_seconds = settings.dynamo_connect_timeout
        meta.read_timeout_seconds = settings.dynamo_read_timeout
        meta.max_retry_attempts = settings.dynamo_max_attempts
        meta.max_pool_connections = settings.executor_max_workers
        model._connection = None
