"""Same-cluster generations without changing the frozen general PG helper."""
import json
from pathlib import Path
import socket
import subprocess
import time

from qa.run_portable_postgres import PortablePostgres, ROOT, hidden_process_options
from qa.release_pipeline.identity import identity_alive, process_identity
from qa.browser_acceptance.process_job import port_listener_identity
from .contracts import owner_valid, require
from .process import process_image

class CrashPostgres(PortablePostgres):
    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.membership = membership
        self.generations = []
        self.current_identity = None
    def __enter__(self):
        super().__enter__()
        try:
            self._record_generation()
        except BaseException:
            self.close()
            raise
        return self
    def _record_generation(self):
        identity = process_identity(self.process.pid)
        require(identity["active"] and identity["pid"] in self.membership.members(), "pg_generation_outside_owned_job")
        require(process_image(identity) == Path(self.executable("postgres")).resolve(), "pg_binary_identity_mismatch")
        self.current_identity = dict(identity)
        self.generations.append({"generation": len(self.generations), "identity": dict(identity),
            "cluster": str(self.cluster), "port": self.port, "database": self.database_name})
        self.assert_live_owner()
    def assert_live_owner(self):
        self._assert_owner(require_pid=True)
        require(identity_alive(self.current_identity), "pg_generation_creation_changed")
        owner_valid(self.token, self.cluster,
            json.loads((self.run_dir / "owner.json").read_text(encoding="utf-8")),
            (self.cluster / "postmaster.pid").read_text(encoding="utf-8").splitlines(),
            self.current_identity, self.process, self.port)
        require(self.process.args == [self.executable("postgres"), "-D", str(self.cluster)], "pg_launch_arguments_changed")
        with self.connect() as connection:
            directory = connection.execute("SELECT current_setting('data_directory')").fetchone()[0]
        require(Path(directory).resolve() == self.cluster, "pg_sql_data_directory_mismatch")
        listener = port_listener_identity(self.port, self.membership.members())
        require(listener["owned"], "pg_listener_not_owned_loopback")
    def crash_restart(self, guard, on_stopped):
        self.assert_live_owner()
        expected_binary = Path(self.executable("postgres")).resolve()
        prior = []
        for pid in self.membership.members():
            identity = None
            try:
                identity = process_identity(pid)
                if identity["active"] and process_image(identity) == expected_binary:
                    prior.append(identity)
            except (OSError, ValueError):
                # An unavailable identity/image is not proof of process exit.
                # Only a fresh exact-Job query may establish this PID has left.
                require(pid not in self.membership.members(), "old_pg_member_identity_unverifiable")
                continue
        require(self.current_identity in prior and len(prior) <= 80, "old_pg_generation_inventory_missing")
        log = self.run_dir / "postgres-server.log"
        offset = log.stat().st_size
        before = time.monotonic()
        self.run([self.executable("pg_ctl"), "-D", str(self.cluster), "-m", "immediate", "-w", "-t", "30", "stop"],
                 name="owned-immediate-stop", timeout=40)
        self.process.wait(10)
        end = time.monotonic()+10
        while any(identity_alive(value) for value in prior):
            guard()
            require(time.monotonic() < end, "old_pg_generation_not_fully_exited")
            time.sleep(.1)
        require(not (self.cluster / "postmaster.pid").exists(), "stopped_pg_pid_file_remaining")
        require(not port_listener_identity(self.port, [])['listeners'], "same_port_has_unknown_listener")
        on_stopped()
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            reservation.bind(("127.0.0.1", self.port))
        finally:
            reservation.close()
        guard()
        # Do not call __enter__/initdb/create_database/pg_ctl restart. The tracked
        # Popen is updated immediately so normal helper cleanup targets this generation.
        # Same gated factory as the first generation; no post-spawn Job assignment.
        end = time.monotonic()+40
        self.process = self._launch_owned_postgres(deadline=end)
        self.evidence["postgres"]["pid"] = self.process.pid
        self.current_identity = process_identity(self.process.pid)
        self.save_evidence()
        import psycopg
        while True:
            guard()
            require(self.process.poll() is None, "restarted_pg_exited")
            try:
                with self.connect() as connection:
                    connection.execute("SELECT 1").fetchone()
                self._record_generation()
                break
            except psycopg.OperationalError:
                require(time.monotonic() < end, "restarted_pg_readiness_timeout")
                time.sleep(.2)
        require(log.stat().st_size-offset <= 512*1024, "restart_log_segment_budget_exceeded")
        with log.open("rb") as stream:
            stream.seek(offset)
            segment = stream.read(512*1024).decode("utf-8", errors="replace")
        self.evidence["generations"] = self.generations
        return {"mode": "official_pg_ctl_immediate", "actual_stop_restart_seconds": time.monotonic()-before,
            "old_generation_process_identities": prior, "old_generation_all_exited": True,
            "same_port_reused": True, "reinitialized": False, "log_segment_offset": offset}, segment
    def close(self):
        if self.current_identity and self.process is not None and self.process.poll() is None:
            if not identity_alive(self.current_identity) or self.current_identity["pid"] != self.process.pid:
                # Reject cluster commands, but release the exact already-owned
                # Job so a surviving controller cannot retain this child tree.
                return super().close(ownership_error="pg_current_creation_identity_changed")
        return super().close()
