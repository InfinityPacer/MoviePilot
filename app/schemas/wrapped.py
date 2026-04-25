from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


WrappedRange = Literal["day", "week", "month", "year", "all"]
WrappedMetric = Literal["download", "transfer", "success_rate", "storage_used", "library_total"]
WrappedHistoryMode = Literal["backfilled", "snapshot", "projected", "optional"]


class WrappedMetricMeta(BaseModel):
    """
    Wrapped 单项指标的历史口径说明，供前端逐卡片展示数据质量提示。
    """
    # 指标标识
    metric: str = Field(description="指标标识")
    # 指标名称
    label: str = Field(description="指标名称")
    # 历史口径
    history_mode: WrappedHistoryMode = Field(description="历史口径")
    # 历史起始日期
    history_start_at: Optional[str] = Field(default=None, description="历史起始日期")
    # 是否可与上一周期比较
    comparable_to_previous_period: bool = Field(default=False, description="是否可与上一周期比较")
    # 数据质量说明
    data_quality_note: Optional[str] = Field(default=None, description="数据质量说明")


class WrappedMetricCard(BaseModel):
    """
    Wrapped 总览卡片数据，承载当前值、对比值和单项口径。
    """
    # 指标标识
    metric: str = Field(description="指标标识")
    # 展示标题
    title: str = Field(description="展示标题")
    # 当前周期数值
    value: float = Field(default=0, description="当前周期数值")
    # 展示单位
    unit: Optional[str] = Field(default=None, description="展示单位")
    # 上一周期数值
    previous_value: Optional[float] = Field(default=None, description="上一周期数值")
    # 相对上一周期变化率
    delta_ratio: Optional[float] = Field(default=None, description="相对上一周期变化率")
    # 指标口径
    meta: WrappedMetricMeta = Field(description="指标口径")


class WrappedSeriesPoint(BaseModel):
    """
    Wrapped 曲线点，按前端选定粒度聚合后的单桶数据。
    """
    # 时间桶开始日期
    bucket: str = Field(description="时间桶开始日期")
    # 当前周期数值
    value: Optional[float] = Field(default=None, description="当前周期数值")
    # 上一周期同位数值
    previous_value: Optional[float] = Field(default=None, description="上一周期同位数值")


class WrappedSeries(BaseModel):
    """
    Wrapped 指标曲线响应，包含粒度、序列和指标口径。
    """
    # 查询时间范围
    range: WrappedRange = Field(description="查询时间范围")
    # 查询指标
    metric: WrappedMetric = Field(description="查询指标")
    # 展示粒度
    granularity: Literal["day", "week", "month"] = Field(description="展示粒度")
    # 曲线数据
    series: List[WrappedSeriesPoint] = Field(default_factory=list, description="曲线数据")
    # 指标口径
    metric_meta: WrappedMetricMeta = Field(description="指标口径")
    # 是否启用对比
    compare_enabled: bool = Field(default=False, description="是否启用对比")
    # 空数据原因
    empty_reason: Optional[str] = Field(default=None, description="空数据原因")


class WrappedOverview(BaseModel):
    """
    Wrapped 总览响应，汇总当前范围核心卡片和构建状态。
    """
    # 查询时间范围
    range: WrappedRange = Field(description="查询时间范围")
    # 展示粒度
    granularity: Literal["day", "week", "month"] = Field(description="展示粒度")
    # 总览卡片
    cards: List[WrappedMetricCard] = Field(default_factory=list, description="总览卡片")
    # 口径索引
    metric_meta: Dict[str, WrappedMetricMeta] = Field(default_factory=dict, description="口径索引")
    # 是否启用对比
    compare_enabled: bool = Field(default=False, description="是否启用对比")
    # 空数据原因
    empty_reason: Optional[str] = Field(default=None, description="空数据原因")
    # 构建状态
    rebuild_status: Optional["WrappedRebuildStatus"] = Field(default=None, description="构建状态")


class WrappedRankingItem(BaseModel):
    """
    Wrapped 榜单项，用于来源、下载器和媒体库构成排行。
    """
    # 榜单项名称
    name: str = Field(description="榜单项名称")
    # 榜单项数值
    value: float = Field(default=0, description="榜单项数值")
    # 榜单项占比
    ratio: Optional[float] = Field(default=None, description="榜单项占比")


