from typing import Dict, Optional

from sqlalchemy import Column, Integer, JSON, String
from sqlalchemy.orm import Session

from app.db import Base, db_query, db_update, get_id_column


class WrappedBuildState(Base):
    """
    Wrapped 构建状态表，记录回填、扫描和日结任务的最近执行状态。
    """
    __tablename__ = "wrapped_build_state"

    id = get_id_column()
    # 任务标识
    job_key = Column(String, index=True, unique=True)
    # 任务状态
    status = Column(String, default="idle", index=True)
    # 任务进度，0-100
    progress = Column(Integer, default=0)
    # 状态消息
    message = Column(String)
    # 错误信息
    error = Column(String)
    # 开始时间
    started_at = Column(String)
    # 结束时间
    finished_at = Column(String)
    # 更新时间
    updated_at = Column(String)
    # 任务参数
    payload = Column(JSON, default=dict)

    @classmethod
    @db_query
    def get_by_key(cls, db: Session, job_key: str) -> Optional["WrappedBuildState"]:
        """
        按任务标识查询构建状态。
        """
        return db.query(cls).filter(cls.job_key == job_key).first()

    @classmethod
    @db_update
    def upsert(cls, db: Session, job_key: str, payload: Dict) -> None:
        """
        幂等更新构建状态，避免重复触发任务时生成多条状态记录。
        """
        item = cls.get_by_key(db, job_key)
        if item:
            item.update(db, payload)
        else:
            db.add(cls(job_key=job_key, **payload))
