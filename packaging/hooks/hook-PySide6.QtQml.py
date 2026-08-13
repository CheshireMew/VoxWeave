from pathlib import PurePath

from PyInstaller.utils.hooks.qt import (
    add_qt6_dependencies,
    pyside6_library_info,
)

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()

ROOT_ONLY_MODULES = {
    "QtQml",
    "QtQuick",
    "QtQuick/Controls",
}
RECURSIVE_MODULES = {
    "Qt/labs/folderlistmodel",
    "QtCore",
    "QtMultimedia",
    "QtQml/Models",
    "QtQml/WorkerScript",
    "QtQuick/Controls/Basic",
    "QtQuick/Controls/impl",
    "QtQuick/Dialogs",
    "QtQuick/Effects",
    "QtQuick/Layouts",
    "QtQuick/Shapes",
    "QtQuick/Templates",
    "QtQuick/Window",
}


def _selected(entry: tuple[str, str]) -> bool:
    destination = PurePath(entry[1]).as_posix()
    marker = "PySide6/qml/"
    if marker not in destination:
        return False
    module = destination.split(marker, 1)[1]
    if module in ROOT_ONLY_MODULES:
        return True
    return any(
        module == allowed or module.startswith(f"{allowed}/")
        for allowed in RECURSIVE_MODULES
    )


binaries += [entry for entry in qml_binaries if _selected(entry)]
datas += [entry for entry in qml_datas if _selected(entry)]
