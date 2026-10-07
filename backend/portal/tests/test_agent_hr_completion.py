import tempfile
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from portal.agent_models import (AgentBusinessReference, AgentConversation, AgentEvent,
                                 AgentMessage, AgentRequirement, AgentRun, AgentWorkTask)
from portal.agent_management import _hr_batch, _resume_artifact
from portal.agent_runtime import AgentDenied, RuntimeGuard, initialize_root
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_resume_storage import save_file
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.hr_screening_worker import finish_one
from portal.models import Role, User
from .base import PortalTestCase


class AgentHrCompletionTests(TransactionTestCase):
    create_user = PortalTestCase.create_user

    def setUp(self):
        call_command('seed_portal', stdout=StringIO())
        self.owner = self.create_user('agent-hr-completion', 'hr', must_change_password=False)
        self.owner.department_code = 'hr'
        self.owner.save(update_fields=['department_code'])
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=self.storage.name))
        self.scope = self.create_scope('primary')
        self.request = RecruitmentRequest.objects.create(
            created_by=self.owner, updated_by=self.owner, position_name='工程师',
            skill_requirements=['SQL'],
        )
        self.jd = JDVersion.objects.create(
            request=self.request, version=1, input_version=1, state='confirmed',
            body='需要 SQL', requirements={'skill_requirements': ['SQL']}, created_by=self.owner,
        )
        self.request.current_jd = self.request.official_jd = self.jd
        self.request.save(update_fields=['current_jd', 'official_jd'])

    def create_scope(self, key):
        conversation = AgentConversation.objects.create(owner=self.owner, department_code='hr')
        work = AgentWorkTask.objects.create(
            owner=self.owner, department_code='hr', conversation=conversation,
            goal='筛选简历', state='running', current_requirement_version=1,
        )
        message = AgentMessage.objects.create(
            conversation=conversation, work=work, role='user', content='按 SQL 要求筛选',
        )
        requirement = AgentRequirement.objects.create(
            work=work, version=1, user_message=message, content='按 SQL 要求筛选',
        )
        root = AgentRun.objects.create(
            id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, work=work, requirement=requirement, state='running',
            deadline_at=timezone.now() + timedelta(hours=1),
            policy={'max_actions': 60, 'max_model_calls': 30, 'max_tool_calls': 20,
                    'max_launches': 10, 'max_concurrent': 5, 'max_active_ms': 3600000},
        )
        return work, root, RuntimeGuard(initialize_root(root.pk, self.owner.pk))

    def create_batch(self, key, scope=None, *, count=1, bound=True):
        work, root, guard = scope or self.scope
        batch = ResumeScreeningBatch.objects.create(
            jd_version=self.jd, created_by=self.owner, idempotency_key=f'completion-{key}',
            input_version=1, requirements={'skill_requirements': ['SQL']}, status='running',
        )
        if bound:
            values = {
                'agent_root_id': str(root.pk), 'agent_work_id': str(work.pk),
                'agent_requirement_version': work.current_requirement_version,
                'agent_grant_version': guard.binding.grant_version,
                'agent_session_version': guard.binding.session_version,
                'agent_root_fence': guard.binding.fence,
            }
            for field, value in values.items():
                setattr(batch, field, value)
            batch.save(update_fields=[*values, 'updated_at'])
        artifacts = []
        for index in range(count):
            content = f'简历 {key}-{index}：技能 SQL'.encode()
            artifact = ResumeArtifact.objects.create(
                batch=batch, uploaded_by=self.owner,
                **save_file('resume.txt', content),
            )
            artifact.processing_status = 'running'
            artifact.fence = 1
            artifact.lease_until = timezone.now() + timedelta(minutes=5)
            artifact.save(update_fields=['processing_status', 'fence', 'lease_until', 'updated_at'])
            artifacts.append(artifact)
        return batch, artifacts

    @staticmethod
    def finish(artifact, *, error=''):
        return finish_one(
            artifact.pk, 1,
            extraction={'text': '技能 SQL', 'text_sha256': 'a' * 64},
            profile={'skills': {'value': ['SQL']}},
            match={'matrix': [{'verdict': 'MATCH'}], 'score': {'total': 1}},
            error=error,
        )

    def test_completed_batch_records_current_results_and_finishes_work_once(self):
        work, root, guard = self.scope
        batch, artifacts = self.create_batch('success', count=2)
        later_batch, later_artifacts = self.create_batch('success-later')
        original_policy = AgentRun.objects.get(pk=root.pk).policy

        self.assertTrue(self.finish(artifacts[0]))
        work.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual((batch.status, work.state), ('running', 'running'))

        self.assertTrue(self.finish(artifacts[1]))
        batch.refresh_from_db()
        work.refresh_from_db()
        self.assertEqual((batch.status, work.state), ('completed', 'running'))
        self.assertTrue(self.finish(later_artifacts[0]))
        later_batch.refresh_from_db()
        batch.refresh_from_db()
        work.refresh_from_db()
        root.refresh_from_db()
        self.assertEqual((batch.status, later_batch.status), ('completed', 'completed'))
        self.assertEqual(work.state, 'completed')
        self.assertIn('已完成', work.public_summary)
        self.assertEqual(root.state, 'running')
        self.assertEqual(root.policy, original_policy)

        references = list(AgentBusinessReference.objects.filter(root=root))
        batch_refs = {reference.object_id: reference for reference in references
                      if reference.domain_type == 'resume_batch'}
        self.assertEqual(set(batch_refs), {str(batch.pk), str(later_batch.pk)})
        self.assertEqual(batch_refs[str(batch.pk)].revision, str(batch.version))
        self.assertEqual(batch_refs[str(later_batch.pk)].revision, str(later_batch.version))
        self.assertTrue(all(len(reference.digest) == 64 for reference in batch_refs.values()))
        self.assertEqual(_hr_batch(batch_refs[str(batch.pk)])['data']['version'], batch.version)
        artifact_refs = {reference.object_id: reference for reference in references
                         if reference.domain_type == 'resume_artifact'}
        all_artifacts = [*artifacts, *later_artifacts]
        self.assertEqual(set(artifact_refs), {str(artifact.pk) for artifact in all_artifacts})
        for artifact in all_artifacts:
            reference = artifact_refs[str(artifact.pk)]
            self.assertEqual((reference.revision, reference.digest),
                             (str(artifact.version), artifact.sha256))
            self.assertEqual(_resume_artifact(reference)['data']['sha256'], artifact.sha256)
        self.assertEqual({item['domain_type'] for item in work.result_references},
                         {'resume_batch', 'resume_artifact'})
        self.assertEqual(len(work.result_references), 5)

        events = AgentEvent.objects.filter(root=root)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.type, 'work_completed')
        self.assertTrue(event.payload['finished_at'])
        self.assertIn('已完成', event.payload['summary'])
        self.assertFalse(self.finish(later_artifacts[0]))
        self.assertEqual(AgentBusinessReference.objects.filter(root=root).count(), len(references))
        self.assertEqual(events.count(), 1)

        self.assertEqual(guard.check().pk, root.pk)
        with self.assertRaises(AgentDenied):
            guard.check(write=True)

    def test_partial_failure_fails_work_and_keeps_completed_artifact_reference(self):
        work, root, _ = self.scope
        batch, artifacts = self.create_batch('partial', count=2)
        self.assertTrue(self.finish(artifacts[0]))
        self.assertTrue(self.finish(artifacts[1], error='synthetic_failure'))

        batch.refresh_from_db()
        work.refresh_from_db()
        self.assertEqual(batch.status, 'partial_failed')
        self.assertEqual(work.state, 'failed')
        self.assertIn('未完整完成', work.public_summary)
        self.assertEqual(set(AgentBusinessReference.objects.filter(
            root=root, domain_type='resume_artifact').values_list('object_id', flat=True)),
            {str(artifacts[0].pk)})
        self.assertEqual(AgentEvent.objects.get(root=root).type, 'work_failed')

    def test_unbound_legacy_batch_does_not_create_or_finish_agent_work(self):
        work, root, _ = self.scope
        original_work_count = AgentWorkTask.objects.count()
        batch, artifacts = self.create_batch('legacy', bound=False)

        self.assertTrue(self.finish(artifacts[0]))

        batch.refresh_from_db()
        work.refresh_from_db()
        self.assertEqual(batch.status, 'completed')
        self.assertEqual(work.state, 'running')
        self.assertEqual(AgentWorkTask.objects.count(), original_work_count)
        self.assertFalse(AgentBusinessReference.objects.filter(root=root).exists())
        self.assertFalse(AgentEvent.objects.filter(root=root).exists())

    def test_cancelled_revoked_corrected_and_late_runs_never_complete_work(self):
        cases = ('cancelled', 'corrected', 'revoked', 'late')
        for case in cases:
            with self.subTest(case=case):
                scope = self.create_scope(case)
                work, root, _ = scope
                batch, artifacts = self.create_batch(case, scope)
                artifact = artifacts[0]
                if case == 'cancelled':
                    AgentRun.objects.filter(pk=root.pk).update(state='cancelled')
                elif case == 'corrected':
                    AgentWorkTask.objects.filter(pk=work.pk).update(current_requirement_version=2)
                elif case == 'revoked':
                    self.owner.roles.clear()
                    User.objects.filter(pk=self.owner.pk).update(grant_version=F('grant_version') + 1)
                else:
                    ResumeArtifact.objects.filter(pk=artifact.pk).update(fence=F('fence') + 1)

                if case == 'late':
                    self.assertFalse(self.finish(artifact))
                else:
                    self.assertTrue(self.finish(artifact))

                work.refresh_from_db()
                batch.refresh_from_db()
                artifact.refresh_from_db()
                self.assertNotEqual(work.state, 'completed')
                self.assertFalse(AgentBusinessReference.objects.filter(root=root).exists())
                self.assertFalse(AgentEvent.objects.filter(root=root).exists())
                self.assertEqual(artifact.processing_status, 'running' if case == 'late' else 'failed')
                self.assertEqual(artifact.extracted_text, '')
                if case == 'corrected':
                    self.assertEqual(work.current_requirement_version, 2)
                if case == 'revoked':
                    self.owner.roles.add(Role.objects.get(code='hr'))
                    User.objects.filter(pk=self.owner.pk).update(grant_version=F('grant_version') + 1)
                    self.owner.refresh_from_db()
