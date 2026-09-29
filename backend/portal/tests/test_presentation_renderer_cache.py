"""Renderer upgrades invalidate only PPT reuse, retaining historical artifacts."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from portal.product_service import digest
from portal.product_three_drafts import generate_presentation_artifact


class PresentationRendererCacheTests(SimpleTestCase):
    def test_renderer_upgrade_misses_old_cache_while_same_renderer_reuses(self):
        task = SimpleNamespace(pk="synthetic", title="Synthetic report")
        pair = {"sha256": "pair"}
        old_hash = digest({"kind": "presentation", "pair": "pair", "title": task.title, "renderer": "old"})
        saved = SimpleNamespace(pk="old-artifact")
        class RenderReached(Exception):
            pass
        with patch("portal.product_worker._renew"), patch("portal.product_worker._analysis_progress"), \
                patch("portal.product_three_drafts.pair_snapshot", return_value=pair), \
                patch("portal.product_three_drafts.verified_artifact") as verify, \
                patch("portal.product_three_drafts.DocumentArtifact.objects.filter") as lookup, \
                patch("portal.product_presentation.presentation_renderer_hash", return_value="old") as renderer, \
                patch("portal.product_presentation.render_presentation_draft", side_effect=RenderReached) as render:
            lookup.side_effect = lambda **query: SimpleNamespace(first=lambda: saved if query["generation_hash"] == old_hash else None)
            self.assertIs(generate_presentation_artifact(task, 1, "attempt", None, None), saved)
            verify.assert_called_once_with(saved)
            render.assert_not_called()
            renderer.return_value = "new"
            with self.assertRaises(RenderReached):
                generate_presentation_artifact(task, 1, "attempt", None, None)
            render.assert_called_once_with(task, pair)
