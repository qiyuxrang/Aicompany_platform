"""Tender 采集 Worker（TENDER_V1 规格第十二章）。

触发方式
--------
管理命令 `run_tender_worker`，由 **Windows Task Scheduler 或 cron 外部触发**。
**不新建平台级 Scheduler，也不新建常驻进程**——与主平台
`portal/management/commands/run_product_worker.py` 的既有范式一致。

机制（照搬主平台 Worker 范式，见 `portal/product_worker.py:39-86`）
------------------------------------------------------------------
| 机制 | 本模块实现 |
|---|---|
| 批次状态 | `TenderFetchRun.state` |
| 租约 | `lease_until` + `TENDER_LEASE_SECONDS`；**租约有效期间同一来源的再次触发会被跳过**（并发保护） |
| 认领 | `_claim_run`：RUNNING（含过期）一律拒绝自动接管；已正常结束的 WAITING_RETRY 才经 fence CAS 重试 |
| 并发栅栏 | `fence`；**终态写入前 `_finish` 校验 `(pk, fence, RUNNING, 租约未过期)`**，stale worker 的写入被拒绝（`lease_lost`） |
| 重试 | `attempt_count` + `TENDER_MAX_ATTEMPTS`；用尽 → `FAILED` 终态，之后的触发开新批次（失败恢复） |
| 幂等 | 依赖 `ingest_fetch_result` 的版本摘要与事件 `dedupe_key`——重试/并发都不会重复生成 Snapshot / NoticeVersion / Opportunity / Event |

诚实边界（TEN-07 审计声明）：抓取过程中的逐条写入**不做** per-step guard——
入库幂等使 stale worker 的重复写入无副作用，真正需要独占的是**终态与健康计数**，
由 `_finish` 的 CAS 保证。这与主平台"每步 `_guard`"略有差异，是刻意的最小实现。

哪些**不重建**：调度器、队列、进程管理、Web 管理界面——
这些属于平台能力或外部工具，Tender 只提供"被触发时执行一次"的入口。

规格第十二章要求
----------------
- 多来源**逐个**执行（不并发，避免对来源站点造成压力）
- **单源失败隔离**：一个来源异常不影响其他来源
- retry / idempotency / 重复执行安全
- Source Health / Last Success / Last Failure
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import transaction

from . import tender_service
from .security import audit
from .tender_models import TenderFetchRun, TenderSource
from .tender_sources import get_adapter
from .tender_sources.base import SourceBlocked
from .tender_window import classify_window_date, list_window_candidates

__all__ = ["SourceRunResult", "WorkerSummary", "run_source", "run_all", "DEFAULT_PAGE_SIZE"]

DEFAULT_PAGE_SIZE = 20

# 可被登记的失败码。未列入的错误一律降级为 `execution_failed`，
# 与主平台 `product_worker.py:375-385` 的做法一致：错误码必须可枚举、可告警。
KNOWN_ERROR_CODES = frozenset({
    "source_not_registered",
    "preflight_failed",
    "list_failed",
    "detail_failed",
    "ingest_failed",
    "lease_lost",
    "execution_failed",
})


def _lease_seconds() -> int:
    return int(getattr(settings, "TENDER_LEASE_SECONDS", 180))


def _max_attempts() -> int:
    return int(getattr(settings, "TENDER_MAX_ATTEMPTS", 3))


def _normalise_code(code: str) -> str:
    return code if code in KNOWN_ERROR_CODES else "execution_failed"


@dataclass
class SourceRunResult:
    """单来源一次执行的产出，同时用于统计与证据。"""

    source_code: str
    state: str
    run_id: int | None = None
    complete: bool = False
    listed: int = 0
    ingested: int = 0
    new_notices: int = 0
    new_versions: int = 0
    events: int = 0
    skipped: int = 0
    error_code: str = ""
    error_detail: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkerSummary:
    started_at: str
    finished_at: str
    sources: list[SourceRunResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.sources if item.state == "SUCCESS")

    @property
    def blocked(self) -> int:
        return sum(1 for item in self.sources if item.state == "BLOCKED")

    @property
    def skipped(self) -> int:
        """因已有执行中批次而跳过的来源数（并发保护，不是失败）。"""
        return sum(1 for item in self.sources if item.state == "SKIPPED")

    @property
    def failed(self) -> int:
        """本轮未成功的来源数（含等待重试）。

        对调度器而言「等待重试」与「失败」都是"这次没成功"。
        若只统计 FAILED，持续失败的来源在退出码上会表现为成功，告警就失效了。
        """
        return sum(1 for item in self.sources
                   if item.state in (TenderFetchRun.State.FAILED,
                                     TenderFetchRun.State.WAITING_RETRY))

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": len(self.sources),
            "succeeded": self.succeeded,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "failed": self.failed,
            "sources": [item.to_dict() for item in self.sources],
        }


class LeaseHeld(Exception):
    """该来源已有执行中批次（无论租约是否过期），后来的触发不得重复出站。"""

    def __init__(self, source_code: str, run_id: int, recovery_required: bool = False):
        self.source_code = source_code
        self.run_id = run_id
        self.recovery_required = recovery_required
        super().__init__(f"{source_code} 已有执行中批次 run#{run_id}，本次跳过")


@transaction.atomic
def _claim_run(source: TenderSource, *, now: datetime) -> tuple[TenderFetchRun, int]:
    """原子认领一个可执行批次，返回 (run, fence)。

    先锁来源行以串行化同源首次认领；任何 RUNNING（含租约已过期）
    都阻止再次出站。仅已正常结束的 WAITING_RETRY 可以 fence CAS 重试。
    租约过期不是进程退出的证明，须人工确认后才解除阻断。
    """
    TenderSource.objects.select_for_update().get(pk=source.pk)
    active = (TenderFetchRun.objects.filter(source=source, state=TenderFetchRun.State.RUNNING)
              .order_by('-created_at', '-id').first())
    if active is not None:
        raise LeaseHeld(source.code, active.pk,
                        recovery_required=active.lease_until is None or active.lease_until <= now)

    candidates = (TenderFetchRun.objects.filter(source=source, state=TenderFetchRun.State.WAITING_RETRY)
                  .select_for_update().order_by('-created_at', '-id'))
    stale = candidates.first()
    if stale is not None:
        previous_fence = stale.fence
        updated = TenderFetchRun.objects.filter(
            pk=stale.pk, fence=previous_fence, state=TenderFetchRun.State.WAITING_RETRY).update(
            fence=previous_fence + 1,
            state=TenderFetchRun.State.RUNNING,
            attempt_count=stale.attempt_count + 1,
            version=stale.version + 1,
            started_at=now,
            finished_at=None,
            lease_until=now + timedelta(seconds=_lease_seconds()),
            error_code="", error_detail="",
        )
        if updated == 0:
            raise LeaseHeld(source.code, stale.pk)
        stale.refresh_from_db()
        return stale, stale.fence

    run = TenderFetchRun.objects.create(
        source=source, state=TenderFetchRun.State.RUNNING,
        fence=1, attempt_count=1, started_at=now, version=1,
        lease_until=now + timedelta(seconds=_lease_seconds()),
    )
    return run, 1


def _finish(run: TenderFetchRun, fence: int, *, state: str, moment: datetime,
            stats: dict | None = None, error_code: str = "", error_detail: str = "") -> bool:
    """以 CAS 写入批次终态。

    终态更新要求 `(pk, fence, RUNNING)` 仍成立且**租约未过期**——
    旧 worker（人工确认后 fence 已递增、或租约已过期）的写入在此被拒绝，
    返回 False。调用方据此**不得**再写来源健康事件。
    """
    values: dict = {"state": state, "finished_at": moment, "lease_until": None,
                    "error_code": error_code, "error_detail": error_detail[:500]}
    if stats is not None:
        values["stats"] = stats
    updated = TenderFetchRun.objects.filter(
        pk=run.pk, fence=fence, state=TenderFetchRun.State.RUNNING,
        lease_until__gt=moment,
    ).update(**values, version=run.version + 1)
    if updated:
        run.state = state
        run.version += 1
    return bool(updated)


def run_source(
    source: TenderSource,
    *,
    adapter=None,
    now: datetime | None = None,
    actor=None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> SourceRunResult:
    """执行单个来源的一次采集。

    **异常绝不外泄**：任何失败都被转换为 `TenderFetchRun` 状态 + 来源健康事件，
    以保证调用方（多来源循环）不会被单个来源拖垮。
    唯一例外是 `LeaseHeld`——那是并发保护的正常表现，由 `run_all` 记为 SKIPPED。

    `adapter` 可注入，便于离线测试；生产路径按 `source.adapter_code` 查注册表。
    """
    if not getattr(settings, "PORTAL_TENDER_INGESTION_ENABLED", False):
        return SourceRunResult(source.code, TenderFetchRun.State.BLOCKED, error_code="ingestion_disabled")
    moment = now or datetime.now(timezone.utc)
    try:
        run, fence = _claim_run(source, now=moment)
    except LeaseHeld as held:
        return SourceRunResult(
            source_code=source.code, state=TenderFetchRun.State.SKIPPED,
            run_id=held.run_id, error_code='recovery_required' if held.recovery_required else '',
            notes=[str(held)],
        )
    result = SourceRunResult(source_code=source.code, state=TenderFetchRun.State.RUNNING,
                             run_id=run.pk)

    def completion_time():
        return moment if now is not None else datetime.now(timezone.utc)

    # 1) 取得适配器
    try:
        source_adapter = adapter if adapter is not None else get_adapter(
            source.adapter_code or source.code)
    except Exception as error:  # noqa: BLE001
        return _finish_failure(source, run, fence, result, completion_time(),
                               "source_not_registered", str(error))

    # 2) 预检：未过预检不得进入采集
    try:
        preflight = source_adapter.preflight()
    except Exception as error:  # noqa: BLE001
        return _finish_failure(source, run, fence, result, completion_time(),
                               "preflight_failed", str(error))

    if preflight.blocked:
        reason = ",".join(item.value for item in preflight.reasons) or "unknown"
        blocked_at = completion_time()
        if _finish(run, fence, state=TenderFetchRun.State.BLOCKED, moment=blocked_at,
                   stats={"probes": len(preflight.probes), "reasons": reason},
                   error_code="preflight_failed", error_detail=preflight.detail):
            tender_service.record_source_health(
                source, event_type="PREFLIGHT_BLOCKED", reason_code=reason,
                detail=preflight.detail, payload={"probes": len(preflight.probes)},
                occurred_at=blocked_at, actor=actor,
            )
        result.state = TenderFetchRun.State.BLOCKED if run.state == TenderFetchRun.State.BLOCKED else TenderFetchRun.State.SKIPPED
        result.error_code = "preflight_failed"
        result.error_detail = preflight.detail
        result.notes.append("预检未通过，按规格不进入采集")
        return result

    # 3) 全国来源必须扫描到上轮完整边界，不能只截断第一页。
    listing = None
    try:
        max_pages = max(1, min(int(source.fetch_policy.get('max_pages', 3)), 10))
        max_candidates = max(1, min(int(source.fetch_policy.get('max_candidates', 100)), 500))
        if not source.initial_coverage_complete:
            pages = max_pages if source.code == 'ccgp_national' else 1
            listing = list_window_candidates(source_adapter, max_pages=pages,
                                             max_candidates=max_candidates)
            refs = listing.refs
        elif source.code == 'ccgp_national' and hasattr(source_adapter, 'list_incremental'):
            previous = source.trusted_checkpoint.get('head_ids')
            if not previous:
                raise ValueError('没有可信初始边界，禁止增量采集')
            listing = source_adapter.list_incremental(previous, max_pages=max_pages, page_size=page_size)
            refs = listing.refs
        else:
            raise ValueError('此来源没有可信增量扫描边界')
    except SourceBlocked as error:
        return _finish_blocked(source, run, fence, result, completion_time(), error, actor)
    except Exception as error:  # noqa: BLE001
        return _finish_failure(source, run, fence, result, completion_time(), "list_failed", str(error))

    result.listed = len(refs)

    # 4) 逐条抓详情并入库。单条失败不影响整批。
    for ref in refs:
        try:
            fetched = source_adapter.fetch_detail(ref)
            if not source.initial_coverage_complete:
                verified_date = (fetched.source_metadata.get('detail_published_at')
                                 or fetched.source_metadata.get('list_published_at')
                                 or ref.published_at)
                if classify_window_date(verified_date) in ('before', 'after'):
                    result.skipped += 1
                    continue
            outcome = tender_service.ingest_fetch_result(
                fetched, source=source, run=run, actor=actor)
        except SourceBlocked as error:
            result.skipped += 1
            result.notes.append(f"{ref.source_notice_id}: {error.detail or '来源阻塞'}")
            continue
        except Exception as error:  # noqa: BLE001
            result.skipped += 1
            result.notes.append(f"{ref.source_notice_id}: {type(error).__name__}: {error}")
            continue

        result.ingested += 1
        result.new_notices += int(outcome.notice_created)
        result.new_versions += int(outcome.version_created)
        result.events += len(outcome.events)

    # 注入的测试时间沿用原契约；真实执行以完成时刻检查租约。
    finish_at = completion_time()
    # 5) 仅完整扫描且全部详情成功时推进全国边界。
    result.complete = bool(listing is not None and listing.complete and result.skipped == 0)
    stats = {"listed": result.listed, "ingested": result.ingested, "skipped": result.skipped,
             "new_notices": result.new_notices, "new_versions": result.new_versions,
             "events": result.events, "complete": result.complete}
    if listing is not None and not result.complete:
        result.error_code = 'detail_failed' if result.skipped else 'list_failed'
        result.error_detail = listing.reason or '部分详情抓取失败'
        finished = _finish(run, fence, state=TenderFetchRun.State.WAITING_RETRY,
                           moment=finish_at, stats=stats,
                           error_code=result.error_code, error_detail=result.error_detail)
        result.state = TenderFetchRun.State.WAITING_RETRY if finished else TenderFetchRun.State.SKIPPED
        result.complete = False
        return result
    with transaction.atomic():
        finished = _finish(run, fence, state=TenderFetchRun.State.SUCCESS,
                           moment=finish_at, stats=stats)
        if finished and listing is not None and hasattr(listing, 'head_ids'):
            checkpoint = {'head_ids': listing.head_ids, 'pages_seen': listing.pages_seen}
            run.checkpoint = checkpoint
            run.save(update_fields=['checkpoint'])
            source.initial_coverage_complete = True
            source.trusted_checkpoint = checkpoint
            source.save(update_fields=['initial_coverage_complete', 'trusted_checkpoint'])
    if finished:
        tender_service.record_source_health(
            source, event_type="PREFLIGHT_OK", detail="预检与采集均正常。",
            occurred_at=finish_at, actor=actor,
        )
    result.state = TenderFetchRun.State.SUCCESS if finished else TenderFetchRun.State.SKIPPED
    result.complete = result.complete and finished
    return result


def _finish_blocked(source: TenderSource, run: TenderFetchRun, fence: int,
                    result: SourceRunResult, moment: datetime,
                    error: SourceBlocked, actor) -> SourceRunResult:
    reason = ",".join(item.value for item in error.reasons) or "unknown"
    finished = _finish(run, fence, state=TenderFetchRun.State.BLOCKED, moment=moment,
                       error_code="list_failed", error_detail=error.detail)
    if finished:
        tender_service.record_source_health(
            source, event_type="PREFLIGHT_BLOCKED", reason_code=reason,
            detail=error.detail, occurred_at=moment, actor=actor,
        )
    result.state = TenderFetchRun.State.BLOCKED if finished else TenderFetchRun.State.SKIPPED
    result.error_code = "list_failed"
    result.error_detail = error.detail
    return result


def _finish_failure(source: TenderSource, run: TenderFetchRun, fence: int,
                    result: SourceRunResult, moment: datetime,
                    code: str, detail: str) -> SourceRunResult:
    code = _normalise_code(code)
    # WAITING_RETRY 的判定只影响"是否值得重试"；两种终态都通过 CAS 写入。
    state = (TenderFetchRun.State.FAILED if run.attempt_count >= _max_attempts()
             else TenderFetchRun.State.WAITING_RETRY)
    finished = _finish(run, fence, state=state, moment=moment,
                       error_code=code, error_detail=detail)
    if finished:
        tender_service.record_source_health(
            source, event_type="FETCH_FAILED", reason_code=code, detail=detail,
            occurred_at=moment,
        )
    result.state = state if finished else TenderFetchRun.State.SKIPPED
    result.error_code = code
    result.error_detail = detail[:500]
    return result


def run_all(
    *,
    sources: list[TenderSource] | None = None,
    adapter_factory=None,
    now: datetime | None = None,
    actor=None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> WorkerSummary:
    """按顺序执行全部启用来源。

    **单源失败隔离**：每个来源独立 try/except，一个来源崩溃不影响其余来源。
    顺序执行（非并发）是有意为之——避免对来源站点造成并发压力。
    """
    if not getattr(settings, "PORTAL_TENDER_INGESTION_ENABLED", False):
        raise RuntimeError('Tender 采集未获启用')
    moment = now or datetime.now(timezone.utc)
    targets = list(sources) if sources is not None else list(
        TenderSource.objects.filter(enabled=True).order_by("code"))

    summary = WorkerSummary(started_at=moment.isoformat(), finished_at="")
    for source in targets:
        # 适配器构造也必须在隔离范围内——来源配置错误（例如 adapter_code 写错、
        # 适配器初始化抛异常）同样不该拖垮整轮采集。
        try:
            adapter = adapter_factory(source) if adapter_factory else None
            summary.sources.append(run_source(
                source, adapter=adapter, now=moment, actor=actor, page_size=page_size))
        except Exception as error:  # noqa: BLE001 - 隔离兜底：绝不让单个来源中断整轮
            summary.sources.append(SourceRunResult(
                source_code=source.code,
                state=TenderFetchRun.State.FAILED,
                error_code="execution_failed",
                error_detail=f"{type(error).__name__}: {error}"[:500],
                notes=["未预期的异常已被 worker 隔离"],
            ))

    finished = datetime.now(timezone.utc)
    summary.finished_at = finished.isoformat()
    audit(
        actor, "tender_worker_run", f"sources={len(summary.sources)}",
        changes=["state"],
    )
    return summary


def preflight_source(source: TenderSource, *, adapter=None) -> dict:
    """只做预检、不采集、不写任何业务数据。供调度部署前验证连通性与合规状态。"""
    if not getattr(settings, "PORTAL_TENDER_INGESTION_ENABLED", False):
        return {"source_code": source.code, "reachable": False, "reasons": ["ingestion_disabled"], "detail": "Tender 采集未获启用"}
    try:
        source_adapter = adapter if adapter is not None else get_adapter(
            source.adapter_code or source.code)
        preflight = source_adapter.preflight()
        return {"source_code": source.code, "reachable": not preflight.blocked,
                "reasons": [item.value for item in preflight.reasons],
                "detail": preflight.detail[:500]}
    except Exception as error:  # noqa: BLE001 - 预检入口本身兜底
        return {"source_code": source.code, "reachable": False,
                "reasons": ["preflight_failed"],
                "detail": f"{type(error).__name__}: {error}"[:500]}


def summary_json(summary: WorkerSummary) -> str:
    return json.dumps(summary.to_dict(), ensure_ascii=False, indent=2)
