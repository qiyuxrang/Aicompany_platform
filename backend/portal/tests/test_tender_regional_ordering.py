from datetime import timedelta

from django.test import Client
from django.utils import timezone

from portal.tender_models import TenderOpportunity
from .base import PortalTestCase


class TenderRegionalOrderingTests(PortalTestCase):
    def setUp(self):
        self.client = Client()
        self.login(self.client, self.create_user('regional-board-reader', 'product'))
        self.now = timezone.now()

    def opportunity(self, key, region, days=0, **values):
        return TenderOpportunity.objects.create(
            opportunity_key=key, project_name=values.pop('project_name', key), region=region,
            industry_code='coal', classification_status='matched', notice_category='procurement',
            publish_at=self.now - timedelta(days=days), first_seen_at=self.now, **values)

    def test_default_order_prioritizes_verified_regions_without_removing_national_results(self):
        national = self.opportunity('national', '云南省')
        unknown = self.opportunity('unknown', '', project_name='榆林企业全国建设项目')
        same_town_name = self.opportunity('unrelated-town', '吉林省榆林镇')
        neighbor = self.opportunity('neighbor', '内蒙古自治区', 2)
        neighbor_older = self.opportunity('neighbor-older', '山西省', 3)
        shaanxi = self.opportunity('shaanxi', '陕西省西安市', 4)
        yulin_older = self.opportunity('yulin-older', '陕西 / 榆林市', 6)
        yulin = self.opportunity('yulin', '榆林市榆阳区', 5)
        payload = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(payload['total'], 8)
        self.assertEqual([row['id'] for row in payload['items']], [
            yulin.pk, yulin_older.pk, shaanxi.pk, neighbor.pk, neighbor_older.pk,
            same_town_name.pk, unknown.pk, national.pk])
        self.assertEqual(payload['stats']['total'], 8)

    def test_region_filter_and_explicit_time_sort_remain_available(self):
        yulin = self.opportunity('local', '陕西省榆林市', 1)
        national = self.opportunity('national', '北京市')
        filtered = self.client.get('/api/product/opportunities/?region=北京').json()
        self.assertEqual([row['id'] for row in filtered['items']], [national.pk])
        chronological = self.client.get('/api/product/opportunities/?ordering=-publish_at').json()
        self.assertEqual([row['id'] for row in chronological['items']], [national.pk, yulin.pk])

    def test_date_only_newer_notice_sorts_before_older_timed_notice(self):
        older = self.opportunity('older', '陕西省榆林市', 2,
                                 publish_date=(self.now - timedelta(days=2)).date())
        newer = self.opportunity('newer', '陕西省榆林市', publish_date=self.now.date())
        TenderOpportunity.objects.filter(pk=newer.pk).update(publish_at=None, publish_precision='date')
        rows = self.client.get('/api/product/opportunities/').json()['items']
        self.assertEqual([row['id'] for row in rows], [newer.pk, older.pk])

    def test_out_of_range_page_returns_first_page_and_preserves_filtered_stats(self):
        yulin = self.opportunity('local', '榆林市')
        self.opportunity('national', '北京市')
        payload = self.client.get('/api/product/opportunities/?region=榆林&industry=coal&page=99&page_size=1').json()
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['stats']['total'], 1)
        self.assertEqual([row['id'] for row in payload['items']], [yulin.pk])
        self.assertFalse(payload['has_more'])
        empty = self.client.get('/api/product/opportunities/?industry=water&page=99').json()
        self.assertEqual(empty['page'], 1)
        self.assertEqual(empty['total'], 0)
        self.assertEqual(empty['items'], [])

    def test_valid_second_page_keeps_global_region_order(self):
        self.opportunity('national', '云南省')
        shaanxi = self.opportunity('shaanxi', '陕西省西安市', 1)
        self.opportunity('yulin', '陕西榆林', 2)
        payload = self.client.get('/api/product/opportunities/?page=2&page_size=1').json()
        self.assertEqual(payload['page'], 2)
        self.assertEqual(payload['total'], 3)
        self.assertEqual(payload['items'][0]['id'], shaanxi.pk)
        self.assertTrue(payload['has_more'])

    def test_county_aliases_need_explicit_shaanxi_and_other_yulin_names_are_not_promoted(self):
        unverified = self.opportunity('unknown-county', '神木', 0)
        other_yulin = self.opportunity('sichuan-yulin', '四川省甘孜州榆林新区', 0)
        fugu = self.opportunity('fugu', '陕西省府谷县', 3)
        shenmu = self.opportunity('shenmu', '陕西 / 神木市', 2)
        yuyang = self.opportunity('yuyang', '陕西省榆阳区', 1)
        rows = self.client.get('/api/product/opportunities/').json()['items']
        self.assertEqual([row['id'] for row in rows], [yuyang.pk, shenmu.pk, fugu.pk,
                                                      other_yulin.pk, unverified.pk])

    def test_region_options_reject_legacy_prose_and_prioritize_local_administrative_names(self):
        valid = ['北京市', '陕西省榆林市', '陕西省榆林市神木市', '陕西 / 榆林市',
                 '榆林市', '陕西省西安市', '内蒙古自治区鄂尔多斯市', '云南省红河哈尼族彝族自治州']
        invalid = ['落实政府采购政策需满足的资格要求：详见采购文件', '本项目所在地区',
                   '陕西省政府采购支持中小企业政策适用地区', '陕西省西安市某路100号',
                   '采购单位：某地区', '未知', '陕西榆林公司', '陕西省榆林市。']
        for index, region in enumerate(valid + invalid):
            self.opportunity(f'option-{index}', region)
        regions = self.client.get('/api/product/options/').json()['region']
        values = [item['value'] for item in regions]
        self.assertEqual(values[:4], ['榆林市', '陕西 / 榆林市', '陕西省榆林市', '陕西省榆林市神木市'])
        self.assertLess(values.index('陕西'), values.index('北京'))
        self.assertLess(values.index('陕西省西安市'), values.index('北京'))
        for value in valid:
            self.assertIn(value if value != '北京市' else '北京', values)
        for value in invalid:
            self.assertNotIn(value, values)
