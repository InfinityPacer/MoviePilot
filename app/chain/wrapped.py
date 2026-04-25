import calendar
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func

from app import schemas
from app.chain import ChainBase
from app.chain.dashboard import DashboardChain
from app.chain.mediaserver import MediaServerChain
from app.chain.storage import StorageChain
from app.db import ScopedSession
from app.db.models.downloadhistory import DownloadHistory
from app.db.models.transferhistory import TransferHistory
from app.db.models.wrappedbuildstate import WrappedBuildState
from app.db.models.wrappedcatalogsnapshot import WrappedCatalogSnapshot
from app.db.models.wrappeddailyfact import WrappedDailyFact
from app.helper.directory import DirectoryHelper
from app.helper.service import ServiceConfigHelper
from app.log import logger


class WrappedChain(ChainBase):
    """
    MoviePilot Wrapped 聚合链，负责回填行为历史、扫描媒体库画像并输出报告接口数据。
    """

    REBUILD_JOB_KEY = "wrapped_rebuild"
    DAILY_JOB_KEY = "wrapped_daily_rollup"

    METRIC_LABELS = {
        "download": "下载数",
        "transfer": "整理数",
        "success_rate": "整理成功率",
        "storage_used": "存储已用空间",
        "library_total": "媒体库总量",
    }

    def availability(self) -> schemas.WrappedAvailability:
        """
        查询当前实例 Wrapped 可展示能力，前端据此隐藏不稳定增强模块。
        """
        db = ScopedSession()
        try:
            behavior_count = (
                db.query(func.count(WrappedDailyFact.id))
                .filter(WrappedDailyFact.behavior_ready == True)  # noqa: E712
                .scalar()
                or 0
            )
            catalog_count = db.query(func.count(WrappedCatalogSnapshot.id)).scalar() or 0
            subtitle_count = (
                db.query(func.count(WrappedCatalogSnapshot.id))
                .filter(WrappedCatalogSnapshot.has_subtitles == True)  # noqa: E712
                .scalar()
                or 0
            )
        finally:
            db.close()

        notes = []
        if not behavior_count:
            notes.append("尚未回填下载/整理历史，可手动重建后展示真实行为曲线。")
        if not catalog_count:
            notes.append("尚未扫描媒体库画像，类型、地区、分辨率等构成暂不可用。")
        notes.append("观影历史在 V1 中仅作为可选增强，当前不会伪造跨媒体服务器口径。")
        return schemas.WrappedAvailability(
            behavior_history_supported=behavior_count > 0,
            catalog_snapshot_supported=catalog_count > 0,
            subtitle_coverage_supported=subtitle_count > 0,
            watch_history_supported=False,
            watch_user_dimension_supported=False,
            notes=notes,
        )

    def overview(self, range_name: schemas.WrappedRange) -> schemas.WrappedOverview:
        """
        构建 Wrapped 总览卡片，按每个指标自身口径返回数据质量元信息。
        """
        start, end, previous_start, previous_end, granularity = self.__range_window(range_name)
        facts = self.__list_facts(start, end)
        previous_facts = self.__list_facts(previous_start, previous_end) if previous_start and previous_end else []
        metric_meta = self.__metric_meta_index()
        cards = [
            self.__metric_card("download", self.__sum_metric(facts, "download"), self.__sum_metric(previous_facts, "download"), metric_meta),
            self.__metric_card("transfer", self.__sum_metric(facts, "transfer"), self.__sum_metric(previous_facts, "transfer"), metric_meta),
            self.__metric_card("success_rate", self.__success_rate(facts), self.__success_rate(previous_facts), metric_meta, unit="%"),
            self.__metric_card("storage_used", self.__latest_metric(facts, "storage_used"), self.__latest_metric(previous_facts, "storage_used"), metric_meta, unit="bytes"),
            self.__metric_card("library_total", self.__latest_metric(facts, "library_total"), self.__latest_metric(previous_facts, "library_total"), metric_meta),
        ]
        return schemas.WrappedOverview(
            range=range_name,
            granularity=granularity,
            cards=cards,
            metric_meta=metric_meta,
            compare_enabled=range_name != "all",
            empty_reason=None if facts else "当前范围暂无 Wrapped 数据，请先执行重建或等待日结。",
            rebuild_status=self.rebuild_status(),
        )

    def series(self, range_name: schemas.WrappedRange, metric: schemas.WrappedMetric) -> schemas.WrappedSeries:
        """
        构建 Wrapped 指标曲线，行为指标补 0，快照指标仅从可用日期开始延展。
        """
        start, end, _, _, granularity = self.__range_window(range_name)
        facts = self.__list_facts(start, end)
        fact_map = {self.__date_from_text(item.stat_date): item for item in facts}
        buckets = self.__buckets(start, end, granularity)
        points = []
        last_snapshot_value: Optional[float] = None
        for bucket_start, bucket_end in buckets:
            bucket_facts = [fact_map[item] for item in self.__date_range(bucket_start, bucket_end) if item in fact_map]
            if metric == "success_rate":
                value = self.__success_rate(bucket_facts)
            elif metric in ["storage_used", "library_total"]:
                value = self.__latest_metric(bucket_facts, metric)
                if value is None and last_snapshot_value is not None:
                    value = last_snapshot_value
                elif value is not None:
                    last_snapshot_value = value
            else:
                value = self.__sum_metric(bucket_facts, metric)
            points.append(schemas.WrappedSeriesPoint(bucket=bucket_start.isoformat(), value=value))
        meta = self.__metric_meta_index()[metric]
        return schemas.WrappedSeries(
            range=range_name,
            metric=metric,
            granularity=granularity,
            series=points,
            metric_meta=meta,
            compare_enabled=range_name != "all" and meta.comparable_to_previous_period,
            empty_reason=None if facts else "当前范围暂无曲线数据。",
        )

    def rankings(self, range_name: schemas.WrappedRange) -> schemas.WrappedRankings:
        """
        构建 Wrapped 来源贡献和媒体库构成榜单。
        """
        start, end, _, _, _ = self.__range_window(range_name)
        facts = self.__list_facts(start, end)
        latest_catalog = self.__latest_catalog_fact(facts)
        groups = [
            self.__ranking_group("sites", "Top 站点", self.__merge_breakdowns(facts, "site_breakdown"), "backfilled"),
            self.__ranking_group("downloaders", "Top 下载器", self.__merge_breakdowns(facts, "downloader_breakdown"), "backfilled"),
        ]
        if latest_catalog:
            groups.extend(
                [
                    self.__ranking_group("genres", "类型构成", latest_catalog.genre_breakdown or {}, "projected"),
                    self.__ranking_group("countries", "国家/地区构成", latest_catalog.country_breakdown or {}, "projected"),
                    self.__ranking_group("resolutions", "分辨率构成", latest_catalog.resolution_breakdown or {}, "projected"),
                    self.__ranking_group("dynamic_ranges", "动态范围构成", latest_catalog.dynamic_range_breakdown or {}, "projected"),
                ]
            )
        return schemas.WrappedRankings(
            range=range_name,
            groups=groups,
            empty_reason=None if any(group.items for group in groups) else "当前范围暂无榜单数据。",
        )

    def highlights(self, range_name: schemas.WrappedRange) -> schemas.WrappedHighlights:
        """
        构建 Wrapped 高光故事，展示行为峰值和当前媒体库里程碑。
        """
        start, end, _, _, _ = self.__range_window(range_name)
        facts = self.__list_facts(start, end)
        highlights: List[schemas.WrappedHighlight] = []
        if facts:
            active = max(facts, key=lambda item: (item.download_count or 0) + (item.transfer_count or 0))
            highlights.append(
                schemas.WrappedHighlight(
                    key="most_active_day",
                    title="最活跃的一天",
                    value=(active.download_count or 0) + (active.transfer_count or 0),
                    date=active.stat_date,
                    history_mode="backfilled",
                    description="按下载数与整理数合计计算。",
                )
            )
            peak_transfer = max(facts, key=lambda item: item.transfer_count or 0)
            highlights.append(
                schemas.WrappedHighlight(
                    key="peak_transfer_day",
                    title="整理峰值日",
                    value=peak_transfer.transfer_count or 0,
                    date=peak_transfer.stat_date,
                    history_mode="backfilled",
                    description="来自整理历史真实回填。",
                )
            )
        latest_catalog = self.__latest_catalog_fact(facts)
        if latest_catalog:
            highlights.append(
                schemas.WrappedHighlight(
                    key="library_total",
                    title="当前媒体库规模",
                    value=latest_catalog.library_total or 0,
                    date=latest_catalog.stat_date,
                    history_mode="projected",
                    description="来自最近一次媒体库扫描快照，不代表过去日期真实库存。",
                )
            )
            if latest_catalog.subtitle_covered_count:
                highlights.append(
                    schemas.WrappedHighlight(
                        key="subtitle_coverage",
                        title="字幕覆盖条目",
                        value=latest_catalog.subtitle_covered_count,
                        date=latest_catalog.stat_date,
                        history_mode="projected",
                        description="按媒体服务器可返回的字幕轨信息统计。",
                    )
                )
        return schemas.WrappedHighlights(
            range=range_name,
            highlights=highlights,
            empty_reason=None if highlights else "当前范围暂无可展示高光。",
        )

    def rebuild_status(self, job_key: str = REBUILD_JOB_KEY) -> schemas.WrappedRebuildStatus:
        """
        查询 Wrapped 重建状态，没有历史记录时返回 idle。
        """
        db = ScopedSession()
        try:
            state = WrappedBuildState.get_by_key(db, job_key)
        finally:
            db.close()
        if not state:
            return schemas.WrappedRebuildStatus(job_key=job_key)
        return schemas.WrappedRebuildStatus(
            job_key=state.job_key,
            status=state.status or "idle",
            progress=state.progress or 0,
            message=state.message,
            error=state.error,
            started_at=state.started_at,
            finished_at=state.finished_at,
            updated_at=state.updated_at,
            payload=state.payload or {},
        )

    def schedule_rebuild(self, request: schemas.WrappedRebuildRequest) -> schemas.WrappedRebuildStatus:
        """
        标记 Wrapped 重建任务为运行中，实际执行由 API BackgroundTasks 承接。
        """
        now = self.__now_text()
        self.__update_state(
            self.REBUILD_JOB_KEY,
            status="running",
            progress=1,
            message="Wrapped 重建任务已提交",
            error=None,
            started_at=now,
            finished_at=None,
            updated_at=now,
            payload=request.model_dump(),
        )
        return self.rebuild_status()

    def rebuild(self, request: schemas.WrappedRebuildRequest) -> None:
        """
        执行 Wrapped 重建，按请求选择行为回填和媒体库画像扫描。
        """
        try:
            if request.include_behavior:
                self.__update_state(self.REBUILD_JOB_KEY, progress=20, message="正在回填下载/整理历史", updated_at=self.__now_text())
                self.backfill_behavior(start_date=request.start_date, end_date=request.end_date, force=request.force)
            if request.include_catalog:
                self.__update_state(self.REBUILD_JOB_KEY, progress=60, message="正在扫描媒体库画像", updated_at=self.__now_text())
                self.scan_catalog()
            self.__update_state(self.REBUILD_JOB_KEY, progress=85, message="正在生成今日快照", updated_at=self.__now_text())
            self.daily_rollup()
            now = self.__now_text()
            self.__update_state(
                self.REBUILD_JOB_KEY,
                status="success",
                progress=100,
                message="Wrapped 重建完成",
                error=None,
                finished_at=now,
                updated_at=now,
            )
        except Exception as err:
            logger.error(f"Wrapped 重建失败：{err} - {traceback.format_exc()}")
            now = self.__now_text()
            self.__update_state(
                self.REBUILD_JOB_KEY,
                status="failed",
                progress=100,
                message="Wrapped 重建失败",
                error=str(err),
                finished_at=now,
                updated_at=now,
            )

    def backfill_behavior(self, start_date: Optional[str] = None, end_date: Optional[str] = None, force: bool = False) -> None:
        """
        从下载历史和整理历史回填日级行为指标，缺失日期按 0 写入。
        """
        db = ScopedSession()
        try:
            start, end = self.__history_bounds(db, start_date, end_date)
            if not start or not end:
                return
            if force:
                WrappedDailyFact.delete_behavior_between(db, start.isoformat(), end.isoformat())
            daily = self.__collect_behavior(db, start, end)
            now = self.__now_text()
            for item_date in self.__date_range(start, end):
                payload = daily.get(item_date, {})
                WrappedDailyFact.upsert(
                    db,
                    item_date.isoformat(),
                    {
                        "download_count": payload.get("download_count", 0),
                        "transfer_count": payload.get("transfer_count", 0),
                        "transfer_success_count": payload.get("transfer_success_count", 0),
                        "transfer_fail_count": payload.get("transfer_fail_count", 0),
                        "site_breakdown": dict(payload.get("site_breakdown", {})),
                        "downloader_breakdown": dict(payload.get("downloader_breakdown", {})),
                        "behavior_ready": True,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
        finally:
            db.close()

    def scan_catalog(self) -> None:
        """
        扫描媒体服务器当前条目，保存用于高光榜单重算的媒体库画像。
        """
        rows = []
        snapshot_at = self.__now_text()
        media_chain = MediaServerChain()
        for mediaserver in ServiceConfigHelper.get_mediaserver_configs() or []:
            if not mediaserver or not mediaserver.enabled:
                continue
            try:
                libraries = media_chain.librarys(mediaserver.name) or []
            except Exception as err:
                logger.warning(f"Wrapped 扫描媒体服务器 {mediaserver.name} 媒体库失败：{err}")
                continue
            sync_libraries = mediaserver.sync_libraries or []
            for library in libraries:
                if sync_libraries and "all" not in sync_libraries and str(library.id) not in sync_libraries:
                    continue
                try:
                    # 单个媒体库扫描失败不应阻断其他库，避免 Wrapped 重建因局部服务异常整体失败。
                    for item in media_chain.items(server=mediaserver.name, library_id=library.id):
                        if not item or not item.item_id:
                            continue
                        rows.append(self.__catalog_row(snapshot_at, mediaserver.name, item))
                except Exception as err:
                    logger.warning(f"Wrapped 扫描媒体服务器 {mediaserver.name} 媒体库 {library.name} 失败：{err}")
        db = ScopedSession()
        try:
            WrappedCatalogSnapshot.replace_all(db, rows)
        finally:
            db.close()

    def daily_rollup(self) -> None:
        """
        生成当前日期的媒体库快照日事实，作为后续持续时间序列的基础。
        """
        db = ScopedSession()
        try:
            rows = WrappedCatalogSnapshot.list_all(db)
            now = self.__now_text()
            today = date.today().isoformat()
            media_statistics = DashboardChain().media_statistic() or []
            statistic = self.__merge_media_statistics(media_statistics)
            storage_total, storage_used = self.__storage_usage()
            genre_counter: Counter = Counter()
            country_counter: Counter = Counter()
            resolution_counter: Counter = Counter()
            dynamic_range_counter: Counter = Counter()
            subtitle_count = 0
            for row in rows:
                genre_counter.update(self.__normalize_list(row.genres))
                country_counter.update(self.__normalize_list(row.countries))
                if row.resolution_bucket:
                    resolution_counter[row.resolution_bucket] += 1
                if row.dynamic_range:
                    dynamic_range_counter[row.dynamic_range] += 1
                if row.has_subtitles:
                    subtitle_count += 1
            WrappedDailyFact.upsert(
                db,
                today,
                {
                    "library_total": len(rows) or statistic.movie_count + statistic.tv_count,
                    "movie_count": statistic.movie_count or self.__count_by_type(rows, ["Movie", "movie", "电影"]),
                    "tv_count": statistic.tv_count or self.__count_by_type(rows, ["Series", "show", "TV", "电视剧"]),
                    "episode_count": statistic.episode_count or 0,
                    "storage_used": storage_used,
                    "storage_total": storage_total,
                    "subtitle_covered_count": subtitle_count,
                    "genre_breakdown": dict(genre_counter),
                    "country_breakdown": dict(country_counter),
                    "resolution_breakdown": dict(resolution_counter),
                    "dynamic_range_breakdown": dict(dynamic_range_counter),
                    "catalog_ready": bool(rows or statistic.movie_count or statistic.tv_count),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            self.__update_state(
                self.DAILY_JOB_KEY,
                status="success",
                progress=100,
                message="Wrapped 日结完成",
                error=None,
                started_at=now,
                finished_at=self.__now_text(),
                updated_at=self.__now_text(),
            )
        finally:
            db.close()

    def __collect_behavior(self, db, start: date, end: date) -> Dict[date, Dict[str, Any]]:
        """
        汇总下载和整理历史到自然日维度。
        """
        daily: Dict[date, Dict[str, Any]] = defaultdict(lambda: {
            "download_count": 0,
            "transfer_count": 0,
            "transfer_success_count": 0,
            "transfer_fail_count": 0,
            "site_breakdown": Counter(),
            "downloader_breakdown": Counter(),
        })
        for row in db.query(DownloadHistory.date, DownloadHistory.downloader, DownloadHistory.torrent_site).all():
            item_date = self.__parse_history_date(row.date)
            if not item_date or item_date < start or item_date > end:
                continue
            daily[item_date]["download_count"] += 1
            if row.torrent_site:
                daily[item_date]["site_breakdown"][row.torrent_site] += 1
            if row.downloader:
                daily[item_date]["downloader_breakdown"][row.downloader] += 1
        for row in db.query(TransferHistory.date, TransferHistory.downloader, TransferHistory.status).all():
            item_date = self.__parse_history_date(row.date)
            if not item_date or item_date < start or item_date > end:
                continue
            daily[item_date]["transfer_count"] += 1
            if row.status:
                daily[item_date]["transfer_success_count"] += 1
            else:
                daily[item_date]["transfer_fail_count"] += 1
            if row.downloader:
                daily[item_date]["downloader_breakdown"][row.downloader] += 1
        return daily

    def __history_bounds(self, db, start_date: Optional[str], end_date: Optional[str]) -> Tuple[Optional[date], Optional[date]]:
        """
        推导行为历史回填边界，显式参数优先于历史表范围。
        """
        start = self.__date_from_text(start_date) if start_date else None
        end = self.__date_from_text(end_date) if end_date else date.today()
        if start:
            return start, end
        dates = []
        for value in db.query(DownloadHistory.date).filter(DownloadHistory.date.isnot(None)).all():
            parsed = self.__parse_history_date(value.date)
            if parsed:
                dates.append(parsed)
        for value in db.query(TransferHistory.date).filter(TransferHistory.date.isnot(None)).all():
            parsed = self.__parse_history_date(value.date)
            if parsed:
                dates.append(parsed)
        if not dates:
            return None, None
        return min(dates), end

    def __catalog_row(self, snapshot_at: str, server_name: str, item) -> Dict[str, Any]:
        """
        将媒体服务器条目转成 Wrapped 画像行，缺失字段保持空值并由前端降级。
        """
        return {
            "snapshot_at": snapshot_at,
            "server": server_name,
            "library": str(item.library) if item.library is not None else None,
            "item_id": str(item.item_id),
            "item_type": item.item_type,
            "title": item.title,
            "year": str(item.year) if item.year is not None else None,
            "tmdbid": item.tmdbid,
            "genres": self.__normalize_list(item.genres),
            "countries": self.__normalize_list(item.countries),
            "resolution_bucket": self.__resolution_bucket(item.resolution_bucket),
            "dynamic_range": self.__dynamic_range(item.dynamic_range),
            "has_subtitles": bool(item.has_subtitles),
            "subtitle_stream_count": item.subtitle_stream_count or 0,
            "audio_stream_count": item.audio_stream_count or 0,
            "extra": {},
            "created_at": snapshot_at,
        }

    def __metric_meta_index(self) -> Dict[str, schemas.WrappedMetricMeta]:
        """
        构建指标口径索引，避免前端把行为历史和快照投影混用。
        """
        first_date = self.__first_fact_date()
        catalog_date = self.__latest_catalog_date()
        return {
            "download": schemas.WrappedMetricMeta(
                metric="download",
                label=self.METRIC_LABELS["download"],
                history_mode="backfilled",
                history_start_at=first_date,
                comparable_to_previous_period=True,
                data_quality_note="来自下载历史，可按自然日真实回填。",
            ),
            "transfer": schemas.WrappedMetricMeta(
                metric="transfer",
                label=self.METRIC_LABELS["transfer"],
                history_mode="backfilled",
                history_start_at=first_date,
                comparable_to_previous_period=True,
                data_quality_note="来自整理历史，可按自然日真实回填。",
            ),
            "success_rate": schemas.WrappedMetricMeta(
                metric="success_rate",
                label=self.METRIC_LABELS["success_rate"],
                history_mode="backfilled",
                history_start_at=first_date,
                comparable_to_previous_period=True,
                data_quality_note="按整理成功数 / 整理总数计算。",
            ),
            "storage_used": schemas.WrappedMetricMeta(
                metric="storage_used",
                label=self.METRIC_LABELS["storage_used"],
                history_mode="projected",
                history_start_at=catalog_date,
                comparable_to_previous_period=False,
                data_quality_note="V1 仅代表最近快照或日结记录，不伪造上线前历史。",
            ),
            "library_total": schemas.WrappedMetricMeta(
                metric="library_total",
                label=self.METRIC_LABELS["library_total"],
                history_mode="projected",
                history_start_at=catalog_date,
                comparable_to_previous_period=False,
                data_quality_note="V1 仅代表媒体库扫描快照或后续日结状态。",
            ),
        }

    def __metric_card(
        self,
        metric: str,
        value: Optional[float],
        previous_value: Optional[float],
        metric_meta: Dict[str, schemas.WrappedMetricMeta],
        unit: Optional[str] = None,
    ) -> schemas.WrappedMetricCard:
        """
        构建单张总览卡片并计算变化率。
        """
        delta = None
        if previous_value not in [None, 0] and value is not None:
            delta = (value - previous_value) / previous_value
        return schemas.WrappedMetricCard(
            metric=metric,
            title=self.METRIC_LABELS[metric],
            value=value or 0,
            unit=unit,
            previous_value=previous_value,
            delta_ratio=delta,
            meta=metric_meta[metric],
        )

    def __range_window(
        self, range_name: schemas.WrappedRange
    ) -> Tuple[date, date, Optional[date], Optional[date], str]:
        """
        将前端范围转换为自然日窗口和默认展示粒度。
        """
        today = date.today()
        if range_name == "day":
            start = today - timedelta(days=29)
            return start, today, start - timedelta(days=30), start - timedelta(days=1), "day"
        if range_name == "week":
            start = today - timedelta(days=today.weekday())
            return start, today, start - timedelta(days=7), start - timedelta(days=1), "day"
        if range_name == "month":
            start = today.replace(day=1)
            previous_end = start - timedelta(days=1)
            previous_start = previous_end.replace(day=1)
            return start, today, previous_start, previous_end, "day"
        if range_name == "year":
            start = today.replace(month=1, day=1)
            previous_start = start.replace(year=start.year - 1)
            previous_end = start - timedelta(days=1)
            return start, today, previous_start, previous_end, "month"
        first = self.__first_fact_date()
        start = self.__date_from_text(first) if first else today - timedelta(days=29)
        return start, today, None, None, "month"

    def __list_facts(self, start: date, end: date) -> List[WrappedDailyFact]:
        """
        查询指定自然日区间内的 Wrapped 事实。
        """
        db = ScopedSession()
        try:
            return WrappedDailyFact.list_between(db, start.isoformat(), end.isoformat())
        finally:
            db.close()

    def __sum_metric(self, facts: List[WrappedDailyFact], metric: str) -> float:
        """
        汇总行为类指标数值。
        """
        if metric == "download":
            return float(sum(item.download_count or 0 for item in facts))
        if metric == "transfer":
            return float(sum(item.transfer_count or 0 for item in facts))
        return 0

    def __success_rate(self, facts: List[WrappedDailyFact]) -> Optional[float]:
        """
        计算整理成功率，无整理记录时返回 None 让前端展示空态。
        """
        total = sum(item.transfer_count or 0 for item in facts)
        if not total:
            return None
        success = sum(item.transfer_success_count or 0 for item in facts)
        return round(success * 100 / total, 2)

    def __latest_metric(self, facts: List[WrappedDailyFact], metric: str) -> Optional[float]:
        """
        获取范围内最近一条快照指标。
        """
        for item in sorted(facts, key=lambda fact: fact.stat_date, reverse=True):
            if not item.catalog_ready:
                continue
            if metric == "storage_used":
                return item.storage_used
            if metric == "library_total":
                return float(item.library_total or 0)
        return None

    def __latest_catalog_fact(self, facts: List[WrappedDailyFact]) -> Optional[WrappedDailyFact]:
        """
        从当前范围内选择最近一条媒体库快照事实。
        """
        for item in sorted(facts, key=lambda fact: fact.stat_date, reverse=True):
            if item.catalog_ready:
                return item
        db = ScopedSession()
        try:
            return WrappedDailyFact.latest_catalog(db)
        finally:
            db.close()

    def __ranking_group(
        self, key: str, title: str, data: Dict[str, Any], history_mode: schemas.WrappedHistoryMode
    ) -> schemas.WrappedRankingGroup:
        """
        将原始分布字典转换为前端可渲染榜单。
        """
        total = sum(float(value or 0) for value in data.values())
        items = [
            schemas.WrappedRankingItem(name=name, value=float(value or 0), ratio=(float(value or 0) / total if total else None))
            for name, value in sorted(data.items(), key=lambda item: item[1] or 0, reverse=True)[:8]
            if value
        ]
        note = "来自真实行为历史回填。" if history_mode == "backfilled" else "来自最近媒体库扫描快照，不代表过去真实库存。"
        return schemas.WrappedRankingGroup(key=key, title=title, history_mode=history_mode, items=items, data_quality_note=note)

    def __merge_breakdowns(self, facts: List[WrappedDailyFact], field: str) -> Dict[str, float]:
        """
        合并多日 JSON 分布字段。
        """
        counter: Counter = Counter()
        for fact in facts:
            value = getattr(fact, field) or {}
            counter.update({key: count or 0 for key, count in value.items()})
        return dict(counter)

    def __buckets(self, start: date, end: date, granularity: str) -> List[Tuple[date, date]]:
        """
        生成曲线展示桶，月粒度按自然月切分。
        """
        if granularity != "month":
            return [(item, item) for item in self.__date_range(start, end)]
        buckets = []
        cursor = start.replace(day=1)
        while cursor <= end:
            last_day = calendar.monthrange(cursor.year, cursor.month)[1]
            bucket_end = min(cursor.replace(day=last_day), end)
            buckets.append((cursor, bucket_end))
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        return buckets

    @staticmethod
    def __date_range(start: date, end: date) -> Iterable[date]:
        """
        生成包含起止日期的自然日序列。
        """
        cursor = start
        while cursor <= end:
            yield cursor
            cursor += timedelta(days=1)

    @staticmethod
    def __parse_history_date(value: Optional[str]) -> Optional[date]:
        """
        从历史表字符串日期中提取自然日。
        """
        if not value:
            return None
        return WrappedChain.__date_from_text(value[:10])

    @staticmethod
    def __date_from_text(value: Optional[str]) -> Optional[date]:
        """
        将 YYYY-MM-DD 字符串转为 date，非法值返回 None。
        """
        if not value:
            return None
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    @staticmethod
    def __now_text() -> str:
        """
        返回当前时间字符串，保持与项目历史表时间格式一致。
        """
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def __normalize_list(value: Any) -> List[str]:
        """
        标准化媒体服务器返回的列表型标签。
        """
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def __resolution_bucket(value: Any) -> Optional[str]:
        """
        将媒体服务器分辨率字段归一为 Wrapped 展示分桶。
        """
        if value is None:
            return None
        text = str(value).lower()
        if "4k" in text or "2160" in text or text == "uhd":
            return "4K"
        if "1080" in text:
            return "1080p"
        if "720" in text:
            return "720p"
        if "480" in text:
            return "480p"
        if text.isdigit():
            height = int(text)
            if height >= 2000:
                return "4K"
            if height >= 1000:
                return "1080p"
            if height >= 700:
                return "720p"
        return str(value)

    @staticmethod
    def __dynamic_range(value: Any) -> Optional[str]:
        """
        将动态范围字段归一为 HDR/Dolby Vision/SDR 等展示值。
        """
        if not value:
            return None
        text = str(value).upper()
        if "DOLBY" in text or "DV" == text:
            return "Dolby Vision"
        if "HDR" in text:
            return "HDR"
        if "SDR" in text:
            return "SDR"
        return str(value)

    def __first_fact_date(self) -> Optional[str]:
        """
        查询 Wrapped 最早可用事实日期。
        """
        db = ScopedSession()
        try:
            return WrappedDailyFact.first_date(db)
        finally:
            db.close()

    def __latest_catalog_date(self) -> Optional[str]:
        """
        查询 Wrapped 最近媒体库画像日期。
        """
        db = ScopedSession()
        try:
            latest = WrappedDailyFact.latest_catalog(db)
            if latest:
                return latest.stat_date
            latest_snapshot = WrappedCatalogSnapshot.latest_snapshot_at(db)
            return latest_snapshot[:10] if latest_snapshot else None
        finally:
            db.close()

    def __merge_media_statistics(self, values: List[schemas.Statistic]) -> schemas.Statistic:
        """
        合并多个媒体服务器返回的数量统计。
        """
        result = schemas.Statistic()
        for item in values:
            result.movie_count += item.movie_count or 0
            result.tv_count += item.tv_count or 0
            result.episode_count += item.episode_count or 0
            result.user_count += item.user_count or 0
        return result

    def __storage_usage(self) -> Tuple[float, float]:
        """
        统计媒体库目录总空间和已用空间，失败时返回 0。
        """
        total, available = 0.0, 0.0
        dirs = DirectoryHelper().get_dirs()
        storages = set([item.library_storage for item in dirs if item.library_storage])
        for storage in storages:
            usage = StorageChain().storage_usage(storage)
            if usage:
                total += usage.total or 0
                available += usage.available or 0
        return total, total - available

    @staticmethod
    def __count_by_type(rows: List[WrappedCatalogSnapshot], type_names: List[str]) -> int:
        """
        按媒体服务器类型名称粗略统计媒体数量，用于媒体服务器统计缺失时兜底。
        """
        normalized = {item.lower() for item in type_names}
        return sum(1 for row in rows if (row.item_type or "").lower() in normalized)

    def __update_state(self, job_key: str, **payload: Any) -> None:
        """
        更新 Wrapped 构建状态。
        """
        db = ScopedSession()
        try:
            WrappedBuildState.upsert(db, job_key, payload)
        finally:
            db.close()
