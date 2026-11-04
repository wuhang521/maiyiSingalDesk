"""Make every backend test module runnable independently of collection order."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TASK_DATABASE_URL", "sqlite:///./test-signaldesk-tasks.db")
