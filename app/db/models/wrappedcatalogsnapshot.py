from typing import Dict, List, Optional

from sqlalchemy import Boolean, Column, Integer, JSON, String
from sqlalchemy.orm import Session

from app.db import Base, db_query, db_update, get_id_column


class WrappedCatalogSnapshot(Base):
    """
    Wrapped 媒体库画像表，保存全量扫描得到的当前库标签与技术规格。
    """
    __tablename__ = "wrapped_catalog_snapshot"

    id = get_id_column()
    # 扫描批次时间
    snapshot_at = Column(String, index=True)
    # 媒体服务器名称
    server = Column(String, index=True)
    # 媒体库 ID
    library = Column(String, index=True)
    # 媒体服务器条目 ID
    item_id = Column(String, index=True)
    # 媒体类型
    item_type = Column(String, index=True)
    # 标题
    title = Column(String)
    # 年份
    year = Column(String)
    # TMDB ID
    tmdbid = Column(Integer, index=True)
    # 类型标签
    genres = Column(JSON, default=list)
    # 国家/地区标签
    countries = Column(JSON, default=list)
    # 分辨率分桶
    resolution_bucket = Column(String, index=True)
    # 动态范围
    dynamic_range = Column(String, index=True)
    # 是否包含字幕
    has_subtitles = Column(Boolean, default=False)
    # 字幕流数量
    subtitle_stream_count = Column(Integer, default=0)
    # 音频流数量
    audio_stream_count = Column(Integer, default=0)
    # 原始扩展信息
    extra = Column(JSON, default=dict)
    # 创建时间
    created_at = Column(String)

    @classmethod
    @db_update
    def replace_all(cls, db: Session, rows: List[Dict]) -> None:
        """
        用一次完整扫描结果替换当前画像，避免不同批次数据混合导致榜单失真。
        """
        db.query(cls).delete()
        if rows:
            db.bulk_insert_mappings(cls, rows)

    @classmethod
    @db_query
    def list_all(cls, db: Session) -> List["WrappedCatalogSnapshot"]:
        """
        查询当前完整媒体库画像。
        """
        return db.query(cls).all()

    @classmethod
    @db_query
    def latest_snapshot_at(cls, db: Session) -> Optional[str]:
        """
        查询最近一次媒体库画像扫描时间。
        """
        item = db.query(cls).order_by(cls.snapshot_at.desc()).first()
        return item.snapshot_at if item else None
