import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from portal.product_rules import ProductRulesError, ProductRulesUnavailable, rules_hash, stage_rules


RULES_HASH = "7ed4d2eabea00402e63bfd26e85f5551e79e449e0ad4f859b150816d1edfec55"
SOURCE_HASHES = {
    "af32f0d8281c8ed0f99c1e8671bd0d1b1f463203425ba9aedcf3549a81c4ca22",
    "2056900ab63c9fc0cef54280f4e5bdcf1edd2bff7992ec571ec5a3b9f4192c3a",
    "0096f3849f052043fa93a958e508c910b02039e7e2cb600250a6d139859b9b0c",
    "3af4d63a5db7ff9a5fd076e6482e866688a7fb0f70577791308e5951f4887446",
}


class ProductRulesTests(SimpleTestCase):
    def test_stage_routing_uses_explicit_stages(self):
        texts = {stage: stage_rules(stage) for stage in ("blueprint", "write", "review")}

        for stage, text in texts.items():
            self.assertIn(f"适用阶段：{stage}", text)
        self.assertEqual(len(set(texts.values())), 3)
        self.assertIn("已批准蓝图", texts["write"])
        self.assertIn("审查必须绑定", texts["review"])
        for invalid in ("product_writing", None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ProductRulesError, "^product_rules_stage_invalid$"):
                stage_rules(invalid)

    def test_rules_and_source_hashes_are_traceable(self):
        combined = "\n".join(stage_rules(stage) for stage in ("blueprint", "write", "review"))

        self.assertEqual(rules_hash(), RULES_HASH)
        self.assertEqual({value for value in SOURCE_HASHES if value in combined}, SOURCE_HASHES)
        self.assertIn("50,000字", combined)
        self.assertIn("70,000字", combined)
        self.assertIn("至少20张图片", combined)
        self.assertIn("WorkBuddy", combined)

    def test_missing_or_changed_asset_fails_closed(self):
        original = Path(__file__).resolve().parents[1] / "product_assets" / "p1_rules.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.json"
            changed = root / "changed.json"
            changed.write_bytes(original.read_bytes() + b"\n")

            for path in (missing, changed):
                with self.subTest(path=path.name), patch("portal.product_rules._RULES_PATH", path):
                    with self.assertRaisesRegex(ProductRulesUnavailable, "^product_rules_unavailable$"):
                        stage_rules("blueprint")
                    with self.assertRaisesRegex(ProductRulesUnavailable, "^product_rules_unavailable$"):
                        rules_hash()
