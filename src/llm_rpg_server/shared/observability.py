from __future__ import annotations

from typing import Any


class Observability:
    def __init__(self):
        self.client: Any = None
        self.callback: Any = None
        try:
            from langfuse import get_client
            from langfuse.langchain import CallbackHandler

            self.client = get_client()
            self.callback = CallbackHandler()
        except Exception:
            pass

    def config(self, run_name: str, thread_id: str) -> dict[str, Any]:
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "run_name": run_name,
        }
        if self.callback is not None:
            config["callbacks"] = [self.callback]
        return config

    def flush(self) -> None:
        if self.client is None:
            return
        try:
            self.client.flush()
        except Exception:
            pass

