from pathlib import Path, PurePath


project_root = Path(SPECPATH).resolve().parent
source_root = project_root / "src"
package_root = source_root / "voxweave"

worker_sources = [
    "analysis_worker.py",
    "media_postprocess.py",
    "media_postprocess_worker.py",
    "model_inspect_worker.py",
    "runtime_assets_worker.py",
    "runtime_worker.py",
    "rvc_realtime_audio.py",
    "rvc_realtime_worker.py",
    "rvc_worker.py",
    "release_smoke.py",
    "separation_worker.py",
]

datas = [
    (str(package_root / "qml"), "voxweave/qml"),
    (str(package_root / "resources"), "voxweave/resources"),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "LICENSE-NOTICE.md"), "."),
    (str(project_root / "LICENSING.md"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
]
datas.extend((str(package_root / name), "voxweave") for name in worker_sources)

analysis = Analysis(
    [str(package_root / "app.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.lifespan.off",
        "uvicorn.lifespan.on",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl",
    ],
    hookspath=[str(project_root / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "_pytest",
        "httpx",
        "jsonschema",
        "packaging",
        "pip",
        "pytest",
        "ruff",
        "setuptools",
        "wheel",
    ],
    noarchive=False,
    optimize=1,
)

unused_qt_binaries = {
    "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6Quick3DUtils.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
}
analysis.binaries = [
    entry
    for entry in analysis.binaries
    if PurePath(entry[0]).as_posix() not in unused_qt_binaries
    and not PurePath(entry[0]).as_posix().startswith("PySide6/plugins/qmltooling/")
    and not PurePath(entry[0]).as_posix().startswith(
        "PySide6/plugins/platforminputcontexts/"
    )
]
supported_qt_translations = ("_en.qm", "_ja.qm", "_zh_CN.qm")
analysis.datas = [
    entry
    for entry in analysis.datas
    if not PurePath(entry[0]).as_posix().startswith("PySide6/translations/")
    or PurePath(entry[0]).name.endswith(supported_qt_translations)
]
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VoxWeave",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(project_root / "assets" / "app" / "VoxWeave.ico"),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoxWeave",
)
