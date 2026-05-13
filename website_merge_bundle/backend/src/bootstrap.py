from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


def ensure_workspace_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    workspace = root / "seoul_youth_housing_agent_work"
    load_dotenv(root / ".env", override=False)
    load_dotenv(workspace / ".env", override=False)
    if str(workspace) not in sys.path:
        sys.path.insert(0, str(workspace))
    return workspace
