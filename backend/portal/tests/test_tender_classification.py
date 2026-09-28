import hashlib
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import Client, SimpleTestCase, override_settings

from portal.tender_classification import INDUSTRIES, CLASSIFICATION_VERSION, classify_notice
from portal.tender_models import TenderNoticeVersion, TenderOpportunity, TenderSource
from portal.tender_service import ingest_fetch_result, reclassify_opportunity
from portal.tender_sources.base import FetchResult
from .base import PortalTestCase


class ClassificationTests(SimpleTestCase):
    def test_computing_clusters_require_real_scope_evidence(self):
        for name in ('GPU计算集群', '高性能计算集群', '人工智能算力平台'):
            with self.subTest(name=name):
                body = f'<p>采购需求</p><table><tr><th>标的名称</th></tr><tr><td>{name}</td></tr></table>'
                result = classify_notice(title=f'{name}采购公告', raw=body)
                self.assertEqual(result['classification_status'], 'matched')
                self.assertIn('算力基础设施', result['digital_tags'])
                heading_only = classify_notice(title=f'{name}采购公告', raw=f'<h1>{name}采购公告</h1>')
                self.assertEqual(heading_only['classification_status'], 'review')
        for body in ('供应商登录算力平台下载投标文件。', '采购需求：不采购计算集群。'):
            result = classify_notice(title='采购公告', raw=f'<p>{body}</p>')
            self.assertNotEqual(result['classification_status'], 'matched')

    def test_enterprise_erp_and_agent_maintenance_are_supported_by_construction_scope(self):
        for content in ('构建AI智能体驱动的皮带设备健康诊断引擎',
                        '建设ERP系统，提供系统集成与运维服务', '建设设备预测维护系统'):
            result = classify_notice(title='煤矿建设招标公告', raw=f'<p>建设内容：{content}。</p>')
            self.assertEqual(result['classification_status'], 'matched')
            self.assertEqual(result['industry_code'], 'coal')
        result = classify_notice(title='材料采购公告', raw='<p>供应商登录ERP系统上传投标文件。</p>')
        self.assertEqual(result['classification_status'], 'review')

    def test_qinyuan_qualification_registry_is_not_a_purchased_information_system(self):
        cases = (
            ('防火喷涂及安全设施防腐询比采购公告',
             '供应商基本信息在“陕西省建筑市场监管与诚信一体化管理系统”可查询（提供截图）'),
            ('编制年度消防设施设备评估报告询比采购公告',
             '（2）资质要求：供应商须应已在社会消防技术服务信息系统中登记注册并审核通过'),
        )
        for title, qualification in cases:
            with self.subTest(title=title):
                result = classify_notice(title=title, raw=(
                    '<p>采购项目概况：详见第五章采购需求。</p><p>三、供应商资格要求</p>'
                    f'<p>{qualification}</p><p>须具备信息系统建设服务经验。</p>'))
                self.assertEqual(result['classification_status'], 'review')
                self.assertEqual(result['classification_evidence']['snippets'], [])

    def test_digital_industry_background_does_not_turn_mechanical_or_research_services_into_digital_scope(self):
        for title, background in (
            ('截齿涂层服务询比采购公告',
             '采购项目概况：截齿磨损已成为智能化采煤生产及巷道掘进的瓶颈之一，急需开发新技术提高截齿寿命和可靠性。'),
            ('ETF网格策略研究服务采购公示',
             '采购内容：本项目拟采购ETF网格策略研究服务，用于优化两融客户的智能化服务体验。'),
        ):
            with self.subTest(title=title):
                result = classify_notice(title=title, raw=f'<p>{background}</p>')
                self.assertEqual(result['classification_status'], 'review')
                self.assertEqual(result['classification_evidence']['snippets'], [])

    def test_online_monitoring_maintenance_matches_actual_equipment_scope_not_supplier_registration(self):
        result = classify_notice(title='在线监测设备运维服务询比采购公告', raw=(
            '<p>采购项目概况</p><p>乙方负责对甲方1套水质在线监测设备、1套废气在线监测设备提供全周期运维保障服务。</p>'
            '<p>包含数采仪、工控及传输系统全部运维。</p><p>三、供应商资格要求</p>'
            '<p>供应商须提供监测技术服务机构管理系统备案证明（须提供证明截图）。</p>'))
        self.assertEqual(result['classification_status'], 'matched')
        self.assertEqual(result['digital_tags'], ['物联网与监测'])
        self.assertTrue(any('在线监测设备' in text for text in result['classification_evidence']['snippets']))
        self.assertFalse(any('备案' in text or '管理系统' in text for text in result['classification_evidence']['snippets']))

    def test_construction_scope_after_qualification_section_can_be_recognized(self):
        result = classify_notice(title='矿业公司宿舍楼设计招标公告', raw=(
            '<p>三、投标人资格要求</p><p>须具有信息系统建设服务经验。</p>'
            '<p>招标范围：完成施工图设计，包括智能化系统设计。</p>'))
        self.assertEqual(result['classification_status'], 'matched')
        self.assertEqual(len(result['classification_evidence']['snippets']), 1)

    def test_all_nine_industries_use_scope_evidence(self):
        titles = ('煤矿信息化建设', '水利信息化建设', '电力信息化建设', '医院信息化建设',
                  '交通信息化建设', '石油信息化建设', '市政信息化建设', '学校信息化建设', '工业信息化建设')
        for code, title in zip(INDUSTRIES, titles):
            with self.subTest(code=code):
                result = classify_notice(title=title, raw='<p>采购需求：开发信息系统，部署数据平台。</p>')
                self.assertEqual(result['industry_code'], code)
                self.assertEqual(result['classification_status'], 'matched')
                self.assertEqual(result['digital_tags'], ['信息化建设', '数据平台'])
                self.assertTrue(result['classification_evidence']['snippets'])

    def test_plain_goods_and_procurement_template_are_excluded(self):
        template = ('<nav>数字化建设服务</nav><p>供应商登录信息化平台下载投标文件。</p>'
                    '<p>网上报名，使用数字证书及电子签章。</p><footer>信息化技术支持</footer>')
        for title in ('煤炭采购', '医院药品采购', '学校办公家具采购', '办公楼土建工程'):
            with self.subTest(title=title):
                result = classify_notice(title=title, raw=template)
                self.assertEqual(result['classification_status'], 'excluded')
                self.assertEqual(result['digital_tags'], [])

    def test_mixed_construction_with_explicit_digital_scope_is_retained(self):
        result = classify_notice(title='煤矿办公楼土建工程', raw=(
            '<p>建设内容：办公楼土建施工，安装安防视频监控系统、综合布线及智能化控制。</p>'))
        self.assertEqual(result['classification_status'], 'matched')
        self.assertEqual(result['industry_code'], 'coal')
        self.assertIn('物联网与监测', result['digital_tags'])
        self.assertIn('网络与安全', result['digital_tags'])

    def test_title_or_purchaser_alone_and_negated_scope_do_not_match(self):
        for title, body in (
            ('煤矿智能化改造项目', '<p>具体要求详见附件。</p>'),
            ('园区改造项目', '<p>采购人：信息化服务公司</p>'),
            ('医院家具采购', '<p>采购需求：不含智能化和信息系统建设。</p>'),
            ('数字科技公司办公家具采购', '<p>采购需求：办公桌五十张。</p>'),
            ('家具采购', '<p>采购需求：办公桌10套。</p><p>采购人：某市信息化建设有限公司</p>'),
            ('装修采购', '<p>本项目采用电子化交易，供应商须通过政府采购信息化系统提交报价。</p>'),
        ):
            with self.subTest(title=title):
                result = classify_notice(title=title, raw=body, purchaser='信息化管理局')
                self.assertNotEqual(result['classification_status'], 'matched')
                self.assertEqual(result['digital_tags'], [])
        self.assertEqual(classify_notice(title='矿山项目', raw='详见附件')['classification_status'], 'review')

    def test_negation_of_installation_does_not_negate_network_equipment(self):
        result = classify_notice(title='网络设备采购公告', raw='<p>采购需求：网络设备采购，不包含安装服务。</p>')
        self.assertEqual(result['classification_status'], 'matched')

    def test_real_registration_and_file_acquisition_templates_are_not_digital_scope(self):
        # Minimal public text excerpts from actual 2026-09-28 snapshots; not invented notices.
        # Original SHA256: bdfc8f1170cef521692dedc532f14429bd4356b5faccc1563609ce5529058999.
        registration = ('供应商应通过福建省政府采购网上公开信息系统的注册账号（免费注册）并获取竞争性磋商文件'
                        '(登陆福建省政府采购网上公开信息系统进行文件获取)')
        acquisition = ('印刷服务的潜在供应商应在福建省政府采购网(zfcg.czt.fujian.gov.cn)免费申请账号'
                       '在福建省政府采购网上公开信息系统按项目获取采购文件')
        # Original SHA256: 96dfe2d239057d546323f0e49da334d80dbb858d43aaa16671960d2a18d3f72f.
        renovation = ('滨海新区基地实验楼4-5层前处理间装修改造 采购项目的潜在供应商应在'
                      '“中化商务数字化平台”（https://hyszpt.com/#/zcnotice）获取采购文件')
        for title, body in (('印刷服务竞争性磋商公告', registration + '。' + acquisition),
                            ('实验楼前处理间装修改造竞争性磋商公告', renovation)):
            with self.subTest(title=title):
                result = classify_notice(title=title, raw=f'<p>{body}</p>')
                self.assertNotEqual(result['classification_status'], 'matched')
                self.assertEqual(result['classification_evidence']['snippets'], [])
                self.assertEqual(result['digital_tags'], [])
        result = classify_notice(title='医院信息化建设招标公告', raw=(
            f'<p>{registration}</p><p>采购需求：供应商应建设医院信息系统。</p>'))
        self.assertEqual(result['classification_status'], 'matched')
        self.assertEqual(result['classification_evidence']['snippets'], ['采购需求：供应商应建设医院信息系统'])

    def test_real_procurement_policy_citations_do_not_supply_technical_scope(self):
        # Public snapshot SHA256: 4a4a6d903587be98c566aef6e5aa10a66a101457fea0d6515d2ac85da5800e9e.
        citations = (
            '财政部 、 工业和信息化部《政府采购促进中小企业发展管理办法》（财库〔 2020〕46号）',
            '国家互联网信息办公室 、 工业和信息化部 、 公安部 、 财政部 、 '
            '国家认证认可监督管理委员会《关于调整网络安全专用产品安全管理有关事项的公告》（ 2023年第1号）',
        )
        result = classify_notice(title='实时固化监测采集系统竞争性谈判公告', raw=(
            '<p>采购需求：详见采购需求附件</p>' + ''.join(f'<p>{text}</p>' for text in citations)))
        self.assertEqual(result['classification_status'], 'review')
        self.assertEqual(result['classification_evidence']['snippets'], [])
        self.assertEqual(result['digital_tags'], [])

    def test_real_mixed_smart_construction_is_not_chemical_industry(self):
        # Public snapshot SHA256: d17d30be90487321a32bc3f85ca7e2182a88edfca78ee4f9bdf194d42c7a36ad.
        scope = '建筑及装饰装修工程、给排水工程、电气工程、建筑智能化工程、拆除工程等'
        result = classify_notice(title='乡村工匠名师工作室建设项目成交公告', raw=(
            f'<table><tr><th>标的名称</th><th>施工范围</th></tr><tr><td>{scope}</td></tr></table>'))
        self.assertEqual(result['classification_status'], 'matched')
        self.assertNotEqual(result['industry_code'], 'petrochemical')
        result = classify_notice(title='化工工程信息化建设采购公告', raw='<p>建设内容：部署信息系统。</p>')
        self.assertEqual(result['industry_code'], 'petrochemical')

    def test_contractor_obligations_and_monitoring_are_scope_not_bidding_process(self):
        for content in ('供应商应建设医院信息系统', '采购安防监控设备并安装',
                        '部署井下人员定位平台', '安装远程监测设备', '部署弱电系统'):
            with self.subTest(content=content):
                result = classify_notice(title='建设项目招标公告', raw=f'<p>建设内容：{content}。</p>')
                self.assertEqual(result['classification_status'], 'matched')

    def test_intents_and_future_plans_are_not_public_procurement_notices(self):
        for suffix in ('采购意向', '拟建项目', '投资计划'):
            result = classify_notice(title='医院信息化建设' + suffix, raw='<p>建设内容：部署信息系统。</p>')
            self.assertEqual(result['classification_status'], 'excluded')
            self.assertEqual(result['notice_category'], 'unknown')
        self.assertEqual(classify_notice(title='中标管理信息系统招标公告')['notice_category'], 'procurement')

    def test_notice_groups_keep_changes_and_results_distinct(self):
        for title, category in (('信息化建设采购公告', 'procurement'),
                                ('信息化建设更正公告', 'change'), ('信息化建设中标结果公告', 'result'),
                                ('信息化建设废标公告', 'result')):
            self.assertEqual(classify_notice(title=title)['notice_category'], category)


