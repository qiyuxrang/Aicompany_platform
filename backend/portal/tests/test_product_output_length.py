from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from portal.product_models import DocumentTask
from portal.product_service import ProductError, append_revision, digest
from portal.product_worker import ExecutionError, _generate_family, _minimum_characters, _render

from .base import PortalTestCase


@override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_OUTPUT_PROFILE="formal", PRODUCT_ENFORCE_OUTPUT_LENGTH=True,
                   PRODUCT_TECHNICAL_TARGET_CHARACTERS=50000, PRODUCT_FEASIBILITY_TARGET_CHARACTERS=70000)
class ProductOutputLengthTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("formal-length-owner", "product")
        self.owner.refresh_from_db()
        facts = {"project": "隔离篇幅回归", "requirements": "仅本地模拟，不调用真实模型", "items": [], "conditions": []}
        self.task = DocumentTask.objects.create(
            owner=self.owner, title="隔离篇幅回归", idempotency_key="formal-length", payload_hash=digest(facts),
            state="RUNNING", stage="BLUEPRINT", pending_action="blueprint", fence=1,
            lease_until=timezone.now() + timedelta(seconds=180), checkpoint={"grant_version": self.owner.grant_version},
        )
        self.input = append_revision(self.task, "input", facts, actor=self.owner)
        self.blueprint = append_revision(self.task, "blueprint", {
            "purpose": "篇幅回归", "audience": "测试人员", "conditions": [],
            "chapters": [{"id": "chapter", "title": "项目设计", "scope": "测试", "source_ids": []}],
        }, input_hash=self.input.sha256, actor=self.owner)
        self.task.input_version = self.input.version
        self.task.blueprint_version = self.blueprint.version
        self.task.save()

    def generate(self, family="technical-solution"):
        return _generate_family(self.task.pk, 1, "unused-mocked-attempt", self.task, self.input, self.blueprint, family)

    def chapter(self, text):
        return {"chapter_id": "chapter", "title": "项目设计", "paragraphs": [text], "source_ids": []}

    def save_chapter(self, text, family="technical-solution"):
        return append_revision(self.task, "chapter", self.chapter(text), input_hash=self.input.sha256,
                               blueprint_hash=self.blueprint.sha256, family=family, actor=self.owner)

    def test_formal_minimum_is_not_ninety_percent(self):
        self.assertEqual(_minimum_characters(50000), 50000)
        self.assertEqual(_minimum_characters(70000), 70000)

    @patch("portal.product_worker._model")
    def test_formal_families_continue_in_bounded_parts_and_meet_exact_minimum(self, model):
        for family, minimum in (("technical-solution", 50000), ("feasibility", 70000)):
            model.reset_mock()
            def reply(*args):
                payload = args[-1]
                length = payload["length_target"]["this_response_characters"]
                self.assertLessEqual(length, 2400)
                prefix = f"{family}第{model.call_count}部分"
                return self.chapter(prefix + "文" * (length - len(prefix)))
            model.side_effect = reply
            chapters = self.generate(family)
            actual = sum(len(paragraph) for paragraph in chapters[0].payload["paragraphs"])
            self.assertGreaterEqual(actual, minimum)
            self.assertGreater(model.call_count, 1)
            self.task.refresh_from_db()
            result = self.task.checkpoint["output_generation"][family]
            self.assertEqual(result["minimum_characters"], minimum)
            self.assertEqual(result["status"], "target_met")

    @patch("portal.product_worker._model")
    def test_one_character_short_requires_continuation_without_discarding_saved_text(self, model):
        saved_payload = self.chapter("甲" * 19999)
        saved_payload["paragraphs"].extend(["乙" * 20000, "丙" * 10000])
        saved = append_revision(self.task, "chapter", saved_payload, input_hash=self.input.sha256,
                                blueprint_hash=self.blueprint.sha256, actor=self.owner)
        model.return_value = self.chapter("续")
        chapters = self.generate()
        self.assertEqual(chapters[0].payload["paragraphs"], [*saved.payload["paragraphs"], "续"])
        self.assertEqual(model.call_args.args[-1]["continuation"]["saved_characters"], 49999)
        self.assertEqual(model.call_count, 1)
        model.reset_mock()
        self.generate()
        model.assert_not_called()

    @patch("portal.product_worker._model")
    def test_duplicate_output_does_not_pad_word_count(self, model):
        self.save_chapter("已有段落")
        model.return_value = self.chapter(" 已有段落 \n")
        with self.assertRaises(ExecutionError) as error:
            self.generate()
        self.assertEqual(error.exception.code, "output_length_below_target")
        self.assertEqual(self.task.revisions.filter(kind="chapter").count(), 1)

    @patch("portal.product_documents.render_draft")
    def test_legacy_render_cannot_bypass_formal_minimum(self, render):
        chapter = self.save_chapter("已有短正文")
        with self.assertRaises(ExecutionError) as error:
            _render(self.task, 1, "unused-mocked-attempt", self.input, self.blueprint, [chapter])
        self.assertEqual(error.exception.code, "output_length_below_target")
        render.assert_not_called()

    @patch("portal.product_three_drafts.render_report_draft")
    def test_report_render_cannot_reuse_short_chapters(self, render):
        from portal.product_three_drafts import generate_report_drafts
        self.save_chapter("已有短正文")
        with self.assertRaises(ProductError) as error:
            generate_report_drafts(self.task, 1, "unused-mocked-attempt", self.input, self.blueprint)
        self.assertEqual(error.exception.code, "output_length_below_target")
        render.assert_not_called()

    @patch("portal.product_worker._model")
    def test_interrupted_generation_resumes_saved_parts(self, model):
        model.side_effect = [self.chapter("已保存正文"), ExecutionError("timeout")]
        with self.assertRaises(ExecutionError):
            self.generate()
        latest = self.task.revisions.filter(kind="chapter").latest("version")
        self.assertEqual(latest.payload["paragraphs"], ["已保存正文"])
        model.side_effect = ExecutionError("timeout")
        with self.assertRaises(ExecutionError):
            self.generate()
        self.assertTrue(model.call_args.args[-1]["continuation"]["append_only"])
        self.assertEqual(model.call_args.args[-1]["continuation"]["saved_characters"], 5)
