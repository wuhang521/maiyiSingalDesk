"""Isolated local backend for the Electron member-management E2E test."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
database = os.environ.get("TASK_DATABASE_URL", "")
if not database.startswith("sqlite:///") or not database.endswith("/member-e2e.db"):
    raise RuntimeError("This preview requires its own member-e2e.db SQLite database")
if Path(database.removeprefix("sqlite:///")).exists():
    raise RuntimeError("Refusing to overwrite an existing preview database")

from app.main import app
from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Staff, UserAccount
import uvicorn

Base.metadata.create_all(engine)
with SessionLocal() as db:
    for name, username, role in [("本地测试管理员", "preview-admin", "admin"), ("本地普通成员", "preview-member", "member")]:
        db.add(UserAccount(username=username, staff=Staff(name=name), role=role,
                           password_hash=hash_password("preview-only-password")))
    db.add(Staff(name="待开通账号的人员"))
    db.commit()

@app.get("/preview-release")
def preview_release():
    return {"tag_name": "v0.4.0", "assets": [], "html_url": "http://127.0.0.1/"}

uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
