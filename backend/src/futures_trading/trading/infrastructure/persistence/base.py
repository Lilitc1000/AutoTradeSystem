"""SQLAlchemy 声明基类与命名约定。"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 命名约定：约束名确定化，alembic autogenerate 才能正确识别/批量操作
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
