"""
Agent-layer metrics, computed on demand from the agent_* tables (no
separate metrics store). These are the inputs the offline Improver will
consume later. Like store.py this only reads agent_* tables.

Definitions (rates are None when their denominator is 0):
  task_success_rate            COMPLETED / finished tasks
  first_pass_verification_rate steps verified on their first attempt / steps that reached a verdict
  verification_failure_rate    tasks ending VERIFICATION_FAILED / finished tasks
  escalation_rate              tasks ending ESCALATED or UNCERTAIN / finished tasks
  human_override_rate          approvals a human rejected / approvals decided
                               (Phase 1's only override signal; resolving an
                               escalation isn't recorded yet)
  tool_error_rate              tool calls that errored or were denied / tool calls
  replan_rate                  tasks that replanned / tasks
  average_steps_per_task       steps in each task's latest plan, averaged
  average_execution_time_s     wall time created -> finished for finished tasks
  tokens_per_task              model input+output tokens per task -- the
                               cost driver; multiply by your model's price
                               (cost_per_task is deliberately not hardcoded)
  approval_rate                approvals granted / approvals decided
"""

_FINISHED = "finished_at IS NOT NULL"


def _rate(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def compute_metrics(cur, hospital_id: int) -> dict:
    cur.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE {_FINISHED}),
            COUNT(*) FILTER (WHERE state = 'COMPLETED'),
            COUNT(*) FILTER (WHERE state = 'VERIFICATION_FAILED'),
            COUNT(*) FILTER (WHERE state IN ('ESCALATED', 'UNCERTAIN')),
            COUNT(*) FILTER (WHERE replan_count > 0),
            AVG(EXTRACT(EPOCH FROM (finished_at - created_at))) FILTER (WHERE {_FINISHED})
        FROM agent_tasks WHERE hospital_id = %s
        """,
        (hospital_id,),
    )
    total, finished, completed, verif_failed, escalated, replanned, avg_seconds = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE c.status <> 'success'), COUNT(*)
        FROM agent_tool_calls c JOIN agent_tasks t ON t.id = c.task_id WHERE t.hospital_id = %s
        """,
        (hospital_id,),
    )
    tool_errors, tool_calls = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE a.decision = 'approved'),
               COUNT(*) FILTER (WHERE a.decision = 'rejected'),
               COUNT(*) FILTER (WHERE a.decision IS NOT NULL)
        FROM agent_approvals a JOIN agent_tasks t ON t.id = a.task_id WHERE t.hospital_id = %s
        """,
        (hospital_id,),
    )
    approved, rejected, decided = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE s.state = 'VERIFIED' AND s.attempts = 1),
               COUNT(*) FILTER (WHERE s.state IN ('VERIFIED', 'FAILED'))
        FROM agent_steps s JOIN agent_tasks t ON t.id = s.task_id WHERE t.hospital_id = %s
        """,
        (hospital_id,),
    )
    first_pass, judged = cur.fetchone()

    cur.execute(
        """
        SELECT AVG(n) FROM (
            SELECT COUNT(*) AS n
            FROM agent_steps s
            JOIN agent_plans p ON p.id = s.plan_id
            JOIN agent_tasks t ON t.id = s.task_id
            WHERE t.hospital_id = %s
              AND p.version = (SELECT MAX(version) FROM agent_plans WHERE task_id = t.id)
            GROUP BY s.task_id
        ) per_task
        """,
        (hospital_id,),
    )
    avg_steps = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COALESCE(SUM((e.details->>'input_tokens')::bigint + (e.details->>'output_tokens')::bigint), 0)
        FROM agent_audit_events e JOIN agent_tasks t ON t.id = e.task_id
        WHERE t.hospital_id = %s AND e.event = 'model_call'
        """,
        (hospital_id,),
    )
    tokens = cur.fetchone()[0]

    return {
        "tasks_total": total,
        "tasks_finished": finished,
        "task_success_rate": _rate(completed, finished),
        "first_pass_verification_rate": _rate(first_pass, judged),
        "verification_failure_rate": _rate(verif_failed, finished),
        "escalation_rate": _rate(escalated, finished),
        "human_override_rate": _rate(rejected, decided),
        "tool_error_rate": _rate(tool_errors, tool_calls),
        "replan_rate": _rate(replanned, total),
        "average_steps_per_task": round(float(avg_steps), 2) if avg_steps is not None else None,
        "average_execution_time_s": round(float(avg_seconds), 3) if avg_seconds is not None else None,
        "tokens_per_task": round(tokens / total, 1) if total else None,
        "approval_rate": _rate(approved, decided),
    }
