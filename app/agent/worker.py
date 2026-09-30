"""
Background worker for agent tasks (Phase 3).

Threads inside the API process poll `agent_jobs`, claim one job at a time
with FOR UPDATE SKIP LOCKED (so several threads, and several app
processes, never work the same job), and hand it to the orchestrator.
There is no separate broker or service: the queue is a table in the
database the app already uses.

Failure model:
  * A claimed job holds a lease (store.JOB_LEASE_MINUTES). If the process
    dies mid-task the lease expires, another worker re-claims the job, and
    Orchestrator.process_job continues from the task's recorded state --
    completed steps are skipped and a write that already happened is never
    repeated (precheck / recorded-call replay / unique idempotency index).
  * An exception escaping the orchestrator (it normally turns every
    failure into a terminal task state) requeues the job with backoff;
    after max_attempts the job is `failed` and the task is ESCALATED.
  * A task cancelled while queued or running is simply a terminal task
    when its job is processed: the job completes as a no-op.
  * Shutdown is cooperative: a running job finishes its current call; if
    the process is killed instead, the lease covers it.
"""

import logging
import os
import socket
import threading

from app import config
from app.agent import store
from app.agent.orchestrator import Orchestrator

logger = logging.getLogger("app.agent.worker")

BACKOFF_SECONDS = (5, 30, 120)


class _Heartbeat:
    """Renews a claimed job's lease until stopped. If the job has been taken
    over (renewal matches no row), it stops and records `lost`."""

    def __init__(self, connect, job_id: int, worker_id: str, lease_seconds: int, interval: float):
        self._connect, self._job_id, self._worker_id = connect, job_id, worker_id
        self._lease, self._interval = lease_seconds, interval
        self._stop = threading.Event()
        self.lost = False
        self._thread = threading.Thread(target=self._run, name=f"agent-job-{job_id}-heartbeat", daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        if not store.extend_lease(cur, self._job_id, self._worker_id, lease_seconds=self._lease):
                            self.lost = True
                            logger.warning("agent job %s was taken over by another worker", self._job_id)
                            return
            except Exception:  # a missed beat is survivable; the next one retries
                logger.exception("agent job %s heartbeat failed", self._job_id)


class AgentWorker:
    def __init__(self, orchestrator: Orchestrator, *, name: str | None = None, connection_factory=None,
                 lease_seconds: int | None = None):
        self.orchestrator = orchestrator
        self.lease_seconds = lease_seconds if lease_seconds is not None else config.AGENT_JOB_LEASE_SECONDS
        self.name = name or f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        if connection_factory is None:
            from app.db.connection import get_connection as connection_factory
        self._connect = connection_factory

    def run_once(self) -> bool:
        """Claim and process at most one job. True if a job was handled."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                job = store.claim_job(cur, self.name, lease_seconds=self.lease_seconds)
        if job is None:
            return False

        task_id, job_id = job["task_id"], job["id"]

        if job["attempts"] > job["max_attempts"]:
            self._give_up(job, "the job exceeded its maximum attempts")
            return True

        try:
            with _Heartbeat(self._connect, job_id, self.name, self.lease_seconds, self.lease_seconds / 3):
                outcome = self.orchestrator.process_job(task_id, kind=job["kind"])
        except Exception as exc:
            logger.exception("agent job %s (task %s) raised", job_id, task_id)
            self._retry_or_give_up(job, f"{type(exc).__name__}: {exc}")
            return True

        with self._connect() as conn:
            with conn.cursor() as cur:
                if store.finish_job(cur, job_id, status="done", worker_id=self.name):
                    store.add_event(cur, task_id, "job_finished",
                                    details={"job_id": job_id, "outcome": outcome, "worker": self.name})
                else:
                    logger.warning("agent job %s finished by %s but had been taken over; leaving it to the new owner",
                                   job_id, self.name)
        return True

    def drain(self, limit: int = 1000) -> int:
        """Process jobs until none are runnable. Used by tests and scripts."""
        handled = 0
        while handled < limit and self.run_once():
            handled += 1
        return handled

    # -- failure handling -------------------------------------------------

    def _retry_or_give_up(self, job: dict, error: str) -> None:
        if job["attempts"] >= job["max_attempts"]:
            self._give_up(job, error)
            return
        delay = BACKOFF_SECONDS[min(job["attempts"] - 1, len(BACKOFF_SECONDS) - 1)]
        with self._connect() as conn:
            with conn.cursor() as cur:
                store.requeue_job(cur, job["id"], delay_seconds=delay, error=error)
                store.add_event(cur, job["task_id"], "job_requeued",
                                details={"job_id": job["id"], "error": error, "delay_seconds": delay})

    def _give_up(self, job: dict, error: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                store.finish_job(cur, job["id"], status="failed", error=error, worker_id=self.name)
                task = store.get_task_by_id(cur, job["task_id"])
        if task is not None:
            self.orchestrator.escalate(job["task_id"], task["hospital_id"], f"background job failed: {error}")


class WorkerPool:
    """N polling threads, started/stopped with the app."""

    def __init__(self, make_orchestrator, *, threads: int, poll_seconds: float):
        self._make = make_orchestrator
        self._threads_n = threads
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(self._threads_n):
            worker = AgentWorker(self._make(), name=f"{socket.gethostname()}:{os.getpid()}:w{i}")
            t = threading.Thread(target=self._loop, args=(worker,), name=f"agent-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        logger.info("agent worker pool started (%d thread(s))", self._threads_n)

    def _loop(self, worker: AgentWorker) -> None:
        while not self._stop.is_set():
            try:
                handled = worker.run_once()
            except Exception:  # a claim/DB failure must not kill the thread
                logger.exception("agent worker loop error")
                handled = False
            if not handled:
                self._stop.wait(self._poll)

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()


def build_worker_pool() -> WorkerPool | None:
    """The pool the app starts, or None when the layer isn't enabled
    (no provider configured, or AGENT_EXECUTION_MODE=inline)."""
    from app.agent.llm import LLMUnavailable, build_default_llm
    from app.agent.tools.hospital import build_default_registry

    if config.AGENT_EXECUTION_MODE != "background" or config.AGENT_WORKER_THREADS < 1:
        return None
    try:
        build_default_llm()
    except LLMUnavailable:
        return None

    def make() -> Orchestrator:
        return Orchestrator(build_default_llm(), build_default_registry())

    return WorkerPool(make, threads=config.AGENT_WORKER_THREADS, poll_seconds=config.AGENT_WORKER_POLL_SECONDS)
