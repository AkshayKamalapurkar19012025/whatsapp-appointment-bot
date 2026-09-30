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


class AgentWorker:
    def __init__(self, orchestrator: Orchestrator, *, name: str | None = None, connection_factory=None):
        self.orchestrator = orchestrator
        self.name = name or f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        if connection_factory is None:
            from app.db.connection import get_connection as connection_factory
        self._connect = connection_factory

    def run_once(self) -> bool:
        """Claim and process at most one job. True if a job was handled."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                job = store.claim_job(cur, self.name)
        if job is None:
            return False

        task_id, job_id = job["task_id"], job["id"]

        if job["attempts"] > job["max_attempts"]:
            self._give_up(job, "the job exceeded its maximum attempts")
            return True

        try:
            outcome = self.orchestrator.process_job(task_id, kind=job["kind"])
        except Exception as exc:
            logger.exception("agent job %s (task %s) raised", job_id, task_id)
            self._retry_or_give_up(job, f"{type(exc).__name__}: {exc}")
            return True

        with self._connect() as conn:
            with conn.cursor() as cur:
                store.finish_job(cur, job_id, status="done")
                store.add_event(cur, task_id, "job_finished", details={"job_id": job_id, "outcome": outcome,
                                                                       "worker": self.name})
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
                store.finish_job(cur, job["id"], status="failed", error=error)
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
