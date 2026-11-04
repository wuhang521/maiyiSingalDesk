"""Real local trial backend, isolated from production and all cloud credentials.

Not a provider simulator or deployment tool. Cloud features stay unavailable.
The caller creates a dedicated directory and passes a random local password via
stdin; existing trial accounts/passwords are never reset on restart.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import threading


def main():
    root = Path(__file__).resolve().parents[1]
    directory = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2])
    approved = root.parent / "artifacts" / "knowledge-local-trial"
    if directory != approved.resolve() or directory.is_symlink() or not 1024 <= port <= 65535:
        raise RuntimeError("Requires the dedicated local trial directory and an unprivileged port")
    request = json.loads(sys.stdin.readline(4096))
    password = request["password"]
    if not isinstance(password, str) or not 16 <= len(password) <= 128:
        raise RuntimeError("Invalid local password")
    database = directory / "trial.db"
    marker = directory / "database-owner.json"
    if database.is_symlink() or marker.is_symlink():
        raise RuntimeError("Refusing symlinked trial data")
    if database.exists():
        if not marker.is_file() or json.loads(marker.read_text("utf-8")) != {"kind": "knowledge-local-trial", "schema": "20261008_12"}:
            raise RuntimeError("Refusing an unknown or incomplete database; no data was reset")
    elif marker.exists():
        raise RuntimeError("Trial database is missing; no account was silently recreated")

    # Never load repository .env or inherit provider/service credentials.
    for key in list(os.environ):
        if key.startswith(("TASK_", "ARK_", "TTS_", "SEEDANCE_")):
            del os.environ[key]
    sys.path.insert(0, str(root))
    from app import config
    settings = config.Settings(_env_file=None,
        database_url=f"sqlite:///{database.as_posix()}",
        app_name="SignalDesk · 本地新版体验（云服务关闭）",
        api_key=secrets.token_urlsafe(40),
        knowledge_storage_dir=str(directory / "files"),
        cors_origins=f"http://127.0.0.1:{request['frontend_port']}",
        knowledge_paid_daily_limit=0, knowledge_paid_monthly_limit=0,
        knowledge_trash_retention_days=0)
    config.get_settings = lambda: settings
    # The real extraction subprocess also receives explicit safe settings.
    for name, value in settings.model_dump().items():
        os.environ["TASK_" + name.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from app.main import app
    from app.auth import hash_password
    from app.database import Base, SessionLocal, engine
    from app.models import Staff, UserAccount
    from app.knowledge_worker import run_once
    from app.knowledge_notifications import start_heartbeat
    import uvicorn

    fresh = not database.exists()
    # A fresh trial schema, not a production migration or migration-test result.
    Base.metadata.create_all(engine)
    if fresh:
        with SessionLocal() as db:
            db.add_all([
                UserAccount(username="local-admin", staff=Staff(name="本地体验管理员"),
                    role="admin", password_hash=hash_password(password)),
                UserAccount(username="local-member", staff=Staff(name="本地体验成员"),
                    role="member", password_hash=hash_password(password)),
            ])
            db.commit()
        marker.write_text(json.dumps({"kind": "knowledge-local-trial", "schema": "20261008_12"}), encoding="utf-8")

    @app.get("/local-trial/status")
    def local_status():
        return dict(mode="real-local-trial", cloud_enabled=False, production_connected=False,
                    provider_simulation=False, schema="20261008_12")

    @app.get("/local-trial/release")
    def local_release():
        return dict(tag_name="v0.5.3", assets=[], html_url=f"http://127.0.0.1:{port}/docs")

    stopped = threading.Event()
    heartbeat_stop, activity = start_heartbeat("extract")
    def work():
        while not stopped.is_set():
            try:
                activity["state"] = "busy"
                worked = run_once()
                activity["state"] = "idle"
                stopped.wait(.5 if worked else 2)
            except Exception:
                activity["state"] = "error"
                print("local trial worker: processing failed; original files retained", file=sys.stderr, flush=True)
                stopped.wait(5)
    thread = threading.Thread(target=work, daemon=True, name="local-knowledge-worker")
    thread.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    finally:
        stopped.set()
        heartbeat_stop.set()


if __name__ == "__main__":
    main()
