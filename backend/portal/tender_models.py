"""Tender 数据模型（TENDER_V1 规格第六章）。

移植说明
--------
本文件最终整体移动到 `backend/portal/tender_models.py`，并在
`backend/portal/models.py` 末尾以一行导入挂载（与 product / hr 的既有范式一致）。

可移植性设计：
  - 用户外键一律使用 `settings.AUTH_USER_MODEL`，隔离期为 `auth.User`，
    移植后自动指向 `portal.User`，**无需改模型定义**。
  - 不指定 `db_table`，由 app_label 决定；移植时由 portal 的 migration 重建表名。
  - 不实现任何用户 / 权限 / 审计 / 文件存储底座——这些全部复用主平台。

V1 实现范围（规格第六章）：
  TenderSource / TenderFetchRun / TenderSnapshot / TenderNotice /
  TenderNoticeVersion / TenderOpportunity / TenderOpportunityEvent / TenderAnalysis

V1 **不实现**（规格明令）：
  TenderAssessment / TenderDecision / QualificationScore / BidDocument / BidGeneration
"""

from __future__ import annotations

from django.conf import settings
import uuid

from django.db import models
from django.db.models import Q

__all__ = [
    "TenderFetchRun",
    "TenderManualRefresh",
    "TenderRecoveryAudit",
    "TenderNotice",
    "TenderNoticeVersion",
    "TenderOpportunity",
    "TenderOpportunityEvent",
    "TenderSnapshot",
    "TenderSource",
    "TenderSourceHealthEvent",
]


# ---------------------------------------------------------------------------
# 来源与抓取
# ---------------------------------------------------------------------------


class TenderSource(models.Model):
    """来源配置。Admin 维护，不由采集流程创建。"""

    class Health(models.TextChoices):
        UNKNOWN = "unknown", "未检测"
        OK = "ok", "正常"
        DEGRADED = "degraded", "降级"
        BLOCKED = "blocked", "被阻塞"
        ERROR = "error", "异常"

    code = models.SlugField(unique=True)
    name = models.CharField("名称", max_length=120)
    base_url = models.URLField("站点入口", max_length=500, blank=True)
    adapter_code = models.SlugField("适配器标识", max_length=60)
    enabled = models.BooleanField("启用", default=False)
    fetch_policy = models.JSONField("抓取策略", default=dict, blank=True)

    # Source Health / Last Success / Last Failure（规格第十二章）
    health_state = models.CharField("健康状态", max_length=20, choices=Health, default=Health.UNKNOWN)
    health_detail = models.CharField("健康说明", max_length=500, blank=True)
    consecutive_failures = models.PositiveIntegerField("连续失败次数", default=0)
    last_success_at = models.DateTimeField("最近成功", null=True, blank=True)
    last_failure_at = models.DateTimeField("最近失败", null=True, blank=True)

    initial_coverage_complete = models.BooleanField(default=False)
    trusted_checkpoint = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.name}({self.code})"


class TenderFetchRun(models.Model):
    """单来源的一次采集批次。

    并发与重试机制**照搬主平台既有范式**（`portal/product_worker.py`）：
    租约 lease_until + fencing fence + attempt_count 上限 + version 乐观锁 + checkpoint。
    """

    class State(models.TextChoices):
        QUEUED = "QUEUED", "排队"
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "成功"
        BLOCKED = "BLOCKED", "来源阻塞"
        FAILED = "FAILED", "失败"
        WAITING_RETRY = "WAITING_RETRY", "等待重试"
        # 该来源已有租约有效的批次在执行时，后来的触发直接跳过（不重复采集）。
        SKIPPED = "SKIPPED", "跳过（已有执行中批次）"

    source = models.ForeignKey(TenderSource, on_delete=models.CASCADE, related_name="runs")
    state = models.CharField("状态", max_length=20, choices=State, default=State.QUEUED)
    fence = models.PositiveIntegerField("租约栅栏", default=0)
    lease_until = models.DateTimeField("租约到期", null=True, blank=True)
    attempt_count = models.PositiveIntegerField("尝试次数", default=0)
    version = models.PositiveBigIntegerField("版本", default=1)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    error_code = models.CharField("错误码", max_length=80, blank=True)
    error_detail = models.CharField("错误说明", max_length=500, blank=True)
    stats = models.JSONField("统计", default=dict, blank=True)
    checkpoint = models.JSONField("检查点", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=['source'], condition=Q(state='RUNNING'),
                                    name='tender_one_running_per_source'),
        ]
        indexes = [
            models.Index(fields=["state", "created_at"], name="tender_run_state_time"),
            models.Index(fields=["source", "-created_at"], name="tender_run_src_time"),
        ]

    def __str__(self) -> str:
        return f"run#{self.pk} {self.source_id} {self.state}"


