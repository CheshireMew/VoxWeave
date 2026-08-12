from __future__ import annotations

import ast
import re
from pathlib import Path

from voxweave.protocol import OPERATION_SPECS

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "voxweave"

DOMAIN_MODULES = [
    "batch.py",
    "controller.py",
    "model_registry.py",
    "realtime.py",
    "storage.py",
    "task_manager.py",
]

SQL_PREFIX = re.compile(
    r"^\s*(?:SELECT|INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|PRAGMA|"
    r"CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\b",
    re.IGNORECASE,
)


def test_domain_managers_do_not_own_sql_or_database_crud() -> None:
    violations = []
    for name in DOMAIN_MODULES:
        path = SOURCE / name
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        sql_literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and SQL_PREFIX.match(node.value)
        ]
        direct_crud = re.findall(
            r"\bdatabase\.(?:fetch_one|fetch_all|execute|executemany)\b", source
        )
        if sql_literals or direct_crud:
            violations.append((name, sql_literals, direct_crud))
    assert not violations


def test_qml_uses_feature_viewmodels_instead_of_bridge_feature_methods() -> None:
    bridge_source = (SOURCE / "gui.py").read_text(encoding="utf-8")
    bridge_tree = ast.parse(bridge_source)
    bridge = next(
        node
        for node in bridge_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Bridge"
    )
    methods = {
        node.name for node in bridge.body if isinstance(node, ast.FunctionDef)
    }
    old_feature_methods = {
        "analyze",
        "archiveArtifacts",
        "cancelTask",
        "convert",
        "createBatch",
        "exportDiagnostics",
        "importLocalModel",
        "inspectRuntime",
        "preview",
        "refreshBatches",
        "refreshModels",
        "refreshRealtimeStatus",
        "refreshTasks",
        "retryBatch",
        "scanModels",
        "startRealtime",
    }
    assert methods.isdisjoint(old_feature_methods)

    qml = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((SOURCE / "qml").glob("*.qml"))
    )
    for method in old_feature_methods:
        assert f"bridge.{method}" not in qml
    for boundary in [
        "bridge.activity",
        "bridge.batchRules",
        "bridge.maintenance",
        "bridge.media",
        "bridge.modelCatalog",
        "bridge.realtime",
        "bridge.taskList",
    ]:
        assert boundary in qml


def _class_methods(module: str, class_name: str) -> set[str]:
    tree = ast.parse((SOURCE / module).read_text(encoding="utf-8"))
    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name
        for node in target.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_repositories_do_not_reach_into_other_aggregate_tables() -> None:
    task_repository = (SOURCE / "task_repository.py").read_text(encoding="utf-8")
    batch_repository = (SOURCE / "batch_repository.py").read_text(encoding="utf-8")
    service = (SOURCE / "service.py").read_text(encoding="utf-8")
    controller = (SOURCE / "controller.py").read_text(encoding="utf-8")

    assert "batch_runs" not in task_repository
    assert not re.search(r"\b(?:FROM|JOIN)\s+models\b", batch_repository, re.IGNORECASE)
    assert "controller.tasks" not in service
    assert ".repository" not in controller


def test_controller_only_composes_services_and_delegates_operations() -> None:
    assert _class_methods("controller.py", "Controller") == {
        "__init__",
        "execute",
        "task_events",
        "all_task_events",
        "describe",
        "shutdown",
    }
    controller = (SOURCE / "controller.py").read_text(encoding="utf-8")
    assert "if operation" not in controller
    assert "OPERATION_SPECS" not in controller

    router = (SOURCE / "operation_router.py").read_text(encoding="utf-8")
    for operation in OPERATION_SPECS:
        assert router.count(f'"{operation}"') == 1


def test_model_and_media_facades_do_not_retain_removed_god_methods() -> None:
    registry_methods = _class_methods("model_registry.py", "ModelRegistry")
    assert registry_methods.isdisjoint(
        {
            "inspect",
            "scan",
            "import_model",
            "install_from_catalog",
            "_download",
        }
    )
    registry = (SOURCE / "model_registry.py").read_text(encoding="utf-8")
    assert "urllib" not in registry
    assert "subprocess" not in registry

    assert "convert" not in _class_methods("media_pipeline.py", "MediaPipeline")
    runtime_tree = ast.parse((SOURCE / "runtime.py").read_text(encoding="utf-8"))
    runtime_functions = {
        node.name for node in runtime_tree.body if isinstance(node, ast.FunctionDef)
    }
    assert "install_runtime" not in runtime_functions


def test_shared_infrastructure_is_the_only_definition_of_common_helpers() -> None:
    definitions: dict[str, list[str]] = {
        name: []
        for name in (
            "encode_cursor",
            "decode_cursor",
            "archive_failed_staging",
            "run_capture",
            "run_logged",
            "start_managed_process",
        )
    }
    for path in SOURCE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in definitions:
                definitions[node.name].append(path.name)
    assert definitions == {
        "encode_cursor": ["pagination.py"],
        "decode_cursor": ["pagination.py"],
        "archive_failed_staging": ["staging.py"],
        "run_capture": ["process_control.py"],
        "run_logged": ["process_control.py"],
        "start_managed_process": ["process_control.py"],
    }

    for module in (
        "client.py",
        "media_io.py",
        "model_inspector.py",
        "realtime.py",
        "runtime.py",
        "runtime_install.py",
        "rvc_engine.py",
    ):
        tree = ast.parse((SOURCE / module).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "Popen"
        ]
        assert calls == []

    for token in ("CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW", "creationflags"):
        owners = [
            path.name
            for path in SOURCE.glob("*.py")
            if token in path.read_text(encoding="utf-8")
        ]
        assert owners == ["process_control.py"]


def test_realtime_worker_delegates_audio_processing_and_stream_lifecycle() -> None:
    worker = (SOURCE / "rvc_realtime_worker.py").read_text(encoding="utf-8")
    assert "torch.zeros" not in worker
    assert "sd.Stream" not in worker
    assert "def audio_callback" not in worker
    assert "RealtimeAudioProcessor" in worker
    assert "run_audio_stream" in worker
