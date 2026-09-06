"""Launcher for Hugr Modern AI Analytics Application.

Starts the FastAPI server hosting the high-performance analytical engine
and the modern web application at http://127.0.0.1:8000.
"""

import sys
from pathlib import Path
import uvicorn

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Launching Hugr Modern AI Data Analyst at http://127.0.0.1:8000")
    print("=" * 60 + "\n")
    uvicorn.run("dtp.api.server:app", host="127.0.0.1", port=8000, log_level="info")
