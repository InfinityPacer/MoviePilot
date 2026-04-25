from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends

from app import schemas
from app.chain.wrapped import WrappedChain
from app.core.security import verify_token

router = APIRouter()


@router.get("/overview", summary="Wrapped 总览", response_model=schemas.WrappedOverview)
def overview(range: schemas.WrappedRange = "month", _: schemas.TokenPayload = Depends(verify_token)) -> Any:
    """
    查询 Wrapped 当前范围的核心总览卡片。
    """
    return WrappedChain().overview(range)


@router.get("/series", summary="Wrapped 曲线", response_model=schemas.WrappedSeries)
def series(
    range: schemas.WrappedRange = "month",
    metric: schemas.WrappedMetric = "transfer",
    _: schemas.TokenPayload = Depends(verify_token),
) -> Any:
    """
    查询 Wrapped 指标曲线，返回单指标口径和聚合粒度。
    """
    return WrappedChain().series(range, metric)


@router.get("/rankings", summary="Wrapped 榜单", response_model=schemas.WrappedRankings)
def rankings(range: schemas.WrappedRange = "month", _: schemas.TokenPayload = Depends(verify_token)) -> Any:
    """
    查询 Wrapped 来源贡献和媒体库构成榜单。
    """
    return WrappedChain().rankings(range)


@router.get("/highlights", summary="Wrapped 高光", response_model=schemas.WrappedHighlights)
def highlights(range: schemas.WrappedRange = "month", _: schemas.TokenPayload = Depends(verify_token)) -> Any:
    """
    查询 Wrapped 当前范围的高光故事。
    """
    return WrappedChain().highlights(range)


@router.get("/availability", summary="Wrapped 能力", response_model=schemas.WrappedAvailability)
def availability(_: schemas.TokenPayload = Depends(verify_token)) -> Any:
    """
    查询 Wrapped 可展示模块能力。
    """
    return WrappedChain().availability()


@router.post("/rebuild", summary="重建 Wrapped 数据", response_model=schemas.WrappedRebuildStatus)
def rebuild(
    request: schemas.WrappedRebuildRequest,
    background_tasks: BackgroundTasks,
    _: schemas.TokenPayload = Depends(verify_token),
) -> Any:
    """
    触发 Wrapped 后台重建任务。
    """
    chain = WrappedChain()
    status = chain.schedule_rebuild(request)
    background_tasks.add_task(chain.rebuild, request)
    return status


@router.get("/rebuild/status", summary="Wrapped 重建状态", response_model=schemas.WrappedRebuildStatus)
def rebuild_status(_: schemas.TokenPayload = Depends(verify_token)) -> Any:
    """
    查询 Wrapped 后台重建状态。
    """
    return WrappedChain().rebuild_status()
