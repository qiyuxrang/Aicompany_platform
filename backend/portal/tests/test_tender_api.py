import json
from datetime import timedelta

from django.test import Client, override_settings
from django.utils import timezone

from portal.models import AuditEvent
from portal.tender_models import (TenderConsumerHeartbeat, TenderManualRefresh,
                                  TenderNotice, TenderNoticeVersion,
                                  TenderOpportunity, TenderSnapshot, TenderSource)

from .base import PortalTestCase


class TenderApiTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user("tender-product", "product")
        self.client = Client()
        self.login(self.client, self.user)
        self.now = timezone.now()

    def source(self, code="ccgp_national", enabled=True):
        names = {
            "ccgp_national": "中国政府采购网",
            "sx_jk_ecai": "陕西交控 e 采",
            "shxjkjt": "陕西交控集团官网",
            "csg_bidding": "南方电网供应链平台",
        }
        return TenderSource.objects.create(code=code, name=names[code], adapter_code=code,
                                           enabled=enabled)

    def opportunity(self, *, source=None, notice_id="t20260928_123456", url=None,
                    name="榆林市信息化项目", purchaser="采购单位甲"):
        source = source or self.source()
        url = url or f"https://www.ccgp.gov.cn/cggg/zygg/260928/{notice_id}.htm"
        notice = TenderNotice.objects.create(
            source=source, source_notice_id=notice_id, canonical_key=f"{source.code}:id:{notice_id}",
            title=name, notice_type="公开招标", original_url=url, publish_at=self.now,
            publish_date=self.now.date(), publish_precision="second", first_seen_at=self.now,
            last_seen_at=self.now, current_version=1,
        )
        snapshot = TenderSnapshot.objects.create(
            source=source, url=url, fetched_at=self.now, http_status=200,
            content_type="text/html", storage_path="sha256/test", content_sha256="a" * 64,
            byte_size=4,
        )
        TenderNoticeVersion.objects.create(notice=notice, version=1, content_hash="b" * 64,
                                           snapshot=snapshot, change_summary=[])
        return TenderOpportunity.objects.create(
            opportunity_key=notice.canonical_key, source=source, primary_notice=notice,
            project_name=name, project_code="YL-2026-01", purchaser=purchaser,
            region="榆林", notice_type="公开招标", procurement_method="公开招标",
            publish_at=self.now, publish_date=self.now.date(), publish_precision="second",
            status=TenderOpportunity.Status.ACTIVE, current_version=1, first_seen_at=self.now,
        )

    def test_empty_database_returns_zero_without_demo_data_and_admin_has_no_business_rights(self):
        response = self.client.get("/api/product/opportunities/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(response.json()["total"], 0)

        admin = self.create_admin()
        admin_client = Client()
        self.login(admin_client, admin, password="Admin!Pass9274-Qx")
        denied = admin_client.get("/api/product/opportunities/")
        self.assertEqual(denied.status_code, 404)
        self.assertNotIn("items", denied.json())

    def test_all_endpoints_require_current_product_permission(self):
        opportunity = self.opportunity()
        batch = TenderManualRefresh.objects.create(requested_by=self.user,
                                                   source_plan={"test": {"mode": "INITIAL_WINDOW"}})
        outsider = self.create_user("tender-outsider", "hr")
        client = Client()
        self.login(client, outsider)
        paths = [
            "/api/product/opportunities/",
            f"/api/product/opportunities/{opportunity.pk}/",
            f"/api/product/opportunities/{opportunity.pk}/versions/",
            "/api/product/options/",
            "/api/product/sources/",
            "/api/product/refresh/",
            f"/api/product/refresh/{batch.pk}/",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)
        self.assertEqual(client.post("/api/product/refresh/", data="{}",
                                     content_type="application/json").status_code, 404)

    def test_list_detail_versions_filters_and_only_expose_verified_official_detail_urls(self):
        valid = self.opportunity()
        invalid_source = self.source("csg_bidding")
        invalid = [
            self.opportunity(source=invalid_source, notice_id=notice_id, url=url,
                             name=f"非法链接{notice_id}", purchaser="采购单位乙")
            for notice_id, url in (
                ("12345", "https://www.bidding.csg.cn/files/12345.pdf"),
                ("12346", "https://www.bidding.csg.cn/zbgg/index.jhtml"),
                ("12347", "https://example.test/zbgg/12347.jhtml"),
                ("12348", "javascript:alert(1)"),
            )
        ]
        response = self.client.get("/api/product/opportunities/?q=信息化&region=榆林")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["original_url"], valid.primary_notice.original_url)
        self.assertEqual(response.json()["items"][0]["publish_date"], self.now.date().isoformat())

        for opportunity in invalid:
            detail = self.client.get(f"/api/product/opportunities/{opportunity.pk}/")
            self.assertEqual(detail.status_code, 200, detail.content)
            payload = detail.json()["opportunity"]
            self.assertIsNone(payload["original_url"])
            self.assertEqual(payload["notices"], [])
            self.assertNotIn(opportunity.primary_notice.original_url, json.dumps(payload))

        versions = self.client.get(f"/api/product/opportunities/{valid.pk}/versions/")
        self.assertEqual(versions.status_code, 200, versions.content)
        self.assertEqual(versions.json()["versions"][0]["snapshot_sha256"], "a" * 64)
        self.assertNotIn("source_url", versions.json()["versions"][0])

    def test_filter_options_do_not_truncate_real_database_values(self):
        source = self.source()
        rows = [TenderOpportunity(
            opportunity_key=f"ccgp_national:bulk:{index}", source=source,
            project_name=f"项目{index}", purchaser=f"采购单位{index:04d}", region="榆林",
        ) for index in range(5001)]
        TenderOpportunity.objects.bulk_create(rows)
        response = self.client.get("/api/product/options/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["purchaser"]), 5001)

    def test_sources_report_health_instead_of_masking_failure_as_empty_data(self):
        source = self.source()
        source.health_state = TenderSource.Health.ERROR
        source.health_detail = "连接失败"
        source.consecutive_failures = 2
        source.last_failure_at = self.now
        source.save()
        response = self.client.get("/api/product/sources/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["items"][0]["health_state"], "error")
        self.assertEqual(response.json()["items"][0]["health_detail"], "连接失败")

    def test_refresh_is_disabled_by_default_and_creates_no_batch(self):
        for code in ("ccgp_national", "sx_jk_ecai", "shxjkjt", "csg_bidding"):
            self.source(code)
        response = self.client.post("/api/product/refresh/", data="{}",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["code"], "refresh_unavailable")
        self.assertEqual(TenderManualRefresh.objects.count(), 0)

    @override_settings(PORTAL_TENDER_INGESTION_ENABLED=True,
                       PORTAL_TENDER_MANUAL_REFRESH_ENABLED=True)
    def test_refresh_requires_csrf_then_delegates_enqueue_and_reports_batch_state(self):
        for code in ("ccgp_national", "sx_jk_ecai", "shxjkjt", "csg_bidding"):
            self.source(code)
        TenderConsumerHeartbeat.objects.create(slot=1, updated_at=self.now)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies = self.client.cookies
        denied = csrf_client.post("/api/product/refresh/", data="{}", content_type="application/json")
        self.assertEqual(denied.status_code, 403)
        token = csrf_client.get("/api/csrf/").json()["csrfToken"]
        queued = csrf_client.post("/api/product/refresh/", data="{}", content_type="application/json",
                                  HTTP_X_CSRFTOKEN=token)
        self.assertEqual(queued.status_code, 202, queued.content)
        self.assertEqual(queued.json()["stored_state"], "QUEUED")
        batch_id = queued.json()["batch_id"]
        self.assertTrue(AuditEvent.objects.filter(action="tender_manual_refresh_queued",
                                                  target=batch_id).exists())

        state = self.client.get(f"/api/product/refresh/{batch_id}/")
        self.assertEqual(state.status_code, 200, state.content)
        self.assertEqual(state.json()["stored_state"], "QUEUED")
        overview = self.client.get("/api/product/refresh/")
        self.assertTrue(overview.json()["available"])
        self.assertEqual(overview.json()["batch"]["id"], batch_id)

    @override_settings(PORTAL_TENDER_INGESTION_ENABLED=True,
                       PORTAL_TENDER_MANUAL_REFRESH_ENABLED=True)
    def test_refresh_queues_only_explicitly_enabled_source(self):
        self.source('ccgp_national')
        TenderSource.objects.create(code='sx_jk_ecai', name='disabled',
                                    adapter_code='sx_jk_ecai', enabled=False)
        TenderConsumerHeartbeat.objects.create(slot=1, updated_at=self.now)
        response = self.client.post('/api/product/refresh/', data='{}',
                                    content_type='application/json')
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(TenderManualRefresh.objects.get().source_codes, ['ccgp_national'])

    def test_existing_active_refresh_wins_even_when_switches_are_disabled(self):
        batch = TenderManualRefresh.objects.create(requested_by=self.user,
                                                   source_plan={"test": {"mode": "INITIAL_WINDOW"}})
        response = self.client.post("/api/product/refresh/", data="{}",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["batch_id"], str(batch.pk))

    def test_expired_running_batch_keeps_stored_state_but_displays_interrupted(self):
        batch = TenderManualRefresh.objects.create(
            requested_by=self.user, state=TenderManualRefresh.State.RUNNING,
            lease_until=self.now - timedelta(seconds=1),
            source_plan={"test": {"mode": "INITIAL_WINDOW"}},
        )
        response = self.client.get(f"/api/product/refresh/{batch.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["stored_state"], "RUNNING")
        self.assertEqual(response.json()["display_state"], "INTERRUPTED")
        batch.refresh_from_db()
        self.assertEqual(batch.state, TenderManualRefresh.State.RUNNING)
