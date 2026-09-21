from sqlalchemy.orm import DeclarativeBase


class BackendBase(DeclarativeBase):
    """백엔드 MySQL 엔티티 공유 선언 베이스"""


class SearchBase(DeclarativeBase):
    """루틴 검색 Postgres 엔티티 선언 베이스"""