class ClassificationPersistenceTests(PortalTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='tender-classification-')
        self.addCleanup(self.directory.cleanup)
        config = override_settings(TENDER_STORAGE_ROOT=Path(self.directory.name))
        config.enable()
        self.addCleanup(config.disable)
        self.source = TenderSource.objects.create(code='ccgp_national', name='中国政府采购网',
                                                   adapter_code='ccgp_national')

    def ingest(self, body, *, notice_id='t20260928_12345', title='煤矿建设采购公告'):
        raw = (f'<html><title>{title}</title><p>项目编号：CLASSIFY-1</p>'
               f'<p>发布时间：2026-09-28 10:00</p>{body}</html>').encode()
        return ingest_fetch_result(FetchResult(
            source_code=self.source.code, source_notice_id=notice_id,
            original_url=f'https://www.ccgp.gov.cn/cggg/zygg/gkzb/202609/{notice_id}.htm',
            fetched_at='2026-09-28T10:30:00+08:00', http_status=200, content_type='text/html; charset=utf-8',
            raw_bytes=raw, sha256=hashlib.sha256(raw).hexdigest()), source=self.source)

    def test_ingest_reclassifies_changed_scope_and_old_replay_cannot_restore_old_classification(self):
        old = '<p>采购需求：建设智能矿山监测系统。</p>'
        self.ingest(old)
        self.assertEqual(TenderOpportunity.objects.get().classification_status, 'matched')
        self.ingest('<p>采购需求：不含信息系统和智能化建设，具体事项待核实。</p>')
        opportunity = TenderOpportunity.objects.get()
        self.assertEqual(opportunity.classification_status, 'review')
        latest = opportunity.classification_notice_version_id
        self.ingest(old)
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.classification_status, 'review')
        self.assertEqual(opportunity.classification_notice_version_id, latest)

    def test_backfill_uses_latest_aggregate_notice_and_is_repeatable_without_refreshing_timestamps(self):
        self.ingest('<p>采购需求：建设智能矿山监测系统。</p>')
        self.ingest('<p>采购需求：不包含任何信息化建设。</p>',
                    notice_id='t20260928_54321', title='煤矿建设更正公告')
        opportunity = TenderOpportunity.objects.get()
        latest = TenderNoticeVersion.objects.latest('id')
        self.assertNotEqual(opportunity.primary_notice_id, latest.notice_id)
        TenderOpportunity.objects.filter(pk=opportunity.pk).update(
            classification_status='matched', classification_version='', classification_notice_version=None)
        before = opportunity.updated_at
        preview = StringIO()
        call_command('reclassify_tenders', dry_run=True, stdout=preview)
        self.assertIn('changed=1', preview.getvalue())
        self.assertEqual(TenderOpportunity.objects.get().classification_status, 'matched')
        call_command('reclassify_tenders', stdout=StringIO())
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.classification_status, 'review')
        self.assertEqual(opportunity.notice_category, 'change')
        self.assertEqual(opportunity.classification_notice_version_id, latest.pk)
        self.assertEqual(opportunity.updated_at, before)
        second = StringIO()
        call_command('reclassify_tenders', stdout=second)
        self.assertIn('changed=0', second.getvalue())
        self.assertEqual(opportunity.classification_version, CLASSIFICATION_VERSION)

    def test_missing_or_tampered_snapshot_stays_review(self):
        self.ingest('<p>采购需求：建设智能矿山监测系统。</p>')
        snapshot = TenderNoticeVersion.objects.get().snapshot
        Path(self.directory.name, snapshot.storage_path).write_bytes(b'tampered')
        call_command('reclassify_tenders', stdout=StringIO())
        opportunity = TenderOpportunity.objects.get()
        self.assertEqual(opportunity.classification_status, 'review')
        self.assertEqual(opportunity.digital_tags, [])

    def test_new_classification_immediately_changes_paginated_api_stats(self):
        client = Client()
        self.login(client, self.create_user('classification-reader', 'product'))
        with patch('portal.tender_api.timezone.now', return_value=datetime(
                2026, 9, 28, 12, tzinfo=ZoneInfo('Asia/Shanghai'))):
            self.ingest('<p>采购需求：建设智能矿山监测系统。</p>')
            first = client.get('/api/product/opportunities/?industry=coal&page_size=1').json()
            self.assertEqual(first['stats'], {'total': 1, 'today_new': 1, 'closing_soon': 0, 'latest_batch_new': 0})
            self.assertEqual(len(first['items']), 1)
            self.ingest('<p>采购需求：不包含智能化建设，具体内容另行明确。</p>')
            second = client.get('/api/product/opportunities/?industry=coal&page_size=1').json()
            self.assertEqual(second['stats'], {'total': 0, 'today_new': 0, 'closing_soon': 0, 'latest_batch_new': 0})
            self.assertEqual(second['items'], [])
            self.assertFalse(second['has_more'])

    def test_backfill_does_not_overwrite_a_concurrent_ingestion(self):
        self.ingest('<p>具体要求待核实。</p>')
        opportunity = TenderOpportunity.objects.get()

        def concurrent_update(**kwargs):
            values = classify_notice(**kwargs)
            TenderOpportunity.objects.filter(pk=opportunity.pk).update(
                classification_status='matched', updated_at=opportunity.updated_at + timedelta(seconds=1))
            return values

        with patch('portal.tender_service.classify_notice', side_effect=concurrent_update):
            reclassify_opportunity(opportunity)
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.classification_status, 'matched')


class BoardApiTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user('board-user', 'product')
        self.client = Client()
        self.login(self.client, self.user)
        self.now = datetime(2026, 9, 29, 0, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.source = TenderSource.objects.create(code='ccgp_national', name='中国政府采购网',
                                                   adapter_code='ccgp_national', last_success_at=self.now)

    def item(self, key, **values):
        defaults = dict(project_name='数字化建设项目', source=self.source, industry_code='coal',
                        digital_tags=['数字化建设'], classification_status='matched', notice_category='procurement',
                        region='陕西', first_seen_at=self.now, publish_at=self.now,
                        bid_deadline=self.now + timedelta(days=3))
        defaults.update(values)
        return TenderOpportunity.objects.create(opportunity_key=key, **defaults)

    def test_default_scope_multiselect_pagination_and_beijing_stats_agree(self):
        self.item('coal-today')
        self.item('coal-yesterday', first_seen_at=self.now - timedelta(hours=1),
                  bid_deadline=self.now - timedelta(minutes=1))
        self.item('water', industry_code='water', bid_deadline=self.now + timedelta(days=8))
        self.item('power', industry_code='power')
        self.item('other-region', region='山西')
        self.item('change', notice_category='change')
        self.item('result', notice_category='result')
        self.item('review', classification_status='review')
        self.item('excluded', classification_status='excluded')
        query = '/api/product/opportunities/?industry=coal,water&region=陕西&page_size=1'
        with patch('portal.tender_api.timezone.now', return_value=self.now):
            first = self.client.get(query).json()
            second = self.client.get(query + '&page=2').json()
            default = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(first['stats'], {'total': 3, 'today_new': 2, 'closing_soon': 1, 'latest_batch_new': 0})
        self.assertEqual(first['stats'], second['stats'])
        self.assertEqual(first['total'], 3)
        self.assertEqual(len(first['items']), 1)
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertTrue(first['has_more'])
        self.assertEqual(first['last_updated_at'], self.now.astimezone(ZoneInfo('UTC')).isoformat())
        self.assertEqual(default['total'], 5)
        self.assertEqual(first['items'][0]['classification_status'], 'matched')
        self.assertIn(first['items'][0]['industry_label'], ('煤炭', '水利水电'))

    def test_options_categories_review_access_and_invalid_filter(self):
        self.item('procurement')
        self.item('change', notice_category='change')
        self.item('result', notice_category='result')
        self.item('unknown', classification_status='review')
        self.assertEqual(self.client.get('/api/product/opportunities/?notice_category=all').json()['total'], 3)
        self.assertEqual(self.client.get('/api/product/opportunities/?notice_category=result').json()['total'], 1)
        review = self.client.get('/api/product/opportunities/?classification_status=review').json()
        self.assertEqual(review['total'], 1)
        for query in ('industry=coal,invalid', 'notice_category=invalid', 'classification_status=invalid'):
            self.assertEqual(self.client.get('/api/product/opportunities/?' + query).status_code, 400)
        options = self.client.get('/api/product/options/').json()
        self.assertEqual([item['value'] for item in options['industries']], list(INDUSTRIES))
        self.assertIn({'value': '陕西', 'label': '陕西省'}, options['region'])
        self.assertIn({'value': '北京', 'label': '北京市'}, options['region'])
        self.assertEqual({item['value'] for item in options['notice_categories']}, {'all', 'procurement', 'change', 'result'})

    def test_no_success_timestamp_is_not_replaced_with_page_load_time(self):
        self.source.last_success_at = None
        self.source.save()
        self.assertIsNone(self.client.get('/api/product/opportunities/').json()['last_updated_at'])

    def test_region_options_deduplicate_province_names_and_keep_specific_cities(self):
        self.item('beijing', region='北京市')
        self.item('shaanxi', region='陕西省')
        self.item('yulin', region='榆林市')
        self.item('compound', region='陕西省榆林市')
        regions = self.client.get('/api/product/options/').json()['region']
        labels = [item['label'] for item in regions]
        self.assertEqual(labels.count('北京市'), 1)
        self.assertEqual(labels.count('陕西省'), 1)
        self.assertIn({'value': '榆林市', 'label': '榆林市'}, regions)
        self.assertIn({'value': '陕西省榆林市', 'label': '陕西省榆林市'}, regions)
