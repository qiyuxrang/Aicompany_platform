from unittest import TestCase

from portal import tender_requirement as rules


class RequirementExtractionTests(TestCase):
    def test_uses_current_requirement_block_extraction(self):
        items = rules.extract_requirements([
            "二、资格要求",
            "投标人须具备示例工程专业承包二级及以上资质；项目负责人须具备一级注册建造师资格。",
            "三、获取招标文件",
            "此处的资质文字不得进入资格要求",
        ])

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].section, "资格要求")
        self.assertEqual(items[0].category, rules.ENTERPRISE_QUALIFICATION)
        self.assertEqual((items[0].target, items[0].level), ("示例工程专业承包", "二级"))
        self.assertEqual(items[1].category, rules.PERSONNEL_CERTIFICATE)
        self.assertEqual((items[1].target, items[1].level), ("注册建造师资格", "一级"))

    def test_classifies_all_contract_categories_and_keeps_unknown(self):
        cases = {
            "具有示例企业资质": rules.ENTERPRISE_QUALIFICATION,
            "项目经理具有注册建造师": rules.PERSONNEL_CERTIFICATE,
            "提供类似项目业绩": rules.PROJECT_PERFORMANCE,
            "提供年度财务审计报告": rules.FINANCIAL_CONDITION,
            "未列入失信名单": rules.CREDIT_CONDITION,
            "提供制造商授权书": rules.MANUFACTURER_AUTHORIZATION,
            "接受联合体参与": rules.OTHER_ADMISSION_MATERIAL,
        }

        for sentence, category in cases.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(rules.classify_sentence(sentence), category)


class LevelRuleTests(TestCase):
    def test_only_same_level_system_is_comparable(self):
        self.assertGreater(rules.level_rank("一级"), rules.level_rank("二级"))
        self.assertTrue(rules.levels_comparable("一级", "三级"))
        self.assertFalse(rules.levels_comparable("一级", "甲级"))
        self.assertEqual(rules.level_rank("未知"), -1)


class SafeAssessmentTests(TestCase):
    def setUp(self):
        self.requirement = rules.Requirement(
            text="投标人须具备示例工程专业承包二级及以上资质",
            category=rules.ENTERPRISE_QUALIFICATION,
            target="示例工程专业承包",
            level="二级",
            section="资格要求",
        )

    def evidence(self, **changes):
        values = {
            "category": rules.ENTERPRISE_QUALIFICATION,
            "target": "示例工程专业承包",
            "level": "一级",
            "verified": True,
            "applicable": True,
            "current": True,
        }
        values.update(changes)
        return rules.Evidence(**values)

    def test_missing_unverified_or_stale_evidence_is_pending(self):
        variants = [
            (),
            (self.evidence(verified=False),),
            (self.evidence(applicable=False),),
            (self.evidence(current=False),),
        ]

        for evidence in variants:
            with self.subTest(evidence=evidence):
                self.assertEqual(rules.assess_requirement(self.requirement, evidence).verdict, rules.PENDING)

    def test_verified_same_system_levels_are_deterministic(self):
        self.assertEqual(
            rules.assess_requirement(self.requirement, [self.evidence(level="一级")]).verdict,
            rules.SATISFIED,
        )
        self.assertEqual(
            rules.assess_requirement(self.requirement, [self.evidence(level="三级")]).verdict,
            rules.UNSATISFIED,
        )

    def test_cross_system_and_unsupported_rules_are_pending(self):
        cross_system = rules.assess_requirement(self.requirement, [self.evidence(level="甲级")])
        mixed_verification = rules.assess_requirement(self.requirement, [
            self.evidence(level="三级"), self.evidence(level="一级", verified=False),
        ])
        unsupported = rules.Requirement(
            text="提供近三年类似项目业绩",
            category=rules.PROJECT_PERFORMANCE,
            section="资格要求",
        )

        self.assertEqual(cross_system.verdict, rules.PENDING)
        self.assertEqual(mixed_verification.verdict, rules.PENDING)
        self.assertEqual(
            rules.assess_requirement(unsupported, [self.evidence()]).verdict,
            rules.PENDING,
        )
