"""wrapped v1

Revision ID: codex_wrapped_v1
Revises: 58edfac72c32
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "codex_wrapped_v1"
down_revision = "58edfac72c32"
branch_labels = None
depends_on = None


def _has_table(inspector, table_name: str) -> bool:
    """
    判断表是否已存在，兼容用户从旧版本重复升级的场景。
    """
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    """
    判断字段是否已存在，避免 SQLite 与 PostgreSQL 重复加字段失败。
    """
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    """
    升级：新增 Wrapped 聚合表和媒体服务器画像字段。
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not _has_table(inspector, "wrapped_daily_fact"):
        op.create_table(
            "wrapped_daily_fact",
            sa.Column("id", sa.Integer, primary_key=True, index=True),
            sa.Column("stat_date", sa.String, index=True),
            sa.Column("scope_type", sa.String, index=True, default="instance"),
            sa.Column("download_count", sa.Integer, default=0),
            sa.Column("transfer_count", sa.Integer, default=0),
            sa.Column("transfer_success_count", sa.Integer, default=0),
            sa.Column("transfer_fail_count", sa.Integer, default=0),
            sa.Column("library_total", sa.Integer, default=0),
            sa.Column("movie_count", sa.Integer, default=0),
            sa.Column("tv_count", sa.Integer, default=0),
            sa.Column("episode_count", sa.Integer, default=0),
            sa.Column("storage_used", sa.Float, default=0),
            sa.Column("storage_total", sa.Float, default=0),
            sa.Column("subtitle_covered_count", sa.Integer, default=0),
            sa.Column("site_breakdown", sa.JSON, default={}),
            sa.Column("downloader_breakdown", sa.JSON, default={}),
            sa.Column("genre_breakdown", sa.JSON, default={}),
            sa.Column("country_breakdown", sa.JSON, default={}),
            sa.Column("resolution_breakdown", sa.JSON, default={}),
            sa.Column("dynamic_range_breakdown", sa.JSON, default={}),
            sa.Column("behavior_ready", sa.Boolean, default=False),
            sa.Column("catalog_ready", sa.Boolean, default=False),
            sa.Column("created_at", sa.String),
            sa.Column("updated_at", sa.String),
        )

    if not _has_table(inspector, "wrapped_catalog_snapshot"):
        op.create_table(
            "wrapped_catalog_snapshot",
            sa.Column("id", sa.Integer, primary_key=True, index=True),
            sa.Column("snapshot_at", sa.String, index=True),
            sa.Column("server", sa.String, index=True),
            sa.Column("library", sa.String, index=True),
            sa.Column("item_id", sa.String, index=True),
            sa.Column("item_type", sa.String, index=True),
            sa.Column("title", sa.String),
            sa.Column("year", sa.String),
            sa.Column("tmdbid", sa.Integer, index=True),
            sa.Column("genres", sa.JSON, default=[]),
            sa.Column("countries", sa.JSON, default=[]),
            sa.Column("resolution_bucket", sa.String, index=True),
            sa.Column("dynamic_range", sa.String, index=True),
            sa.Column("has_subtitles", sa.Boolean, default=False),
            sa.Column("subtitle_stream_count", sa.Integer, default=0),
            sa.Column("audio_stream_count", sa.Integer, default=0),
            sa.Column("extra", sa.JSON, default={}),
            sa.Column("created_at", sa.String),
        )

    if not _has_table(inspector, "wrapped_build_state"):
        op.create_table(
            "wrapped_build_state",
            sa.Column("id", sa.Integer, primary_key=True, index=True),
            sa.Column("job_key", sa.String, unique=True, index=True),
            sa.Column("status", sa.String, index=True, default="idle"),
            sa.Column("progress", sa.Integer, default=0),
            sa.Column("message", sa.String),
            sa.Column("error", sa.String),
            sa.Column("started_at", sa.String),
            sa.Column("finished_at", sa.String),
            sa.Column("updated_at", sa.String),
            sa.Column("payload", sa.JSON, default={}),
        )

    if _has_table(inspector, "mediaserveritem"):
        wrapped_columns = {
            "genres": sa.Column("genres", sa.JSON, nullable=True),
            "countries": sa.Column("countries", sa.JSON, nullable=True),
            "resolution_bucket": sa.Column("resolution_bucket", sa.String, nullable=True),
            "dynamic_range": sa.Column("dynamic_range", sa.String, nullable=True),
            "has_subtitles": sa.Column("has_subtitles", sa.Boolean, nullable=True),
            "subtitle_stream_count": sa.Column("subtitle_stream_count", sa.Integer, nullable=True),
            "audio_stream_count": sa.Column("audio_stream_count", sa.Integer, nullable=True),
        }
        for column_name, column in wrapped_columns.items():
            if not _has_column(inspector, "mediaserveritem", column_name):
                op.add_column("mediaserveritem", column)


def downgrade() -> None:
    """
    降级：移除 Wrapped 聚合表，保留媒体服务器新增字段以避免丢失同步信息。
    """
    op.drop_table("wrapped_build_state")
    op.drop_table("wrapped_catalog_snapshot")
    op.drop_table("wrapped_daily_fact")
