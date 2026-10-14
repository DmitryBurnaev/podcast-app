from typing import Any, Protocol


class TaskQueue(Protocol):
    """Queue boundary shared by HTTP adapters and application operations."""

    def enqueue(self, task: Any, *args: Any, **kwargs: Any) -> Any: ...


class TaskQueueApp(Protocol):
    rq_queue: TaskQueue
