"""Exercise the startup script's actual model-selection statements without services.

Only selection assignments are compiled: no DB startup, network, config-file read
or credential lookup is executed. All keys below are synthetic fixtures.
"""
import ast
from pathlib import Path


def select_model(configuration, inherited, *, forbid_inherited_load=False):
    script = Path(__file__).resolve().parents[1] / "scripts/run_video_local.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    names = {"model_keys", "inherited_model", "model_fields", "uses_hermes"}
    statements = []
    for node in main.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets):
            statements.append(node)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "uses_hermes":
            statements.append(node)
    assert len(statements) == 5, "Review startup model-selection changes"
    loads = []
    def load(path):
        assert not forbid_inherited_load, "Explicit local model must not read/decrypt an unused Hermes snapshot"
        loads.append(path)
        return inherited
    namespace = {"config_data": configuration, "directory": Path("offline-fixture"), "load_hermes_video_config": load}
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(script), "exec"), namespace)
    return namespace["model_fields"], namespace["uses_hermes"], loads


def test_hermes_primary_overrides_blank_starter_prompt_only_and_preserves_video_provider():
    starter = {"prompt_model": "", "prompt_api_key": "", "prompt_base_url": "https://ark.example.test/v3",
               "seedance_api_key": "synthetic-video-key", "seedance_model": "my-video-model", "seedance_base_url": "https://video.example.test",
               "RUNNINGHUB_API_KEY": "synthetic-rh-key", "TASK_VIDEO_PAID_ENABLED": "0"}
    inherited = {"prompt_model": "hermes-text-model", "prompt_api_key": "synthetic-hermes-key", "prompt_base_url": "https://text.example.test"}
    model, uses_hermes, loads = select_model(starter, inherited)
    assert uses_hermes and len(loads) == 1
    assert {key: model[key] for key in inherited} == inherited
    assert model["seedance_api_key"] == starter["seedance_api_key"]
    assert model["seedance_model"] == starter["seedance_model"]
    assert model["seedance_base_url"] == starter["seedance_base_url"]
    assert "RUNNINGHUB_API_KEY" not in model and "TASK_VIDEO_PAID_ENABLED" not in model
    assert starter["TASK_VIDEO_PAID_ENABLED"] == "0"


def test_explicit_local_primary_is_preserved_without_decrypting_a_stale_snapshot():
    manual = {"prompt_model": "local-text-model", "prompt_api_key": "synthetic-manual-key", "prompt_base_url": "https://manual.example.test",
              "seedance_api_key": "synthetic-video-key"}
    model, uses_hermes, loads = select_model(manual, {}, forbid_inherited_load=True)
    assert not uses_hermes and not loads
    assert model == manual


def test_absent_snapshot_keeps_an_unconfigured_primary_unconfigured():
    starter = {"prompt_model": "", "prompt_api_key": "", "prompt_base_url": "https://ark.example.test/v3"}
    model, uses_hermes, loads = select_model(starter, {})
    assert not uses_hermes and len(loads) == 1
    assert model == starter
