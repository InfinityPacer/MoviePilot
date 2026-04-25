from typing import Dict, List, Optional

from sqlalchemy import Boolean, Column, Float, Integer, JSON, String, and_
from sqlalchemy.orm import Session

from app.db import Base, db_query, db_update, get_id_column


class WrappedDailyFact(Base):
    """
    Wrapped 日级事实表，保存可回填行为指标和可持续积累的媒体库快照指标。
    """
    __tablename__ = "wrapped_daily_fact"

    id = get_id_column()
    # 统计自然日，格式 YYYY-MM-DD
    stat_date = Column(String, index=True)
    # 统计范围类型，V1 固定为 instance
    scope_type = Column(String, default="instance", index=True)
    # 下载次数
    download_count = Column(Integer, default=0)
    # 整理总次数
    transfer_count = Column(Integer, default=0)
    # 整理成功次数
    transfer_success_count = Column(Integer, default=0)
    # 整理失败次数
    transfer_fail_count = Column(Integer, default=0)
    # 媒体库总量
    library_total = Column(Integer, default=0)
    # 电影数量
    movie_count = Column(Integer, default=0)
    # 剧集数量
    tv_count = Column(Integer, default=0)
    # 剧集集数
    episode_count = Column(Integer, default=0)
    # 已用存储空间，单位 bytes
    storage_used = Column(Float, default=0)
    # 总存储空间，单位 bytes
    storage_total = Column(Float, default=0)
    # 字幕覆盖数量
    subtitle_covered_count = Column(Integer, default=0)
    # 站点贡献分布
    site_breakdown = Column(JSON, default=dict)
    # 下载器贡献分布
    downloader_breakdown = Column(JSON, default=dict)
    # 类型分布
    genre_breakdown = Column(JSON, default=dict)
    # 国家/地区分布
    country_breakdown = Column(JSON, default=dict)
    # 分辨率分布
    resolution_breakdown = Column(JSON, default=dict)
    # 动态范围分布
    dynamic_range_breakdown = Column(JSON, default=dict)
    # 是否包含行为历史数据
    behavior_ready = Column(Boolean, default=False)
    # 是否包含媒体库快照数据
    catalog_ready = Column(Boolean, default=False)
    # 创建时间
    created_at = Column(String)
    # 更新时间
    updated_at = Column(String)

    @classmethod
    @db_query
    def get_by_date(cls, db: Session, stat_date: str, scope_type: str = "instance") -> Optional["WrappedDailyFact"]:
        """
        按自然日和范围查询 Wrapped 日事实。
        """
        return db.query(cls).filter(and_(cls.stat_date == stat_date, cls.scope_type == scope_type)).first()

    @classmethod
    @db_query
    def list_between(cls, db: Session, start_date: str, end_date: str) -> List["WrappedDailyFact"]:
        """
        查询日期区间内的日事实，包含起止日期。
        """
        return (
            db.query(cls)
            .filter(cls.stat_date >= start_date, cls.stat_date <= end_date)
            .order_by(cls.stat_date.asc())
            .all()
        )

    @classmethod
    @db_query
    def first_date(cls, db: Session) -> Optional[str]:
        """
        查询 Wrapped 已积累事实的最早日期。
        """
        item = db.query(cls).order_by(cls.stat_date.asc()).first()
        return item.stat_date if item else None

    @classmethod
    @db_query
    def latest_catalog(cls, db: Session) -> Optional["WrappedDailyFact"]:
        """
        查询最近一次包含媒体库快照的日事实。
        """
        return db.query(cls).filter(cls.catalog_ready == True).order_by(cls.stat_date.desc()).first()  # noqa: E712

    @classmethod
    @db_update
    def upsert(cls, db: Session, stat_date: str, payload: Dict, scope_type: str = "instance") -> None:
        """
        幂等写入日事实，重建任务可重复执行而不生成重复日期记录。
        """
        item = cls.get_by_date(db, stat_date, scope_type)
        if item:
            item.update(db, payload)
        else:
            db.add(cls(stat_date=stat_date, scope_type=scope_type, **payload))

    @classmethod
    @db_update
    def delete_behavior_between(cls, db: Session, start_date: str, end_date: str) -> None:
        """
        清理指定范围的行为指标，强制回填时保留同日媒体库快照指标。
        """
        items = cls.list_between(db, start_date, end_date)
        for item in items:
            item.update(
                db,
                {
                    "download_count": 0,
                    "transfer_count": 0,
                    "transfer_success_count": 0,
                    "transfer_fail_count": 0,
                    "site_breakdown": {},
                    "downloader_breakdown": {},
                    "behavior_ready": False,
                },
            )
