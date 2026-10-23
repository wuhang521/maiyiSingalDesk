"""Offline portable skill connection contract; never talks to a provider."""
import importlib.util
import json
import io
from types import SimpleNamespace
import zipfile
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[2] / "hermes-skill/video-production/scripts/video_project.py"
spec = importlib.util.spec_from_file_location("video_skill_client", path)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


@pytest.mark.parametrize("base", ["http://example.com", "https://u:p@example.com", "https://example.com?x=1", "file:///etc/passwd"])
def test_connection_rejects_unsafe_destinations(monkeypatch, base):
    monkeypatch.setenv("HERMES_VIDEO_API_BASE_URL", base)
    monkeypatch.setenv("HERMES_VIDEO_ACCESS_TOKEN", "fixture-token")
    with pytest.raises(ValueError):
        client.connection()


def test_local_connection_and_query_route(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_VIDEO_API_BASE_URL", "http://127.0.0.1:18865/")
    monkeypatch.setenv("HERMES_VIDEO_ACCESS_TOKEN", "fixture-token")
    assert client.connection()[0] == "http://127.0.0.1:18865"
    observed = []
    monkeypatch.setattr(client, "call", lambda *args: observed.append(args) or {"status": "pending"})
    assert client.main(["query", "--project", "project-1", "--job", "job-1"]) == 0
    assert observed == [("GET", "/project-1/jobs/job-1", None)]
    assert json.loads(capsys.readouterr().out)["status"] == "pending"


def test_skill_rejects_path_injection():
    with pytest.raises(ValueError):
        client.identifier("../someone-else/files")


def test_no_redirect_keeps_bearer_from_other_hosts():
    assert client.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example") is None


def test_list_uses_canonical_route_without_auth_redirect(monkeypatch, capsys):
    observed = []
    monkeypatch.setattr(client, "call", lambda *args: observed.append(args) or {"items": []})
    client.main(["list"])
    assert observed == [("GET", "", None)]


def test_export_uses_authenticated_package_and_never_overwrites(monkeypatch, tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("production-plan.json", "{}")
    seen = []
    def request(req, **kwargs):
        seen.append(req)
        return io.BytesIO(buffer.getvalue())
    monkeypatch.setattr(client, "connection", lambda: ("http://127.0.0.1", "offline-token"))
    monkeypatch.setattr(client, "build_opener", lambda *args: SimpleNamespace(open=request))
    output = tmp_path / "publication.zip"
    result = client.export_package("project-1", output)
    assert zipfile.is_zipfile(output)
    assert result["bytes"] == len(buffer.getvalue())
    assert seen[0].get_header("Authorization") == "Bearer offline-token"
    assert seen[0].full_url.endswith("/project-1/package")
    with pytest.raises(ValueError):
        client.export_package("project-1", output)
