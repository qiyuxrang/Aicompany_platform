import hashlib
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from django.apps import apps as live_apps
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from portal.hr_resume_storage import read_file
from portal.hr_retention import active_artifacts, active_batches, active_requests
from portal.product_storage import StorageError


BEFORE_AGENT = "0029_tender_extraction_evidence"
AFTER_AGENT = "0030_agent_platform"
AFTER_HR_RETENTION = "0031_hr_long_term_retention"
AFTER_LEDGER_IDENTITY = "0032_business_record_identity"
AFTER_PRODUCT_GUARDS = "0033_agent_product_guards"
AFTER_HR_GUARDS = "0034_agent_hr_guards"
AGENT_FIELDS = (
    "agent_root_id", "agent_work_id", "agent_requirement_version",
    "agent_grant_version", "agent_session_version", "agent_root_fence",
)


class AgentMigrationRehearsalTests(TransactionTestCase):
    reset_sequences = True

    def migrate_to(self, name=None):
        executor = MigrationExecutor(connection)
        targets = [("portal", name)] if name else executor.loader.graph.leaf_nodes()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def make_resume(self, apps, root, owner, key, *, old=False, deleted=False):
        Request = apps.get_model("portal", "RecruitmentRequest")
        JDVersion = apps.get_model("portal", "JDVersion")
        Batch = apps.get_model("portal", "ResumeScreeningBatch")
        Artifact = apps.get_model("portal", "ResumeArtifact")
        request = Request.objects.create(created_by=owner, updated_by=owner, position_name=key)
        jd = JDVersion.objects.create(request=request, version=1, input_version=1,
                                      body="isolated migration rehearsal", created_by=owner)
        batch = Batch.objects.create(jd_version=jd, created_by=owner, idempotency_key=key,
                                     input_version=1, requirements={})
        file_id = str(uuid.uuid4())
        content = f"synthetic-{key}".encode()
        (root / file_id).write_bytes(content)
        artifact = Artifact.objects.create(batch=batch, uploaded_by=owner, file_id=file_id,
                                           filename=f"{key}.txt", sha256=hashlib.sha256(content).hexdigest(),
                                           size=len(content))
        if deleted:
            (root / file_id).with_suffix(".delete").write_text("", encoding="ascii")
        if old:
            aged = timezone.now() - timedelta(days=16)
            for model, row in ((Request, request), (JDVersion, jd), (Batch, batch), (Artifact, artifact)):
                model.objects.filter(pk=row.pk).update(created_at=aged)
        return {
            "request_id": request.pk,
            "batch_id": batch.pk,
            "artifact_id": artifact.pk,
            "file_id": file_id,
            "content": content,
            "created_at": artifact.created_at,
        }

    def assert_hr_classification(self, apps, valid, expired, deleted, root):
        RecruitmentRequest = apps.get_model("portal", "RecruitmentRequest")
        ResumeArtifact = apps.get_model("portal", "ResumeArtifact")
        ResumeScreeningBatch = apps.get_model("portal", "ResumeScreeningBatch")

        self.assertEqual(RecruitmentRequest.objects.get(pk=valid["request_id"]).archive_state, "active")
        self.assertEqual(ResumeScreeningBatch.objects.get(pk=valid["batch_id"]).archive_state, "active")
        self.assertEqual(ResumeArtifact.objects.get(pk=valid["artifact_id"]).archive_state, "active")
        self.assertEqual(RecruitmentRequest.objects.get(pk=expired["request_id"]).archive_state,
                         "legacy_expired")
        self.assertEqual(ResumeScreeningBatch.objects.get(pk=expired["batch_id"]).archive_state,
                         "legacy_expired")
        self.assertEqual(ResumeArtifact.objects.get(pk=expired["artifact_id"]).archive_state,
                         "legacy_expired")
        self.assertEqual(ResumeArtifact.objects.get(pk=deleted["artifact_id"]).archive_state,
                         "file_deleted")
        self.assertTrue(active_requests(RecruitmentRequest.objects.all()).filter(
            pk=valid["request_id"]).exists())
        self.assertTrue(active_batches(ResumeScreeningBatch.objects.all()).filter(
            pk=valid["batch_id"]).exists())
        self.assertTrue(active_artifacts(ResumeArtifact.objects.all()).filter(
            pk=valid["artifact_id"]).exists())
        self.assertFalse(active_requests(RecruitmentRequest.objects.all()).filter(
            pk=expired["request_id"]).exists())
        self.assertFalse(active_batches(ResumeScreeningBatch.objects.all()).filter(
            pk=expired["batch_id"]).exists())
        self.assertFalse(active_artifacts(ResumeArtifact.objects.all()).filter(
            pk__in=[expired["artifact_id"], deleted["artifact_id"]]).exists())
        self.assertEqual(read_file(valid["file_id"], hashlib.sha256(valid["content"]).hexdigest()),
                         valid["content"])
        with self.assertRaises(StorageError):
            read_file(deleted["file_id"], hashlib.sha256(deleted["content"]).hexdigest())
        self.assertTrue((root / deleted["file_id"]).with_suffix(".delete").exists())

    def assert_hr_binding_guards(self, apps, valid, expired, deleted):
        from portal.hr_agent import current_artifact, lock_batch_scope
        from portal.product_service import ProductError

        Batch = apps.get_model("portal", "ResumeScreeningBatch")
        for field in AGENT_FIELDS:
            self.assertTrue(Batch._meta.get_field(field).null, field)
        for sample in (valid, expired, deleted):
            batch = Batch.objects.get(pk=sample["batch_id"])
            self.assertTrue(all(getattr(batch, field) is None for field in AGENT_FIELDS))
            self.assertIsNone(lock_batch_scope(batch))

        batch = Batch.objects.get(pk=valid["batch_id"])
        values = dict(zip(AGENT_FIELDS, (str(uuid.uuid4()), str(uuid.uuid4()), 1, 1, 1, 1)))
        for missing in AGENT_FIELDS:
            with self.subTest(missing=missing):
                Batch.objects.filter(pk=batch.pk).update(**{**values, missing: None})
                with self.assertRaises(ProductError) as denied:
                    lock_batch_scope(Batch.objects.get(pk=batch.pk))
                self.assertEqual(denied.exception.code, "agent_binding_stale")
        Batch.objects.filter(pk=batch.pk).update(**values)
        with self.assertRaises(ProductError) as forged:
            lock_batch_scope(Batch.objects.get(pk=batch.pk))
        self.assertEqual(forged.exception.code, "agent_binding_stale")
        Batch.objects.filter(pk=batch.pk).update(**dict.fromkeys(AGENT_FIELDS))

        for sample, expected in ((valid, "permission_changed"), (expired, "expired"), (deleted, "expired")):
            with self.subTest(legacy_access=expected):
                with self.assertRaises(StorageError) as denied:
                    current_artifact(sample["artifact_id"])
                self.assertEqual(denied.exception.code, expected)

    def test_0029_to_0034_isolated_data_and_guard_rehearsal(self):
        # All supported databases must retain historical data and enforce guards.
        self.rehearse_migrations()

    def test_sqlite_backup_and_restore_rehearsal(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite backup API rehearsal; PostgreSQL data/guards run separately")
        self.rehearse_migrations(include_sqlite_restore=True)

    def rehearse_migrations(self, *, include_sqlite_restore=False):
        with tempfile.TemporaryDirectory(prefix="agent-migration-rehearsal-") as temp:
            root = Path(temp) / "hr-files"
            root.mkdir()
            with override_settings(HR_STORAGE_ROOT=str(root)):
                try:
                    apps = self.migrate_to(BEFORE_AGENT)
                    User = apps.get_model("portal", "User")
                    owner = User.objects.create(username="migration-rehearsal-owner", password="unused")
                    valid = self.make_resume(apps, root, owner, "valid")
                    expired = self.make_resume(apps, root, owner, "expired", old=True)
                    deleted = self.make_resume(apps, root, owner, "deleted", deleted=True)

                    HrJobTask = apps.get_model("portal", "HrJobTask")
                    old_job = HrJobTask.objects.create(owner=owner, title="expired legacy job")
                    aged = timezone.now() - timedelta(days=16)
                    HrJobTask.objects.filter(pk=old_job.pk).update(created_at=aged)

                    Workbook = apps.get_model("portal", "BusinessLedgerWorkbook")
                    LedgerRevision = apps.get_model("portal", "BusinessLedgerRevision")
                    published_records = [{"id": "legacy-row", "owner": "business owner", "amount": "125"}]
                    published = Workbook.objects.create(
                        department="finance", state="published", revision=7, records=published_records,
                        created_by=owner, updated_by=owner, published_by=owner,
                        published_at=timezone.now(),
                    )
                    published_revision = LedgerRevision.objects.create(
                        workbook=published, revision=7, state="published", source_name="legacy",
                        records=published_records, checksum="a" * 64, action="publish", actor=owner,
                    )
                    draft_records = [{"id": "unknown-author-row", "amount": "250"}]
                    draft = Workbook.objects.create(
                        department="presales", state="draft", revision=3, records=draft_records,
                        created_by=owner, updated_by=owner,
                    )

                    DocumentTaskModel = apps.get_model("portal", "DocumentTask")
                    DocumentRevisionModel = apps.get_model("portal", "DocumentRevision")
                    DocumentSourceModel = apps.get_model("portal", "DocumentSource")
                    task = DocumentTaskModel.objects.create(
                        owner=owner, title="legacy product task", state="COMPLETED", stage="FINAL_REVIEW",
                        version=4, input_version=2, idempotency_key="legacy-task",
                        payload_hash="b" * 64,
                    )
                    source_hash = hashlib.sha256(b"synthetic source bytes").hexdigest()
                    source = DocumentSourceModel.objects.create(
                        task=task, original_name="source.txt", media_type="text/plain",
                        path="isolated/source.txt", sha256=source_hash, size=21,
                        parsed={}, warnings=[], uploaded_by=owner,
                    )
                    source_revision = DocumentRevisionModel.objects.create(
                        task=task, kind="input", version=2,
                        payload={"source_id": str(source.pk)}, sha256=source_hash,
                    )

                    if include_sqlite_restore:
                        backup_path = Path(temp) / "before-migration.sqlite3"
                        restored_path = Path(temp) / "restored-before-migration.sqlite3"
                        connection.ensure_connection()
                        with closing(sqlite3.connect(backup_path)) as backup:
                            connection.connection.backup(backup)
                            backup.commit()
                        with closing(sqlite3.connect(backup_path)) as backup, closing(
                                sqlite3.connect(restored_path)) as restored:
                            backup.backup(restored)
                            restored.commit()
                        with closing(sqlite3.connect(backup_path)) as backup, closing(
                                sqlite3.connect(restored_path)) as restored:
                            table = Workbook._meta.db_table
                            columns = [row[1] for row in backup.execute(f"PRAGMA table_info({table})")]
                            self.assertNotIn("record_meta", columns)
                            original = backup.execute(
                                f"SELECT state, records, published_by_id FROM {table} WHERE id = ?",
                                [published.pk.hex],
                            ).fetchone()
                            self.assertIsNotNone(original)
                            self.assertEqual(restored.execute(
                                f"SELECT state, records, published_by_id FROM {table} WHERE id = ?",
                                [published.pk.hex],
                            ).fetchone(), original)
                            self.assertEqual(restored.execute(
                                "SELECT name FROM django_migrations WHERE app='portal' AND name IN (?, ?, ?, ?, ?) ORDER BY name",
                                [AFTER_AGENT, AFTER_HR_RETENTION, AFTER_LEDGER_IDENTITY,
                                 AFTER_PRODUCT_GUARDS, AFTER_HR_GUARDS],
                            ).fetchall(), [])

                    apps = self.migrate_to(AFTER_AGENT)
                    migrated_user = apps.get_model("portal", "User").objects.get(pk=owner.pk)
                    self.assertEqual(migrated_user.department_code, "")
                    task_at_0030 = apps.get_model("portal", "DocumentTask").objects.get(pk=task.pk)
                    self.assertEqual((task_at_0030.state, task_at_0030.version), ("COMPLETED", 4))

                    apps = self.migrate_to(AFTER_HR_RETENTION)
                    self.assert_hr_classification(apps, valid, expired, deleted, root)
                    jobs = apps.get_model("portal", "HrJobTask")
                    self.assertEqual(jobs.objects.get(pk=old_job.pk).archive_state, "legacy_expired")
                    RecruitmentRequest = apps.get_model("portal", "RecruitmentRequest")
                    ResumeArtifact = apps.get_model("portal", "ResumeArtifact")
                    ResumeScreeningBatch = apps.get_model("portal", "ResumeScreeningBatch")
                    valid_timestamps = (
                        RecruitmentRequest.objects.get(pk=valid["request_id"]).created_at,
                        ResumeScreeningBatch.objects.get(pk=valid["batch_id"]).created_at,
                        ResumeArtifact.objects.get(pk=valid["artifact_id"]).created_at,
                    )
                    aged = timezone.now() - timedelta(days=16)
                    for model, key in ((RecruitmentRequest, "request_id"),
                                       (ResumeScreeningBatch, "batch_id"),
                                       (ResumeArtifact, "artifact_id")):
                        model.objects.filter(pk=valid[key]).update(created_at=aged)
                    self.assert_hr_classification(apps, valid, expired, deleted, root)
                    for model, key, created_at in zip(
                            (RecruitmentRequest, ResumeScreeningBatch, ResumeArtifact),
                            ("request_id", "batch_id", "artifact_id"), valid_timestamps):
                        model.objects.filter(pk=valid[key]).update(created_at=created_at)

                    apps = self.migrate_to(AFTER_LEDGER_IDENTITY)
                    Workbook = apps.get_model("portal", "BusinessLedgerWorkbook")
                    LedgerRevision = apps.get_model("portal", "BusinessLedgerRevision")
                    published_row = Workbook.objects.get(pk=published.pk)
                    self.assertEqual(published_row.state, "published")
                    self.assertEqual(published_row.records, published_records)
                    self.assertEqual(published_row.published_by_id, owner.pk)
                    self.assertEqual(published_row.record_meta, {})
                    published_version = LedgerRevision.objects.get(pk=published_revision.pk)
                    self.assertEqual(published_version.state, "published")
                    self.assertEqual(published_version.records, published_records)
                    self.assertEqual(published_version.record_meta, {})
                    draft_row = Workbook.objects.get(pk=draft.pk)
                    self.assertEqual(draft_row.state, "draft")
                    self.assertEqual(draft_row.record_meta, {})

                    apps = self.migrate_to(AFTER_PRODUCT_GUARDS)
                    DocumentTask = apps.get_model("portal", "DocumentTask")
                    DocumentSource = apps.get_model("portal", "DocumentSource")
                    DocumentRevision = apps.get_model("portal", "DocumentRevision")
                    task_row = DocumentTask.objects.get(pk=task.pk)
                    self.assertEqual((task_row.state, task_row.version), ("COMPLETED", 4))
                    self.assertTrue(all(getattr(task_row, field) is None for field in AGENT_FIELDS))
                    source_row = DocumentSource.objects.get(pk=source.pk)
                    revision_row = DocumentRevision.objects.get(pk=source_revision.pk)
                    self.assertEqual((source_row.sha256, revision_row.version), (source_hash, 2))
                    self.assertEqual(revision_row.payload["source_id"], str(source_row.pk))
                    with self.assertRaises(ValidationError) as invalid_version:
                        DocumentRevision(task=task_row, kind="input", version=0,
                                         payload={}, sha256="c" * 64).full_clean()
                    self.assertIn("version", invalid_version.exception.message_dict)

                    apps = self.migrate_to(AFTER_HR_GUARDS)
                    self.assert_hr_classification(apps, valid, expired, deleted, root)
                    batch_model = apps.get_model("portal", "ResumeScreeningBatch")
                    for sample in (valid, expired, deleted):
                        batch = batch_model.objects.get(pk=sample["batch_id"])
                        self.assertTrue(all(getattr(batch, field) is None for field in AGENT_FIELDS))

                    apps = self.migrate_to()
                    with transaction.atomic():
                        self.assert_hr_binding_guards(apps, valid, expired, deleted)
                    self.assertEqual(
                        apps.get_model("portal", "User")._meta.get_field("department_code").choices,
                        live_apps.get_model("portal", "User")._meta.get_field("department_code").choices,
                    )

                    rollback_apps = self.migrate_to(AFTER_AGENT)
                    old_request = rollback_apps.get_model("portal", "RecruitmentRequest").objects.get(
                        pk=expired["request_id"])
                    self.assertLess(old_request.created_at, timezone.now() - timedelta(days=15))
                    self.assertTrue((root / deleted["file_id"]).with_suffix(".delete").exists())
                    with self.assertRaises(StorageError):
                        read_file(deleted["file_id"], hashlib.sha256(deleted["content"]).hexdigest())
                    rollback_workbook = rollback_apps.get_model(
                        "portal", "BusinessLedgerWorkbook").objects.get(pk=published.pk)
                    self.assertEqual(rollback_workbook.state, "published")
                    self.assertEqual(rollback_workbook.records, published_records)
                    rollback_task = rollback_apps.get_model("portal", "DocumentTask").objects.get(pk=task.pk)
                    self.assertEqual((rollback_task.state, rollback_task.version), ("COMPLETED", 4))
                    self.assertEqual(rollback_apps.get_model("portal", "DocumentSource").objects.get(
                        pk=source.pk).sha256, source_hash)

                    apps = self.migrate_to(AFTER_HR_RETENTION)
                    self.assert_hr_classification(apps, valid, expired, deleted, root)
                    apps = self.migrate_to(AFTER_LEDGER_IDENTITY)
                    self.assertEqual(
                        apps.get_model("portal", "BusinessLedgerWorkbook").objects.get(
                            pk=published.pk).state,
                        "published",
                    )
                    self.assertEqual(
                        apps.get_model("portal", "BusinessLedgerWorkbook").objects.get(
                            pk=draft.pk).state,
                        "draft",
                    )
                    apps = self.migrate_to(AFTER_PRODUCT_GUARDS)
                    apps = self.migrate_to(AFTER_HR_GUARDS)
                    self.assert_hr_classification(apps, valid, expired, deleted, root)
                finally:
                    self.migrate_to()
                    executor = MigrationExecutor(connection)
                    self.assertEqual(executor.migration_plan(executor.loader.graph.leaf_nodes()), [])