class TenderManualRefresh(models.Model):
    """一次人工申请的来源快照与持久执行进度。"""

    class State(models.TextChoices):
        QUEUED = 'QUEUED', '等待执行'
        RUNNING = 'RUNNING', '执行中'
        SUCCESS = 'SUCCESS', '完整完成'
        PARTIAL = 'PARTIAL', '部分更新'
        INTERRUPTED = 'INTERRUPTED', '中断'
        FAILED = 'FAILED', '失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    active_slot = models.PositiveSmallIntegerField(default=1, editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=20, choices=State, default=State.QUEUED)
    source_codes = models.JSONField(default=list)
    source_plan = models.JSONField(default=dict)
    results = models.JSONField(default=dict)
    fence = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(active_slot=1), name='tender_refresh_single_slot'),
            models.UniqueConstraint(fields=['active_slot'], condition=Q(state__in=['QUEUED', 'RUNNING']),
                                    name='tender_one_active_refresh'),
        ]


class TenderConsumerHeartbeat(models.Model):
    slot = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    updated_at = models.DateTimeField()


class TenderRecoveryAudit(models.Model):
    """不可依赖进程内审计存储的操作员停机确认记录。"""
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    target_type = models.CharField(max_length=12)
    target_id = models.CharField(max_length=40)
    reason = models.CharField(max_length=200)
    confirmed_at = models.DateTimeField(auto_now_add=True)


class TenderSnapshot(models.Model):
    """原始快照：抓取到的字节 + 抓取元数据。

    存储策略：**内容寻址**——`storage_path` 由 `content_sha256` 决定，
    相同内容重复抓取只占一份存储；但每次抓取都留一条记录，
    以保留 `fetched_at` 证据链（"我们何时看到过这个内容"）。

    实际落盘由平台存储契约负责（`platform_compat.storage`，B2 参数化 root），
    本模型只记录相对路径与摘要，**不自建存储底座**。
    """

    source = models.ForeignKey(TenderSource, on_delete=models.CASCADE, related_name="snapshots")
    run = models.ForeignKey(TenderFetchRun, on_delete=models.SET_NULL, null=True, blank=True,
                            related_name="snapshots")
    url = models.URLField("来源地址", max_length=1000)
    fetched_at = models.DateTimeField("抓取时间")
    http_status = models.PositiveIntegerField("HTTP 状态", null=True, blank=True)
    content_type = models.CharField("内容类型", max_length=160, blank=True)
    storage_path = models.CharField("存储相对路径", max_length=500)
    content_sha256 = models.CharField("内容摘要", max_length=64, db_index=True)
    byte_size = models.PositiveIntegerField("字节数")
    source_metadata = models.JSONField("来源元数据", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fetched_at", "-id"]
        indexes = [
            models.Index(fields=["source", "-fetched_at"], name="tender_snap_src_time"),
        ]

    def __str__(self) -> str:
        return f"snapshot#{self.pk} {self.content_sha256[:12]}"


# ---------------------------------------------------------------------------
# 公告与版本
# ---------------------------------------------------------------------------


class TenderNotice(models.Model):
    """公告主体。标识为 (source, source_notice_id) —— source-local 去重基础。"""

    source = models.ForeignKey(TenderSource, on_delete=models.CASCADE, related_name="notices")
    source_notice_id = models.CharField("来源公告标识", max_length=200)
    canonical_key = models.CharField("稳定聚合键", max_length=255, db_index=True)
    title = models.CharField("标题", max_length=500)
    notice_type = models.CharField("公告类型", max_length=80, blank=True)
    original_url = models.URLField("原始链接", max_length=1000)
    publish_at = models.DateTimeField("发布时间", null=True, blank=True)
    publish_date = models.DateField(null=True, blank=True)
    publish_precision = models.CharField(max_length=10, default="unknown")
    first_seen_at = models.DateTimeField("首次发现")
    last_seen_at = models.DateTimeField("最近发现")
    current_version = models.PositiveIntegerField("当前版本号", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "source_notice_id"],
                                    name="tender_notice_src_nid_uniq"),
        ]
        indexes = [
            models.Index(fields=["canonical_key"], name="tender_notice_canon"),
        ]

    def __str__(self) -> str:
        return f"{self.source_id}:{self.source_notice_id}"


