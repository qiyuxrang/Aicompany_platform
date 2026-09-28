"""Owner/scope/lease regression tests; provider and gateway are always mocked.

Call the DRF views directly to test their contract independently of URL integration.
Real ORM authorization and conditional UPDATEs run against the Django test database.
"""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import product_knowledge_api as api
from portal import product_knowledge_service as service
from portal.models import Module, User
from portal.product_knowledge_models import ProductKnowledgeConversation as Conversation
from portal.product_service import ProductError

from .base import PortalTestCase
from .test_product_knowledge_service import CONFIG, Response, wire


class ProductKnowledgeApiTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("knowledge-owner", "product")
        self.other = self.create_user("knowledge-other", "product")
        self.denied = self.create_user("knowledge-denied")
        # Role assignment advances grant_version through an ORM signal.
        self.owner.refresh_from_db()
        self.other.refresh_from_db()
        self.grants = {str(user.pk): {"dataset-1": ["document-1"]} for user in (self.owner, self.other)}
        settings = override_settings(PRODUCT_KNOWLEDGE_AUTHORIZATIONS=self.grants)
        settings.enable()
        self.addCleanup(settings.disable)
        self.factory = APIRequestFactory()
        self.ready = self.start_patch("portal.product_knowledge_service.ready", return_value=CONFIG)
        self.outbound = self.start_patch("portal.product_knowledge_service._open",
                                        side_effect=lambda *args: Response(wire()))
        self.gateway = self.start_patch("portal.model_gateway.generate_for_use", return_value={
            "content": json.dumps({"answer": "设备支持此功能。", "source_ids": ["S1"]})})

    def start_patch(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def request(self, view, method="get", data=None, user=None, conversation=None, anonymous=False):
        request = getattr(self.factory, method)("/api/product/knowledge/", data=data, format="json")
        if not anonymous:
            force_authenticate(request, user=user or self.owner)
        kwargs = {"conversation_id": conversation.pk} if conversation else {}
        response = view(request, **kwargs)
        response.render()
        return response

    def create(self):
        response = self.request(api.conversations, "post", {})
        self.assertEqual(response.status_code, 201, response.content)
        return Conversation.objects.get(pk=response.data["id"])

    def send(self, conversation, question="设备支持什么？", version=0, request_id=None, user=None):
        return self.request(api.conversation, "post", {
            "question": question, "version": version, "request_id": request_id or str(uuid.uuid4())},
            user=user, conversation=conversation)

    def assert_unchanged(self, conversation, version=0, turns=None):
        conversation.refresh_from_db()
        self.assertEqual(conversation.version, version)
        self.assertEqual(conversation.turns, [] if turns is None else turns)
        self.assertIsNone(conversation.pending_id)
        self.assertIsNone(conversation.pending_until)

    def test_permissions_and_non_owner_never_call_external_services(self):
        item = self.create()
        for view, conversation in ((api.status, None), (api.datasets, None),
                                   (api.conversations, None), (api.conversation, item)):
            with self.subTest(view=view.__name__):
                denied = self.request(view, user=self.denied, conversation=conversation)
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.data["code"], "forbidden")
                self.assertIn(self.request(view, anonymous=True, conversation=conversation).status_code, (401, 403))
        for method in ("get", "post"):
            denied = self.request(api.conversation, method, {}, user=self.other, conversation=item)
            self.assertEqual(denied.status_code, 404)
        self.assertEqual(self.request(api.conversations, user=self.other).data["conversations"], [])
        self.outbound.assert_not_called()
        self.gateway.assert_not_called()

    def test_dataset_listing_is_owner_scoped_and_uncached(self):
        self.outbound.side_effect = lambda *args: Response(json.dumps({
            "code": 0, "data": [{"id": "dataset-1", "name": "产品资料", "document_count": 48}]}).encode())
        response = self.request(api.datasets)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"datasets": [{"id": "dataset-1", "name": "产品资料", "document_count": 1}]})
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.request(api.datasets, user=self.denied).status_code, 403)
        self.assertEqual(self.outbound.call_count, 1)

    def test_streaming_question_emits_incremental_answer_and_commits_only_on_completion(self):
        item = self.create()
        raw = json.dumps({"answer": "项目甲与网络安全有关。", "source_ids": ["S1"]}, ensure_ascii=False)
        with patch("portal.model_gateway.stream_for_use", return_value=iter([
                {"delta": raw[:20]}, {"delta": raw[20:]},
                {"done": True, "prompt_tokens": 10, "completion_tokens": 20}])):
            request = self.factory.post("/api/product/knowledge/", {
                "question": "项目有哪些？", "version": 0, "request_id": str(uuid.uuid4()), "stream": True}, format="json")
            force_authenticate(request, user=self.owner)
            response = api.conversation(request, conversation_id=item.pk)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            events = [json.loads(line[6:]) for chunk in response.streaming_content
                      for line in chunk.decode().splitlines() if line.startswith("data: ")]
        self.assertTrue(any("delta" in event for event in events))
        self.assertIn("项目甲", "".join(event.get("delta", "") for event in events))
        self.assertIn("done", events[-1])
        self.assertEqual(len(events[-1]["done"]["turns"]), 1)
        item.refresh_from_db()
        self.assertEqual(item.version, 1)
        self.assertIsNone(item.pending_id)

    def test_closed_stream_releases_lease_without_saving_partial_answer(self):
        item = self.create()
        closed = []
        def events():
            try:
                yield {"delta": '{"answer":"草稿'}
                yield {"done": True, "prompt_tokens": 1, "completion_tokens": 1}
            finally:
                closed.append(True)
        with patch("portal.model_gateway.stream_for_use", return_value=events()):
            request = self.factory.post("/api/product/knowledge/", {
                "question": "问题", "version": 0, "request_id": str(uuid.uuid4()), "stream": True}, format="json")
            force_authenticate(request, user=self.owner)
            response = api.conversation(request, conversation_id=item.pk)
            self.assertIn(b'data: {"delta":', next(iter(response.streaming_content)))
            response.close()
        self.assertEqual(closed, [True])
        self.assert_unchanged(item)

    def test_stream_rechecks_scope_and_route_before_releasing_next_chunk(self):
        for change in ("scope", "route"):
            with self.subTest(change=change):
                self.grants[str(self.owner.pk)] = {"dataset-1": ["document-1"]}
                self.ready.return_value = CONFIG
                item = self.create()
                closed = []

                def provider_events():
                    try:
                        yield {"delta": '{"answer":"允许的开头'}
                        if change == "scope":
                            self.grants[str(self.owner.pk)] = {"dataset-1": ["replacement-document"]}
                        else:
                            self.ready.return_value = (CONFIG[0], CONFIG[1], "replacement_route")
                        yield {"delta": 'SECRET_AFTER_REVOCATION","source_ids":["S1"]}'}
                        yield {"done": True}
                    finally:
                        closed.append(True)

                with patch("portal.model_gateway.stream_for_use", return_value=provider_events()):
                    request = self.factory.post("/api/product/knowledge/", {
                        "question": "问题", "version": 0, "request_id": str(uuid.uuid4()), "stream": True}, format="json")
                    force_authenticate(request, user=self.owner)
                    response = api.conversation(request, conversation_id=item.pk)
                    events = [json.loads(line[6:]) for chunk in response.streaming_content
                              for line in chunk.decode().splitlines() if line.startswith("data: ")]
                self.assertEqual(events[0], {"delta": "允许的开头"})
                self.assertEqual(events[-1]["error"]["code"], "scope_revoked" if change == "scope" else "unconfigured")
                self.assertNotIn("SECRET_AFTER_REVOCATION", json.dumps(events))
                self.assertEqual(closed, [True])
                self.assert_unchanged(item)

    def test_streaming_question_without_done_does_not_persist_answer(self):
        item = self.create()
        with patch("portal.model_gateway.stream_for_use", return_value=iter([{"delta": '{"answer":"未完成"'}])):
            request = self.factory.post("/api/product/knowledge/", {
                "question": "问题", "version": 0, "request_id": str(uuid.uuid4()), "stream": True}, format="json")
            force_authenticate(request, user=self.owner)
            response = api.conversation(request, conversation_id=item.pk)
            events = [json.loads(line[6:]) for chunk in response.streaming_content
                      for line in chunk.decode().splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["error"]["code"], "invalid_response")
        self.assert_unchanged(item)

    def test_product_disabled_or_user_stale_denied(self):
        for field in ("session_version", "grant_version"):
            with self.subTest(field=field):
                original = getattr(self.owner, field)
                User.objects.filter(pk=self.owner.pk).update(**{field: original + 1})
                self.assertEqual(self.request(api.status).status_code, 403)
                User.objects.filter(pk=self.owner.pk).update(**{field: original})
        Module.objects.filter(code="product").update(enabled=False)
        self.assertEqual(self.request(api.status).status_code, 403)
        self.outbound.assert_not_called()

    def test_unconfigured_status_and_creation_fail_without_external_calls(self):
        for code in ("disabled", "unconfigured"):
            self.ready.side_effect = ProductError(code, "not ready", 503)
            status = self.request(api.status)
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.data["available"], False)
            self.assertEqual(status.data["code"], code)
            denied = self.request(api.conversations, "post", {})
            self.assertEqual(denied.status_code, 503)
            self.assertEqual(denied.data["code"], code)
        self.assertEqual(Conversation.objects.count(), 0)
        self.outbound.assert_not_called()
        self.gateway.assert_not_called()

    def test_multiturn_persists_context_current_sources_and_no_store(self):
        item = self.create()
        first = self.send(item)
        self.assertEqual(first.status_code, 200, first.content)
        second = self.send(item, "它的端口呢？", version=1)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.data["version"], 2)
        self.assertEqual(len(second.data["turns"]), 2)
        self.assertEqual(self.outbound.call_count, 2)
        self.assertEqual(self.gateway.call_count, 2)
        self.assertEqual(second.data["title"], "设备支持什么？")
        self.assertEqual(second.data["turns"][1]["answer"], "设备支持此功能。 [S1]")
        query = json.loads(self.outbound.call_args.args[0].data)["question"]
        self.assertEqual(query, "设备支持什么？\n它的端口呢？")
        messages = self.gateway.call_args.args[2]
        self.assertEqual(messages[1], {"role": "user", "content": "设备支持什么？"})
        self.assertEqual(messages[2]["role"], "assistant")
        detail = self.request(api.conversation, conversation=item)
        self.assertEqual(detail.data, second.data)
        self.assertEqual(detail["Cache-Control"], "private, no-store")
        self.assertEqual(self.request(api.conversations).data["conversations"][0]["id"], str(item.pk))

    def test_empty_retrieval_saves_empty_turn_without_model(self):
        self.outbound.side_effect = lambda *args: Response(wire([]))
        item = self.create()
        response = self.send(item)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["turns"][0]["outcome"], "empty")
        self.assertEqual(response.data["turns"][0]["sources"], [])
        self.assertEqual(response.data["version"], 1)
        self.gateway.assert_not_called()

    def test_invalid_retrieval_never_reaches_model_and_releases_lease(self):
        self.outbound.side_effect = lambda *args: Response(b'{"code":0,"data":{"chunks":[{}]}}')
        item = self.create()
        response = self.send(item)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "invalid_response")
        self.gateway.assert_not_called()
        self.assert_unchanged(item)

    def test_invalid_model_response_is_not_persisted(self):
        item = self.create()
        self.gateway.return_value = {"content": json.dumps({"answer": "fake", "source_ids": ["unknown"]})}
        response = self.send(item)
        self.assertEqual(response.status_code, 502)
        self.assert_unchanged(item)

    def test_revocation_during_retrieval_prevents_model_and_commit(self):
        item = self.create()
        def retrieve(*args):
            self.grants[str(self.owner.pk)] = {"dataset-1": ["replacement-document"]}
            return Response(wire())
        self.outbound.side_effect = retrieve
        response = self.send(item)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "scope_revoked")
        self.gateway.assert_not_called()
        self.assert_unchanged(item)

    def test_revocation_during_model_prevents_disclosure_and_commit(self):
        item = self.create()
        def answer(*args):
            self.grants.clear()
            return {"content": json.dumps({"answer": "SECRET_ANSWER", "source_ids": ["S1"]})}
        self.gateway.side_effect = answer
        response = self.send(item)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"SECRET_ANSWER", response.content)
        self.assert_unchanged(item)

    def test_scope_change_hides_old_title_turns_and_idempotent_replay(self):
        item = self.create()
        request_id = str(uuid.uuid4())
        self.assertEqual(self.send(item, "SECRET_QUESTION", request_id=request_id).status_code, 200)
        self.outbound.reset_mock()
        self.gateway.reset_mock()
        self.grants[str(self.owner.pk)] = {"dataset-1": ["replacement-document"]}
        self.assertEqual(self.request(api.conversations).data["conversations"], [])
        for response in (self.request(api.conversation, conversation=item),
                         self.send(item, "SECRET_QUESTION", request_id=request_id)):
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(b"SECRET_QUESTION", response.content)
            self.assertEqual(response.data["code"], "scope_revoked")
        self.outbound.assert_not_called()
        self.gateway.assert_not_called()

    def test_route_change_during_retrieval_prevents_gateway(self):
        item = self.create()
        def retrieve(*args):
            self.ready.return_value = (CONFIG[0], CONFIG[1], "replacement_route")
            return Response(wire())
        self.outbound.side_effect = retrieve
        response = self.send(item)
        self.assertEqual(response.status_code, 503)
        self.gateway.assert_not_called()
        self.assert_unchanged(item)

    def test_idempotency_replay_does_not_repeat_calls_or_increment_version(self):
        item = self.create()
        request_id = str(uuid.uuid4())
        first = self.send(item, request_id=request_id)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.outbound.call_count, 1)
        self.assertEqual(self.gateway.call_count, 1)
        self.outbound.reset_mock()
        self.gateway.reset_mock()
        replay = self.send(item, request_id=request_id)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        mismatch = self.send(item, "different question", request_id=request_id)
        self.assertEqual(mismatch.status_code, 409)
        stale = self.send(item)
        self.assertEqual(stale.status_code, 409)
        self.outbound.assert_not_called()
        self.gateway.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.version, 1)

    def test_live_lease_collision_is_rejected_before_retrieval(self):
        item = self.create()
        lease = uuid.uuid4()
        Conversation.objects.filter(pk=item.pk).update(pending_id=lease, pending_until=timezone.now() + timedelta(seconds=60))
        response = self.send(item)
        self.assertEqual(response.status_code, 409)
        self.outbound.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.pending_id, lease)

    def test_expired_lease_can_be_reclaimed(self):
        item = self.create()
        Conversation.objects.filter(pk=item.pk).update(pending_id=uuid.uuid4(), pending_until=timezone.now() - timedelta(seconds=1))
        response = self.send(item)
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.version, 1)
        self.assertIsNone(item.pending_id)

    def test_expiry_during_model_cannot_commit_even_without_successor(self):
        item = self.create()
        def answer(*args):
            Conversation.objects.filter(pk=item.pk).update(pending_until=timezone.now() - timedelta(seconds=1))
            return {"content": json.dumps({"answer": "late", "source_ids": ["S1"]})}
        self.gateway.side_effect = answer
        self.assertEqual(self.send(item).status_code, 409)
        self.assert_unchanged(item)

    def test_losing_worker_cannot_overwrite_or_release_successor_lease(self):
        item = self.create()
        successor = uuid.uuid4()
        def answer(*args):
            Conversation.objects.filter(pk=item.pk).update(pending_id=successor, version=1,
                pending_until=timezone.now() + timedelta(seconds=60), turns=[{"answer": "successor"}])
            return {"content": json.dumps({"answer": "late", "source_ids": ["S1"]})}
        self.gateway.side_effect = answer
        response = self.send(item)
        self.assertEqual(response.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.pending_id, successor)
        self.assertEqual(item.version, 1)
        self.assertEqual(item.turns, [{"answer": "successor"}])

    def test_nested_request_during_retrieval_loses_claim(self):
        item = self.create()
        nested = []
        def retrieve(*args):
            nested.append(self.send(item))
            return Response(wire())
        self.outbound.side_effect = retrieve
        response = self.send(item)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(nested[0].status_code, 409)
        self.assertEqual(self.outbound.call_count, 1)
        self.assertEqual(self.gateway.call_count, 1)

    def test_input_validation_precedes_external_calls(self):
        item = self.create()
        good = {"question": "q", "version": 0, "request_id": str(uuid.uuid4())}
        bad = [[], {}, {**good, "extra": 1}, {**good, "question": " "},
               {**good, "question": "x" * 2001}, {**good, "question": "bad\x00"},
               {**good, "version": True}, {**good, "version": -1}, {**good, "request_id": "not-uuid"}]
        for payload in bad:
            with self.subTest(payload=str(payload)[:80]):
                response = self.request(api.conversation, "post", payload, conversation=item)
                self.assertEqual(response.status_code, 400)
        self.outbound.assert_not_called()
        self.gateway.assert_not_called()
        self.assert_unchanged(item)

    def test_authority_or_authorization_revision_change_hides_old_history(self):
        item = self.create()
        self.assertEqual(self.send(item).status_code, 200)
        for change in ({"PRODUCT_KNOWLEDGE_URL": "https://replacement.test/api/v1/retrieval"},
                       {"PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION": "replacement-revision"}):
            with self.subTest(change=change), override_settings(**change):
                self.assertEqual(self.request(api.conversations).data["conversations"], [])
                self.assertEqual(self.request(api.conversation, conversation=item).status_code, 403)

    def test_unpaired_surrogate_question_rejected_before_outbound(self):
        item = self.create()
        request = self.factory.post("/api/product/knowledge/",
            data=json.dumps({"question": "bad" + chr(0xD800), "version": 0, "request_id": str(uuid.uuid4())}),
            content_type="application/json")
        force_authenticate(request, user=self.owner)
        response = api.conversation(request, conversation_id=item.pk)
        self.assertEqual(response.status_code, 400)
        self.outbound.assert_not_called()
        self.gateway.assert_not_called()

    def test_retry_first_request_after_second_turn_returns_current_history_with_two_calls(self):
        item = self.create()
        first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(self.send(item, request_id=first_id).status_code, 200)
        second = self.send(item, "follow up", version=1, request_id=second_id)
        self.assertEqual(second.status_code, 200)
        replay = self.send(item, request_id=first_id)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, second.data)
        self.assertEqual(replay.data["version"], 2)
        self.assertEqual(self.outbound.call_count, 2)
        self.assertEqual(self.gateway.call_count, 2)

    def test_disabled_during_model_does_not_save_answer(self):
        item = self.create()
        def answer(*args):
            self.ready.side_effect = ProductError("disabled", "disabled", 503)
            return {"content": json.dumps({"answer": "SECRET", "source_ids": ["S1"]})}
        self.gateway.side_effect = answer
        response = self.send(item)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"SECRET", response.content)
        self.assert_unchanged(item)

    def test_real_session_urls_owner_boundary_and_csrf(self):
        client, other = Client(), Client()
        self.login(client, self.owner)
        self.login(other, self.other)
        base = "/api/product/knowledge/"
        status = client.get(base + "status/")
        self.assertEqual(status.status_code, 200, status.content)
        self.assertTrue(status.json()["available"])
        created = client.post(base + "conversations/", data="{}", content_type="application/json")
        self.assertEqual(created.status_code, 201, created.content)
        detail_url = base + "conversations/" + created.json()["id"] + "/"
        detail = client.get(detail_url)
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json(), created.json())
        self.assertEqual(other.get(detail_url).status_code, 404)
        self.assertEqual(other.get(base + "conversations/").json()["conversations"], [])
        protected = Client(enforce_csrf_checks=True)
        protected.cookies = client.cookies
        denied = protected.post(base + "conversations/", data="{}", content_type="application/json")
        self.assertEqual(denied.status_code, 403, denied.content)
        denied_turn = protected.post(detail_url, data=json.dumps({"question": "q", "version": 0,
            "request_id": str(uuid.uuid4())}), content_type="application/json")
        self.assertEqual(denied_turn.status_code, 403)
        token = self.csrf_token(protected)
        accepted = protected.post(detail_url, data=json.dumps({"question": "q", "version": 0,
            "request_id": str(uuid.uuid4())}), content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(self.outbound.call_count, 1)
        self.assertEqual(self.gateway.call_count, 1)
