from django.test import TransactionTestCase
from langgraph.store.memory import InMemoryStore
from deepagents.backends import StoreBackend

from portal.agent_runtime import AgentDenied, RuntimeGuard, RunBinding
from portal.agent_storage import scoped_backend
from portal.models import User
from portal.tests.test_agent_runtime import make_guard
from portal.agent_models import AgentSkillInstallation
from portal.agent_models import AgentRun
from portal.agent_skills import reviewed_catalog, skill_bundle


class StorageIsolationTests(TransactionTestCase):
    def test_all_backend_methods_are_scoped_for_same_owner_roots_and_children(self):
        store = InMemoryStore()
        root_guard = make_guard()
        root = root_guard.check()
        other_root = make_guard(user=User.objects.get(pk=root_guard.binding.owner_id))
        child = AgentRun.objects.create(root_run_id=root.pk, parent=root, conversation=root.conversation)
        child_guard = RuntimeGuard(RunBinding(**{**root_guard.binding.__dict__, "run_id": str(child.pk)}))
        own = scoped_backend(root_guard, store=store)
        own.write("/workspace/private.txt", "root-only")
        own.edit("/workspace/private.txt", "root-only", "authorized-root")
        self.assertTrue(own.grep("authorized-root", "/workspace").matches)
        self.assertTrue(own.glob("*.txt", "/workspace").matches)
        for other_guard in (other_root, child_guard):
            isolated = scoped_backend(other_guard, store=store)
            self.assertFalse(isolated.ls("/workspace").entries)
            self.assertFalse(isolated.glob("*.txt", "/workspace").matches)
            self.assertFalse(isolated.grep("authorized-root", "/workspace").matches)
            self.assertIsNotNone(isolated.read("/workspace/private.txt").error)
            self.assertIsNotNone(isolated.edit("/workspace/private.txt", "authorized-root", "attacker").error)
            isolated.write("/workspace/private.txt", "own separate namespace")
        self.assertIn("authorized-root", own.read("/workspace/private.txt").file_data["content"])

    def test_virtual_files_internal_offload_and_skills_are_isolated(self):
        store = InMemoryStore()
        user = User.objects.create_user(username="skill-owner", must_change_password=False)
        entry = reviewed_catalog()["source-check"]
        installation = AgentSkillInstallation.objects.create(owner=user,
            skill_id=entry["id"], version=entry["version"], digest=entry["digest"])
        first, second = make_guard(user=user), make_guard("other")
        digest = skill_bundle(first.binding.owner_id)["digest"]
        skills = StoreBackend(store=store, namespace=lambda _: ("agent-skills", digest))
        skills.write("/review/SKILL.md", "---\nname: review\ndescription: Reviewed synthetic skill\n---\nRead only.")
        own = scoped_backend(first, store=store, skill_digest=digest)
        other = scoped_backend(second, store=store, skill_digest=digest)
        own.write("/workspace/secret.txt", "private-a")
        self.assertIsNone(own.read("/workspace/secret.txt").error)
        self.assertIsNotNone(other.read("/workspace/secret.txt").error)
        own.upload_files([("/large_tool_results/internal.txt", b"offload-a")])
        self.assertIsNotNone(other.download_files(["/large_tool_results/internal.txt"])[0].error)
        self.assertIsNone(own.read("/skills/review/SKILL.md").error)
        with self.assertRaisesRegex(AgentDenied, "skill_not_installed"):
            other.read("/skills/review/SKILL.md")
        for path in ("/skills/review/SKILL.md", "/../secret", "C:/secret", "/workspace/../../etc/passwd",
                     "/workspace/..\\secret", "//server/share", "/workspace/%2e%2e/secret", "/etc/passwd"):
            with self.subTest(path=path), self.assertRaises(AgentDenied):
                own.write(path, "attack")
        with self.assertRaises(AgentDenied):
            own.upload_files([("/skills/review/SKILL.md", b"attack")])
        installation.enabled = False
        installation.save(update_fields=["enabled"])
        with self.assertRaisesRegex(AgentDenied, "skill_authorization_changed"):
            own.read("/skills/review/SKILL.md")
        User.objects.filter(pk=first.binding.owner_id).update(grant_version=2)
        for operation in (lambda: own.read("/workspace/secret.txt"),
                          lambda: own.download_files(["/large_tool_results/internal.txt"]),
                          lambda: own.read("/skills/review/SKILL.md")):
            with self.assertRaises(AgentDenied):
                operation()