class TenderNoticeVersion(models.Model):
    """公告版本。内容或关键字段变化即新增版本，**永不覆盖旧版本**。"""

    notice = models.ForeignKey(TenderNotice, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField("版本号")
    content_hash = models.CharField("内容摘要", max_length=64)
    normalized = models.JSONField("归一字段", default=dict, blank=True)
    attachments = models.JSONField("附件", default=list, blank=True)
    snapshot = models.ForeignKey(TenderSnapshot, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="versions")
    change_summary = models.JSONField("相对上一版变化", default=list, blank=True)
    supersedes = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="superseded_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["notice", "content_hash"],
                                    name="tender_noticever_hash_uniq"),
            models.UniqueConstraint(fields=["notice", "version"],
                                    name="tender_noticever_ver_uniq"),
        ]

    def __str__(self) -> str:
        return f"notice#{self.notice_id} v{self.version}"


# ---------------------------------------------------------------------------
# 商机聚合与事件
# ---------------------------------------------------------------------------


class TenderOpportunity(models.Model):
    """商机：用户看到的对象，聚合同一项目的多份公告与变化。

    `opportunity_key` 为 **source-local** 聚合键（规格第八章：先实现 source-local dedupe）。
    跨来源相似项只记入 `possible_match_keys`，**不合并**（规格第八章：不得仅因标题相似强制合并）。
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "进行中"
        UPDATED = "UPDATED", "有更新"
        AWARDED = "AWARDED", "已中标"
        CLOSED = "CLOSED", "已结束"

    opportunity_key = models.CharField("聚合键", max_length=255, unique=True)
    source = models.ForeignKey(TenderSource, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="opportunities")
    primary_notice = models.ForeignKey(TenderNotice, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="opportunities")

    # 物化的归一字段，供列表筛选（规格第十一章）与前端展示
    project_name = models.CharField("项目名称", max_length=500, blank=True)
    project_code = models.CharField("项目编号", max_length=200, blank=True)
    purchaser = models.CharField("采购单位", max_length=300, blank=True)
    agency = models.CharField("代理机构", max_length=300, blank=True)
    region = models.CharField("地区", max_length=120, blank=True)
    notice_type = models.CharField("公告类型", max_length=80, blank=True)
    procurement_method = models.CharField("采购方式", max_length=80, blank=True)

    budget_amount_yuan = models.DecimalField("预算(元)", max_digits=20, decimal_places=2,
                                             null=True, blank=True)
    budget_cap_yuan = models.DecimalField("最高限价(元)", max_digits=20, decimal_places=2,
                                          null=True, blank=True)
    budget_raw = models.CharField("预算原文", max_length=200, blank=True)

    publish_at = models.DateTimeField("发布时间", null=True, blank=True)
    publish_date = models.DateField(null=True, blank=True)
    publish_precision = models.CharField(max_length=10, default="unknown")
    bid_deadline = models.DateTimeField("投标截止", null=True, blank=True)
    bid_open_at = models.DateTimeField("开标时间", null=True, blank=True)
    signup_time_text = models.CharField("报名时间(原文)", max_length=300, blank=True)

    contact_person = models.CharField("联系人", max_length=120, blank=True)
    contact_phone = models.CharField("联系方式", max_length=120, blank=True)
    attachment_count = models.PositiveIntegerField("附件数", default=0)
    attachment_urls = models.JSONField("附件地址", default=list, blank=True)

    unknown_fields = models.JSONField("未能确定的字段", default=list, blank=True)
    possible_match_keys = models.JSONField("可能的跨来源匹配", default=list, blank=True)

    status = models.CharField("状态", max_length=20, choices=Status, default=Status.ACTIVE)
    current_version = models.PositiveIntegerField("当前公告版本", default=0)
    first_seen_at = models.DateTimeField("首次发现", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publish_at", "-id"]
        indexes = [
            models.Index(fields=["-publish_at"], name="tender_opp_publish"),
            models.Index(fields=["region", "-publish_at"], name="tender_opp_region_time"),
            models.Index(fields=["bid_deadline"], name="tender_opp_deadline"),
            models.Index(fields=["status", "-updated_at"], name="tender_opp_status_time"),
        ]

    def __str__(self) -> str:
        return f"opp#{self.pk} {self.project_name[:40]}"


class TenderOpportunityEvent(models.Model):
    """商机事件时间线（规格第九章）。`dedupe_key` 保证重复执行不产生重复事件。

    注意：**不含 SOURCE_UNAVAILABLE**。来源故障是来源级事实，
    把它写成某个商机的事件等于把「站点挂了」伪装成「项目变化」。
    该场景由 `TenderSourceHealthEvent` 承载（jie 2026-09-24 拍板）。
    """

    class EventType(models.TextChoices):
        DISCOVERED = "DISCOVERED", "首次发现"
        NOTICE_UPDATED = "NOTICE_UPDATED", "公告更新"
        DEADLINE_CHANGED = "DEADLINE_CHANGED", "截止时间变化"
        DOCUMENT_ADDED = "DOCUMENT_ADDED", "新增附件"
        BUDGET_CHANGED = "BUDGET_CHANGED", "预算变化"
        REQUIREMENT_CHANGED = "REQUIREMENT_CHANGED", "要求变化"
        AWARD_PUBLISHED = "AWARD_PUBLISHED", "中标结果发布"

    opportunity = models.ForeignKey(TenderOpportunity, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField("事件类型", max_length=40, choices=EventType)
    occurred_at = models.DateTimeField("发生时间")
    notice_version = models.ForeignKey(TenderNoticeVersion, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name="events")
    payload = models.JSONField("事件载荷", default=dict, blank=True)
    dedupe_key = models.CharField("幂等键", max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["opportunity", "dedupe_key"],
                                    name="tender_event_dedupe_uniq"),
        ]
        indexes = [
            models.Index(fields=["opportunity", "occurred_at"], name="tender_event_opp_time"),
            models.Index(fields=["event_type", "-occurred_at"], name="tender_event_type_time"),
        ]

    def __str__(self) -> str:
        return f"event#{self.pk} {self.event_type}"


# ---------------------------------------------------------------------------
# 来源健康事件（来源级，不属于商机时间线）
# ---------------------------------------------------------------------------


class TenderSourceHealthEvent(models.Model):
    """来源健康事件（规格 `SOURCE_UNAVAILABLE` 的归位去处，jie 2026-09-24 拍板）。

    为什么单独成表而不是塞进 `TenderOpportunityEvent`：
    来源故障是**来源级**事实——「陕西省公共资源交易平台证书失效了」
    与「某某项目的截止时间变了」不是同一维度的事。
    混在一起会把站点故障伪装成项目变化，污染商机时间线。

    `dedupe_key` 与商机事件同样承担幂等职责：来源持续不可用不会刷出无限事件。
    """

    class EventType(models.TextChoices):
        PREFLIGHT_OK = "PREFLIGHT_OK", "预检通过"
        PREFLIGHT_BLOCKED = "PREFLIGHT_BLOCKED", "预检阻塞"
        FETCH_FAILED = "FETCH_FAILED", "抓取失败"
        RATE_LIMITED = "RATE_LIMITED", "被限流"
        DEGRADED = "DEGRADED", "降级"
        RECOVERED = "RECOVERED", "已恢复"

    source = models.ForeignKey(TenderSource, on_delete=models.CASCADE, related_name="health_events")
    event_type = models.CharField("事件类型", max_length=30, choices=EventType)
    occurred_at = models.DateTimeField("发生时间")
    reason_code = models.CharField("原因码", max_length=80, blank=True)
    detail = models.CharField("说明", max_length=500, blank=True)
    payload = models.JSONField("事件载荷", default=dict, blank=True)
    dedupe_key = models.CharField("幂等键", max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "dedupe_key"],
                                    name="tender_source_health_dedupe_uniq"),
        ]
        indexes = [
            models.Index(fields=["source", "-occurred_at"], name="tender_src_health_time"),
            models.Index(fields=["event_type", "-occurred_at"], name="tender_src_health_type"),
        ]

    def __str__(self) -> str:
        return f"src-health#{self.pk} {self.source_id} {self.event_type}"
