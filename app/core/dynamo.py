"""DynamoDB 커넥션 — boto3 리소스 핸들. core 레이어."""
from functools import lru_cache

import boto3

from app.core.config import get_settings


@lru_cache
def get_routines_table():
    """`routines` 테이블 핸들 (온디맨드, PK=slug)."""
    settings = get_settings()
    resource = boto3.resource("dynamodb", region_name=settings.aws_region)
    return resource.Table(settings.routines_table)
