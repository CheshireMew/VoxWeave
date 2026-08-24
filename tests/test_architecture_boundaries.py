from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import MISSING
from pathlib import Path

from voxweave.protocol import OPERATION_SPECS, OperationSpec

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


def _class_shape(module: str, class_name: str) -> tuple[int, int, int]:
    path = SOURCE / module
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    methods = [
        node
        for node in target.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    attributes = {
        node.attr
        for node in ast.walk(target)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    return len(path.read_text(encoding="utf-8").splitlines()), len(methods), len(attributes)


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


def test_realtime_responsibilities_stay_split_across_focused_services() -> None:
    lines, methods, attributes = _class_shape("realtime.py", "RealtimeSessionManager")
    assert lines <= 220
    assert methods <= 16
    assert attributes <= 10
    limits = {
        ("realtime_request.py", "RealtimeRequestBuilder"): (70, 4, 2),
        ("realtime_session_state.py", "RealtimeSessionState"): (320, 17, 8),
        ("realtime_worker_controller.py", "RealtimeWorkerController"): (420, 20, 18),
        ("realtime_worker_transport.py", "RealtimeWorkerTransport"): (200, 9, 9),
    }
    for (module, class_name), maximums in limits.items():
        shape = _class_shape(module, class_name)
        assert all(value <= maximum for value, maximum in zip(shape, maximums, strict=True))
    manager = (SOURCE / "realtime.py").read_text(encoding="utf-8")
    assert "RealtimeRequestBuilder" in manager
    assert "RealtimeSessionState" in manager
    assert "RealtimeWorkerController" in manager

    controller = (SOURCE / "realtime_worker_controller.py").read_text(encoding="utf-8")
    transport = (SOURCE / "realtime_worker_transport.py").read_text(encoding="utf-8")
    for token in ("subprocess", "start_managed_process", "json.loads", "process.stdin"):
        assert token not in manager
        assert token not in controller
        assert token in transport
    assert "repository" not in controller
    assert "repository" not in transport


def test_batch_manager_is_only_a_small_composition_boundary() -> None:
    lines, methods, attributes = _class_shape("batch.py", "BatchManager")
    assert lines <= 90
    assert methods <= 13
    assert attributes <= 4
    limits = {
        ("batch_rules.py", "BatchRuleService"): (120, 8, 2),
        ("batch_submission.py", "BatchSubmissionService"): (210, 5, 4),
        ("batch_run.py", "BatchRunCoordinator"): (165, 8, 4),
        ("batch_watch.py", "BatchWatchSupervisor"): (225, 11, 9),
        ("batch_directory_watcher.py", "WindowsDirectoryWatcher"): (165, 7, 7),
    }
    for (module, class_name), maximums in limits.items():
        shape = _class_shape(module, class_name)
        assert all(value <= maximum for value, maximum in zip(shape, maximums, strict=True))
    facade = (SOURCE / "batch.py").read_text(encoding="utf-8")
    for service in (
        "BatchRuleService",
        "BatchSubmissionService",
        "BatchRunCoordinator",
        "BatchWatchSupervisor",
    ):
        assert service in facade
    for token in ("threading", "os.scandir", "sha256_file", "WindowsDirectoryWatcher"):
        assert token not in facade

    assert "os.scandir" in (SOURCE / "batch_submission.py").read_text(encoding="utf-8")
    assert "WindowsDirectoryWatcher" in (
        SOURCE / "batch_watch.py"
    ).read_text(encoding="utf-8")
    assert "ReadDirectoryChangesW" in (
        SOURCE / "batch_directory_watcher.py"
    ).read_text(encoding="utf-8")


def test_settings_value_file_store_and_concurrency_service_remain_separate() -> None:
    settings_methods = _class_methods("config.py", "Settings")
    assert settings_methods.isdisjoint(
        {"update", "persist_normalized", "_adopt", "load", "commit"}
    )
    config = (SOURCE / "config.py").read_text(encoding="utf-8")
    file_store = (SOURCE / "settings_file_store.py").read_text(encoding="utf-8")
    service = (SOURCE / "settings_service.py").read_text(encoding="utf-8")
    assert "active.replace_with" in file_store
    assert "InterprocessFileLock" in file_store
    assert "threading.RLock" in service
    assert "def replace_with" in config
    assert "._adopt" not in "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.glob("*.py")
    )


def test_download_and_runtime_metadata_have_single_authoritative_owners() -> None:
    importer = (SOURCE / "model_importer.py").read_text(encoding="utf-8")
    installer = (SOURCE / "runtime_install.py").read_text(encoding="utf-8")
    downloader = (SOURCE / "verified_download.py").read_text(encoding="utf-8")
    assert "urllib.request.urlopen" not in importer + installer
    assert "download_verified(" in importer + installer
    assert "urllib.request.urlopen" in downloader
    assert "Content-Length" in downloader
    assert 'f"VoxWeave/{__version__}"' in downloader

    python_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.glob("*.py")
    )
    for metadata in (
        "torch==2.7.1",
        "torchaudio==2.7.1",
        "pymss==2.0.14",
        "mirrors.pku.edu.cn",
        "mirrors.nju.edu.cn",
        "rmvpe.pt",
        "wespeaker-resnet34-lm",
    ):
        assert metadata not in python_sources
    contract = (
        SOURCE / "resources" / "runtime_components.json"
    ).read_text(encoding="utf-8")
    assert all(metadata in contract for metadata in ("torch==2.7.1", "rmvpe.pt"))


def test_realtime_parameter_contract_is_the_only_definition_of_ui_constraints() -> None:
    definitions = {
        path.name: path.read_text(encoding="utf-8").count("ParameterSpec(")
        for path in SOURCE.glob("*.py")
    }
    assert {name: count for name, count in definitions.items() if count} == {
        "parameter_contracts.py": 9
    }
    engine = (SOURCE / "rvc_engine.py").read_text(encoding="utf-8")
    page = (SOURCE / "qml" / "RealtimePage.qml").read_text(encoding="utf-8")
    assert "self._parameters" not in engine
    assert "parameterSpecs" in page
    for helper in ("stateText", "stateTone", "workerStateText", "workerTone", "meterValue"):
        assert f"function {helper}" not in page


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


def test_public_operations_cannot_fall_back_to_an_untyped_result_contract() -> None:
    result_field = OperationSpec.__dataclass_fields__["result"]
    assert result_field.default is MISSING
    assert result_field.default_factory is MISSING
    assert all(spec.result is not None for spec in OPERATION_SPECS.values())


def test_qml_facing_slots_use_parameter_objects_instead_of_long_positional_lists() -> None:
    violations = []
    for path in SOURCE.glob("gui*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "Slot"
                    and len(decorator.args) > 3
                ):
                    violations.append((path.name, node.name, len(decorator.args)))
    assert violations == []


def test_shared_public_constants_have_one_definition_and_archives_keep_history() -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in SOURCE.glob("*.py")
    }
    for definition in (
        "AUDIO_EXTENSIONS =",
        "VIDEO_EXTENSIONS =",
        "STARTER_MODEL_IDS =",
        "CONTROL_PROTOCOL =",
        "CONTROL_PROTOCOL_VERSION =",
    ):
        owners = [name for name, source in sources.items() if definition in source]
        assert owners == ["capabilities.py"]
    storage_sources = sources["storage.py"] + sources["storage_repository.py"]
    assert "rewrite_task_references" not in storage_sources
    assert "UPDATE tasks SET arguments_json" not in storage_sources


def test_service_composition_import_does_not_eagerly_load_scipy() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import voxweave.controller; "
            "assert 'scipy' not in sys.modules; assert 'numpy' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
