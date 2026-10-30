import json
import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage

from domain.locks import TaskQueue, WorkflowLockStore
from domain.persistence import WorkflowStore
from domain.models import WorkflowRun, WorkflowStatus
from domain.observability import timed_event
from graph import grafo

logger = logging.getLogger(__name__)


def execute_task(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    thread_id = str(payload.get("thread_id", request_id))
    user_id = str(payload["user_id"])
    text = str(payload["text"])
    store = WorkflowStore(os.getenv("WORKFLOW_DB_PATH", "workspace.db"))
    store.save(
        WorkflowRun(
            request_id=request_id,
            workflow=str(payload.get("workflow", "async")),
            user_id=user_id,
            objective=text,
            status=WorkflowStatus.PRODUCING,
        )
    )
    latest: dict[str, Any] = {}
    try:
        with timed_event("worker_workflow", request_id=request_id, workflow="async"):
            for event in grafo.stream(
                {
                    "messages": [HumanMessage(content=text)],
                    "request_id": request_id,
                    "user_id": user_id,
                },
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="values",
            ):
                latest = event
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"
        run = store.get(request_id)
        if run:
            run.status = WorkflowStatus.FAILED
            run.errors = [f"Falha no worker: {detail}"]
            store.save(run)
        logger.exception("Workflow assíncrono falhou: %s", request_id)
        return {"request_id": request_id, "status": WorkflowStatus.FAILED.value, "error": detail}
    run = store.get(request_id)
    if run:
        run.status = (
            WorkflowStatus.APPROVAL_REQUIRED
            if "__interrupt__" in latest
            else WorkflowStatus.APPROVED
        )
        run.iteration = latest.get("iteration", 0)
        if latest.get("quality_report"):
            run.quality_report = latest["quality_report"]
        if latest.get("research_sources"):
            run.sources = latest["research_sources"]
        if latest.get("plan"):
            run.metadata["plan"] = latest["plan"]
        store.save(run)
    return {"request_id": request_id, "status": run.status.value if run else "failed"}


def worker_once(queue_name: str = "workflows", timeout_seconds: int = 1) -> dict[str, Any] | None:
    task_queue = TaskQueue()
    payload = task_queue.dequeue(queue_name, timeout_seconds)
    if payload is None:
        return None
    data = json.loads(payload)
    lock_store = WorkflowLockStore(
        os.getenv("WORKFLOW_DB_PATH", "workspace.db"),
        redis_url=os.getenv("REDIS_URL", ""),
        timeout_seconds=int(os.getenv("REDIS_LOCK_TIMEOUT_SECONDS", "30")),
    )
    with lock_store.acquire(f"workflow:{data['request_id']}", f"worker:{os.getpid()}"):
        return execute_task(data)


def run_forever(queue_name: str = "workflows") -> None:
    while True:
        worker_once(queue_name, timeout_seconds=5)


if __name__ == "__main__":
    run_forever()
