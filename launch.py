"""Launcher for Hugr Modern AI Analytics Application.

Starts the FastAPI server hosting the high-performance analytical engine
and the modern web application at http://127.0.0.1:8000.
"""

import os
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# Strip Claude Code's xkiro credentials from the environment before any
# project code runs. These three env vars are set by Claude Code itself
# to route its own traffic through a third-party proxy; they do not
# belong to the Hugr project. If the project inherits them, the agent
# attempts to call a model URL that does not respond (the proxy serves
# an OpenAI-style schema, not Anthropic's), and `/api/ask` 500s with
# `anthropic.NotFoundError`. Stripping at module import time ensures no
# `dtp.*` code ever sees them.
#
# If a real Anthropic key is needed for this project in the future, put
# it in `.env` (gitignored) and load it explicitly - do not repurpose
# Claude Code's xkiro credential.
# ----------------------------------------------------------------------
_CLAUDE_CODE_OWNED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")
for _var in _CLAUDE_CODE_OWNED_ENV:
    if os.environ.pop(_var, None) is not None:
        print(f"hugr: stripped Claude-Code-owned {_var} from environment")

import uvicorn  # noqa: E402  (imported after the env strip)

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Launching Hugr Modern AI Data Analyst at http://127.0.0.1:8000")
    print("=" * 60 + "\n")
    uvicorn.run("dtp.api.server:app", host="127.0.0.1", port=8000, log_level="info")
