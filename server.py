from __future__ import annotations

import importlib
import sys
from pathlib import Path

src = Path(__file__).resolve().parent / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

main = importlib.import_module("llm_rpg_server.main")
app = main.app
app_api = main.app_api

__all__ = ["app", "app_api"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app_api", host="0.0.0.0", port=8008, reload=True)