class WrappedRankingGroup(BaseModel):
    """
    Wrapped 榜单分组，描述一个统计维度的 Top 构成。
    """
    # 分组标识
    key: str = Field(description="分组标识")
    # 分组名称
    title: str = Field(description="分组名称")
    # 历史口径
    history_mode: WrappedHistoryMode = Field(description="历史口径")
    # 榜单项
    items: List[WrappedRankingItem] = Field(default_factory=list, description="榜单项")
    # 数据质量说明
    data_quality_note: Optional[str] = Field(default=None, description="数据质量说明")


class WrappedRankings(BaseModel):
    """
    Wrapped 榜单响应，返回来源贡献和当前媒体库构成。
    """
    # 查询时间范围
    range: WrappedRange = Field(description="查询时间范围")
    # 榜单分组
    groups: List[WrappedRankingGroup] = Field(default_factory=list, description="榜单分组")
    # 空数据原因
    empty_reason: Optional[str] = Field(default=None, description="空数据原因")


class WrappedHighlight(BaseModel):
    """
    Wrapped 高光故事，供前端报告模块展示关键里程碑。
    """
    # 高光标识
    key: str = Field(description="高光标识")
    # 高光标题
    title: str = Field(description="高光标题")
    # 高光数值
    value: Optional[float] = Field(default=None, description="高光数值")
    # 高光日期
    date: Optional[str] = Field(default=None, description="高光日期")
    # 历史口径
    history_mode: WrappedHistoryMode = Field(description="历史口径")
    # 展示说明
    description: Optional[str] = Field(default=None, description="展示说明")


class WrappedHighlights(BaseModel):
    """
    Wrapped 高光响应，聚合当前范围内的峰值和里程碑。
    """
    # 查询时间范围
    range: WrappedRange = Field(description="查询时间范围")
    # 高光列表
    highlights: List[WrappedHighlight] = Field(default_factory=list, description="高光列表")
    # 空数据原因
    empty_reason: Optional[str] = Field(default=None, description="空数据原因")


class WrappedAvailability(BaseModel):
    """
    Wrapped 模块能力响应，前端据此隐藏不支持的增强模块。
    """
    # 是否存在可展示的行为历史
    behavior_history_supported: bool = Field(default=False, description="是否存在可展示的行为历史")
    # 是否存在媒体库画像
    catalog_snapshot_supported: bool = Field(default=False, description="是否存在媒体库画像")
    # 是否可展示字幕覆盖模块
    subtitle_coverage_supported: bool = Field(default=False, description="是否可展示字幕覆盖模块")
    # 是否可展示观影历史模块
    watch_history_supported: bool = Field(default=False, description="是否可展示观影历史模块")
    # 是否可展示家庭成员维度
    watch_user_dimension_supported: bool = Field(default=False, description="是否可展示家庭成员维度")
    # 当前能力说明
    notes: List[str] = Field(default_factory=list, description="当前能力说明")


class WrappedRebuildRequest(BaseModel):
    """
    Wrapped 重建请求，控制是否重建行为历史和媒体库画像。
    """
    # 是否回填行为历史
    include_behavior: bool = Field(default=True, description="是否回填行为历史")
    # 是否扫描媒体库画像
    include_catalog: bool = Field(default=True, description="是否扫描媒体库画像")
    # 是否强制重建现有数据
    force: bool = Field(default=False, description="是否强制重建现有数据")
    # 回填开始日期
    start_date: Optional[str] = Field(default=None, description="回填开始日期")
    # 回填结束日期
    end_date: Optional[str] = Field(default=None, description="回填结束日期")


class WrappedRebuildStatus(BaseModel):
    """
    Wrapped 重建状态，记录后台回填、扫描和日结进度。
    """
    # 任务标识
    job_key: str = Field(default="wrapped_rebuild", description="任务标识")
    # 任务状态
    status: Literal["idle", "running", "success", "failed"] = Field(default="idle", description="任务状态")
    # 任务进度
    progress: int = Field(default=0, description="任务进度")
    # 状态消息
    message: Optional[str] = Field(default=None, description="状态消息")
    # 错误信息
    error: Optional[str] = Field(default=None, description="错误信息")
    # 开始时间
    started_at: Optional[str] = Field(default=None, description="开始时间")
    # 结束时间
    finished_at: Optional[str] = Field(default=None, description="结束时间")
    # 更新时间
    updated_at: Optional[str] = Field(default=None, description="更新时间")
    # 任务参数
    payload: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
