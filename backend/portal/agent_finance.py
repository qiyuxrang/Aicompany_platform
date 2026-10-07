from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from .agent_models import AgentBusinessReference, AgentRun, AgentWorkTask, append_public_event
from .business_boards import _checksum, _row_checksum
from .business_models import BusinessLedgerRevision, BusinessLedgerWorkbook
from .product_agent import _record_reference, lock_scope
from .product_service import ProductError


@transaction.atomic
def reconcile_finance_work(work_id):
    snapshot = AgentWorkTask.objects.select_related("owner").filter(
        pk=work_id, department_code="finance", state__in=("running", "waiting_confirmation")).first()
    if snapshot is None:
        return False
    run = AgentRun.objects.filter(work=snapshot, parent__isnull=True).first()
    if run is None or not isinstance(run.policy, dict):
        return False
    identity = run.policy.get("identity", {})
    try:
        root, work = lock_scope(run.pk, snapshot.pk, snapshot.owner,
            snapshot.current_requirement_version, identity.get("grant_version"),
            identity.get("session_version"), run.policy.get("fence"), department="finance")
    except ProductError:
        return False
    references = AgentBusinessReference.objects.filter(
        root=root, work=work, requirement=root.requirement,
        domain_type="finance_record").order_by("-created_at", "-id")
    latest = {}
    for reference in references:
        latest.setdefault(reference.object_id, reference)
    if not latest:
        return False
    expected = {}
    for project_id, reference in latest.items():
        if not reference.revision.isdecimal():
            return False
        source = BusinessLedgerRevision.objects.filter(
            workbook__department="finance", revision=int(reference.revision),
            actor_id=work.owner_id).first()
        if source is None:
            return False
        entry = next(((record_id, meta) for record_id, meta in source.record_meta.items()
            if meta.get("project_id") == project_id and meta.get("record_author_id") == work.owner_id
            and meta.get("draft_actor_id") == work.owner_id and not meta.get("deleted")), None)
        row = next((row for row in source.records if row.get("project_id") == project_id), None)
        if (entry is None or row is None or entry[1].get("draft_revision") != source.revision
                or entry[1].get("draft_checksum") != _row_checksum(row)):
            return False
        expected[entry[0]] = entry[1]
    published = BusinessLedgerRevision.objects.filter(workbook__department="finance",
        actor_id=work.owner_id, state=BusinessLedgerWorkbook.State.PUBLISHED,
        action="publish", revision__gt=max(int(item.revision) for item in latest.values())
    ).order_by("-revision")
    for revision in published.iterator():
        values = SimpleNamespace(department="finance", state=revision.state,
                                 as_of=revision.as_of, records=revision.records)
        if _checksum(values) != revision.checksum:
            continue
        rows = {row.get("project_id"): row for row in revision.records}
        if not all(revision.record_meta.get(record_id) == entry
            and _row_checksum(rows.get(entry["project_id"])) == entry["draft_checksum"]
            for record_id, entry in expected.items()):
            continue
        _record_reference(root, work, "business_revision", revision.pk, revision.revision,
            revision.checksum, "本人精确版本已发布", f"finance:published:{revision.pk}", result=True)
        work.state = "completed"
        work.public_summary = f"本人已确认并发布 {len(expected)} 条精确版本记录。"
        work.save(update_fields=["state", "public_summary", "updated_at"])
        append_public_event(root.pk, root.pk, f"finance:completed:{work.pk}", "work_completed",
                            {"summary": work.public_summary,
                             "finished_at": timezone.now().isoformat()}, work=work)
        return True
    return False
