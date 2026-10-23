"""Electron-owned video sidecar. Bootstrap secrets only arrive through stdin."""
import asyncio
import json
import os
import socket
import sys
import threading
from dataclasses import replace
from pathlib import Path


def bundled_environment(root: Path) -> dict[str, str]:
    """Resolve only trusted installation-relative tools, never saved machine paths."""
    manifest_path = root / "runtime.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest.get("schema") != 2:
        return {}  # Compatibility with the earlier local schema-1 resource patch.
    if (manifest.get("platform"), manifest.get("arch")) != ("win32", "x64"):
        raise ValueError("Unsupported bundled video platform")
    root = root.resolve()
    def tool(name):
        value = manifest.get(name)
        if not isinstance(value, str) or not value or any(char in value for char in ("\\", ":", "\x00")):
            raise ValueError("Invalid bundled tool path")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid bundled tool path")
        target = root
        for part in parts:
            target = target / part
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                raise ValueError("Linked bundled tool path")
        if not target.is_file() or not target.resolve().is_relative_to(root):
            raise ValueError("Missing bundled tool")
        return str(target.resolve())
    python, ffmpeg = tool("python"), tool("ffmpeg")
    if Path(python) != Path(sys.executable).resolve():
        raise ValueError("Use the bundled video interpreter")
    return {"TASK_VIDEO_PYTHON": python, "TASK_VIDEO_FFMPEG": ffmpeg}


def bundled_cache_directory(config_dir: Path, identity_hash: str) -> Path:
    # Keep JIT filenames outside accounts/<64-char identity>; they are long too.
    # Cached code contains no provider/user data. A 96-bit prefix isolates accounts.
    return config_dir.parent / "cache/numba" / identity_hash[:24]


def main():
    # Embeddable Python ignores PYTHONIOENCODING; the desktop pipe is UTF-8.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    server_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(server_root))
    from app.video_desktop import activate, configure, create_app
    envelope = json.loads(sys.stdin.readline(262145))
    configuration = configure(envelope)
    tools = bundled_environment(server_root.parent)
    if tools:
        configuration = replace(configuration, environment={**configuration.environment, **tools,
            "IMAGEIO_FFMPEG_EXE": tools["TASK_VIDEO_FFMPEG"],
            "NUMBA_CACHE_DIR": str(bundled_cache_directory(configuration.config_dir, configuration.identity_hash)),
            "PATH": str(Path(tools["TASK_VIDEO_FFMPEG"]).parent) + os.pathsep + os.environ.get("PATH", "")})
    activate(configuration)
    app = create_app(configuration)
    import uvicorn
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    parent_closed = threading.Event()
    def watch_parent():
        for _line in sys.stdin:
            pass
        parent_closed.set()
        server.should_exit = True
    threading.Thread(target=watch_parent, daemon=True).start()
    async def serve():
        task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started and not task.done():
            if parent_closed.is_set():
                server.should_exit = True
            await asyncio.sleep(0.01)
        if server.started and not parent_closed.is_set():
            print(json.dumps({"event": "ready", "port": port, "instance_id": configuration.instance_id}), flush=True)
        await task
    try:
        asyncio.run(serve())
    finally:
        listener.close()
        app.state.engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Bootstrap and provider exceptions may include paths or secret input.
        print("Desktop video service could not start; check the private local configuration.", file=sys.stderr, flush=True)
        raise SystemExit(1)
