import hashlib
import shutil
import tempfile
from datetime import date
from pathlib import Path

from django.test import TestCase, override_settings

from portal.tender_models import (
    TenderNotice,
    TenderNoticeVersion,
    TenderOpportunity,
    TenderOpportunityEvent,
    TenderSnapshot,
    TenderSource,
)
from portal.tender_service import TenderIngestRejected, ingest_fetch_result
from portal.tender_normalize import extract_verified_publication
from portal.tender_sources.base import FetchResult


class TenderIngestTests(TestCase):
    def setUp(self):
        self.storage_root = Path(tempfile.mkdtemp(prefix="portal-tender-ingest-"))
        self.addCleanup(shutil.rmtree, self.storage_root, True)
        settings_override = override_settings(TENDER_STORAGE_ROOT=self.storage_root)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def source(self, code):
        return TenderSource.objects.create(code=code, name=code, adapter_code=code)

    def result(self, code, notice_id, url, html, *, metadata=None, fetched_at="2026-09-28T10:00:00+08:00"):
        raw = html.encode()
        return FetchResult(
            source_code=code,
            source_notice_id=notice_id,
            original_url=url,
            fetched_at=fetched_at,
            http_status=200,
            content_type="text/html; charset=utf-8",
            raw_bytes=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            source_metadata=metadata or {},
        )

    def test_detail_only_dates_are_verified_for_three_non_national_sources(self):
        cases = (
            ("sx_jk_ecai", "a" * 32,
             f"https://www.sxjkjcpt.com/portal/detail?chnlcode=tender&docid={'a' * 32}",
             "<html><title>道路监控项目招标公告</title><p>项目编号：YL-1</p>"
             "<p>发布日期：<script>document.write(( '2026-09-26 16:13:53.0' ).substring(0, 19));"
             "</script></p></html>", "second"),
            ("shxjkjt", "123", "https://www.shxjkjt.com/notice/bidding-detail?id=123",
             "<html><title>道路监控项目招标公告</title>"
             '<div class="u-m-r-15">时间：2026-09-27</div>'
             '<div class="about-content"><p>项目编号：YL-1</p></div></html>', "date"),
            ("csg_bidding", "456", "https://www.bidding.csg.cn/zbgg/456.jhtml",
             "<html><title>道路监控项目招标公告</title>"
             '<div class="s-date">发布时间： 2026-09-28 17:07:38 来源：本站原创</div>'
             '<div class="Content"><p>招标编号：YL-1</p></div></html>', "second"),
        )
        for code, notice_id, url, html, precision in cases:
            with self.subTest(code=code):
                direct = extract_verified_publication(
                    html,
                    source_code=code,
                    original_url=url,
                )
                self.assertTrue(direct.verified)
                self.assertEqual(direct.precision, precision)
                outcome = ingest_fetch_result(
                    self.result(code, notice_id, url, html),
                    source=self.source(code),
                )
                notice = TenderNotice.objects.get(source__code=code)
                self.assertIn(outcome.publish_date, {"2026-09-26", "2026-09-27", "2026-09-28"})
                self.assertEqual(outcome.publish_precision, precision)
                self.assertEqual(outcome.publish_provenance, "detail:publish_at")
                self.assertEqual(notice.publish_precision, precision)
                self.assertEqual(notice.publish_at is None, precision == "date")

    def test_initial_window_rejects_unknown_and_outside_dates_before_writes(self):
        source = self.source("sx_jk_ecai")
        notice_id = "b" * 32
        url = f"https://www.sxjkjcpt.com/portal/detail?chnlcode=tender&docid={notice_id}"
        cases = (
            ("<html><title>无日期公告</title></html>", "publish_date_unverified"),
            ("<html><title>窗口外公告</title><p>发布时间：2026-09-29</p></html>",
             "publish_date_outside_window"),
        )
        for html, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(TenderIngestRejected) as caught:
                    ingest_fetch_result(self.result(source.code, notice_id, url, html), source=source)
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(TenderSnapshot.objects.count(), 0)
        self.assertEqual(TenderNotice.objects.count(), 0)
        self.assertEqual(list(self.storage_root.rglob("*.bin")), [])

    def test_unverified_official_url_is_rejected_before_writes(self):
        source = self.source("shxjkjt")
        fetched = self.result(
            source.code, "123", "https://evil.example/notice/bidding-detail?id=123",
            "<html><title>项目</title><p>发布时间：2026-09-27</p></html>",
        )
        with self.assertRaises(TenderIngestRejected) as caught:
            ingest_fetch_result(fetched, source=source)
        self.assertEqual(caught.exception.code, "unverified_official_url")
        self.assertFalse(TenderSnapshot.objects.exists())

    def test_repeat_fetch_keeps_snapshot_evidence_without_duplicate_version_or_event(self):
        source = self.source("ccgp_national")
        notice_id = "t20260926_10000001"
        url = f"https://www.ccgp.gov.cn/cggg/zygg/gkzb/202609/{notice_id}.htm"
        html = ("<html><title>榆林市监控平台公开招标公告</title><p>项目编号：YL-2026-1</p>"
                "<p>发布时间：2026-09-26 10:30</p><p>预算金额：100万元</p></html>")
        first = ingest_fetch_result(self.result(source.code, notice_id, url, html), source=source)
        second = ingest_fetch_result(self.result(source.code, notice_id, url, html,
                                                  fetched_at="2026-09-28T11:00:00+08:00"), source=source)

        self.assertTrue(first.notice_created)
        self.assertFalse(second.notice_created)
        self.assertFalse(second.version_created)
        self.assertEqual(TenderSnapshot.objects.count(), 2)
        self.assertEqual(TenderNoticeVersion.objects.count(), 1)
        self.assertEqual(TenderOpportunity.objects.count(), 1)
        self.assertEqual(list(TenderOpportunityEvent.objects.values_list("event_type", flat=True)),
                         ["DISCOVERED"])
        self.assertEqual(len(set(TenderSnapshot.objects.values_list("storage_path", flat=True))), 1)

    def test_new_content_appends_version_and_replaying_old_content_is_stale_safe(self):
        source = self.source("csg_bidding")
        notice_id = "789"
        url = "https://www.bidding.csg.cn/zbgg/789.jhtml"
        first_html = ("<html><title>数据平台公开招标公告</title><p>项目编号：CSG-1</p>"
                      "<p>发布时间：2026-09-27 09:00</p><p>预算金额：100万元</p>"
                      "<p>投标截止时间：2026-10-10 09:00</p></html>")
        second_html = first_html.replace("100万元", "120万元").replace("2026-10-10", "2026-10-12")
        first_result = self.result(source.code, notice_id, url, first_html)
        ingest_fetch_result(first_result, source=source)
        changed = ingest_fetch_result(self.result(source.code, notice_id, url, second_html), source=source)
        event_count = TenderOpportunityEvent.objects.count()
        replay = ingest_fetch_result(first_result, source=source)

        self.assertTrue(changed.version_created)
        self.assertIn("BUDGET_CHANGED", changed.events)
        self.assertIn("DEADLINE_CHANGED", changed.events)
        self.assertFalse(replay.version_created)
        self.assertEqual(TenderNoticeVersion.objects.count(), 2)
        self.assertEqual(TenderOpportunityEvent.objects.count(), event_count)
        self.assertEqual(TenderOpportunity.objects.get().budget_amount_yuan, 1_200_000)

    def test_precise_publish_time_keeps_real_precision(self):
        source = self.source("shxjkjt")
        outcome = ingest_fetch_result(
            self.result(source.code, "321", "https://www.shxjkjt.com/notice/bidding-detail?id=321",
                        "<html><title>项目招标公告</title><p>发布时间：2026-09-28 08:15</p></html>"),
            source=source,
        )
        opportunity = TenderOpportunity.objects.get()
        self.assertEqual(outcome.publish_precision, "minute")
        self.assertEqual(opportunity.publish_precision, "minute")
        self.assertEqual(opportunity.publish_date, date(2026, 9, 28))
        self.assertIsNotNone(opportunity.publish_at)
