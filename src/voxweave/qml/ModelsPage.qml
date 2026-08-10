pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: root
    required property var bridge
    required property var theme
    property var models: []

FolderDialog {
    id: weightRootDialog
    onAccepted: root.bridge.modelCatalog.scanWeightRoot(selectedFolder)
}
FolderDialog {
    id: indexRootDialog
    onAccepted: root.bridge.modelCatalog.scanIndexRoot(selectedFolder)
}
FileDialog {
    id: localModelDialog
    nameFilters: ["RVC model (*.pth)"]
    onAccepted: localModelPath.text = selectedFile
}
FileDialog {
    id: localIndexDialog
    nameFilters: ["RVC index (*.index)"]
    onAccepted: localIndexPath.text = selectedFile
}

    objectName: "modelsPage"
    property int importTab: 0
    readonly property var selectedModel: root.models.length > 0 && libraryModelSelector.currentIndex >= 0
        ? root.models[libraryModelSelector.currentIndex]
        : null
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text("nav.models")
            AppIconButton {
                objectName: "scanModelsButton"
                glyph: "\uE721"
                accessibleName: root.bridge.text("action.scan")
                enabled: !root.bridge.activity.busyKeys.includes("model-scan")
                onClicked: root.bridge.modelCatalog.scan()
            }
            AppIconButton {
                objectName: "addModelFolderButton"
                glyph: "\uE8F4"
                accessibleName: root.bridge.text("action.scan_weights")
                enabled: !root.bridge.activity.busyKeys.includes("model-scan")
                onClicked: weightRootDialog.open()
            }
            AppIconButton {
                objectName: "addIndexFolderButton"
                glyph: "\uE8F4"
                accessibleName: root.bridge.text("action.scan_indices")
                enabled: !root.bridge.activity.busyKeys.includes("model-scan")
                onClicked: indexRootDialog.open()
            }
            AppIconButton {
                objectName: "refreshModelsButton"
                glyph: "\uE72C"
                accessibleName: root.bridge.text("action.refresh")
                kind: "quiet"
                onClicked: root.bridge.modelCatalog.refresh()
            }
        }

        AppPanel {
            Layout.fillWidth: true

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    FieldLabel { text: root.bridge.text("section.available_models") }
                    AppComboBox {
                        id: libraryModelSelector
                        objectName: "libraryModelSelector"
                        Layout.fillWidth: true
                        model: root.models
                        textRole: "localized_name"
                        valueRole: "id"
                        emptyText: root.bridge.text("empty.models.title")
                        enabled: root.models.length > 0
                    }
                }

                StatusPill {
                    Layout.alignment: Qt.AlignBottom
                    Layout.bottomMargin: 9
                    text: root.models.length + " " + root.bridge.text("label.models")
                    tone: root.models.length > 0 ? "info" : "neutral"
                }
            }

            Label {
                Layout.fillWidth: true
                visible: root.selectedModel !== null
                text: root.selectedModel
                    ? root.selectedModel.status
                        + "  ·  " + (root.selectedModel.rvc_version || "-")
                        + "  ·  " + (root.selectedModel.sample_rate || "-") + " Hz"
                        + "  ·  " + (root.selectedModel.license_spdx || root.bridge.text("models.license_unknown"))
                    : ""
                color: root.theme.textMuted
                font.family: root.theme.uiFont
                font.pixelSize: 12
            }
        }

        SectionHeader {
            Layout.fillWidth: true
            title: root.bridge.text("models.add")
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            color: root.theme.field
            radius: root.theme.radiusSmall
            border.color: root.theme.border
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.margins: 1
                spacing: 2

                AppTabButton {
                    objectName: "computerModelTab"
                    Layout.fillHeight: true
                    text: root.bridge.text("models.from_computer")
                    selected: root.importTab === 0
                    onClicked: root.importTab = 0
                }
                AppTabButton {
                    objectName: "linkModelTab"
                    Layout.fillHeight: true
                    text: root.bridge.text("models.from_link")
                    selected: root.importTab === 1
                    onClicked: root.importTab = 1
                }
                Item { Layout.fillWidth: true }
            }
        }

        StackLayout {
            id: modelImportStack
            objectName: "modelImportStack"
            Layout.fillWidth: true
            currentIndex: root.importTab

            AppPanel {
                Layout.fillWidth: true
                FieldLabel { text: root.bridge.text("label.weight_file") }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    AppTextField { id: localModelPath; Layout.fillWidth: true; placeholderText: "model.pth" }
                    AppButton { compact: true; text: root.bridge.text("action.choose"); onClicked: localModelDialog.open() }
                }
                FieldLabel { text: root.bridge.text("label.index_file") }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    AppTextField { id: localIndexPath; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.optional_index") }
                    AppButton { compact: true; text: root.bridge.text("action.choose"); onClicked: localIndexDialog.open() }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 6
                    rowSpacing: 6
                    AppTextField { id: localModelId; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.model_id_optional") }
                    AppTextField { id: localDisplayName; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.display_name") }
                    AppTextField { id: localLicense; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.license_optional") }
                    AppTextField { id: localSource; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.source_optional") }
                }
                AppButton {
                    Layout.alignment: Qt.AlignRight
                    text: root.bridge.activity.busyKeys.includes("model-import")
                        ? root.bridge.text("task.state.running") : root.bridge.text("action.import")
                    kind: "primary"
                    enabled: localModelPath.text.length > 0
                        && !root.bridge.activity.busyKeys.includes("model-import")
                    onClicked: root.bridge.modelCatalog.importLocal(localModelPath.text, localIndexPath.text, localModelId.text, localDisplayName.text, localLicense.text, localSource.text)
                }
            }

            AppPanel {
                Layout.fillWidth: true
                FieldLabel { text: root.bridge.text("models.download_link") }
                AppTextField { id: urlModel; Layout.fillWidth: true; placeholderText: "https://…/model.pth" }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    AppTextField { id: urlSource; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.source_optional") }
                    AppTextField { id: urlSize; Layout.preferredWidth: 132; placeholderText: root.bridge.text("placeholder.bytes"); validator: IntValidator { bottom: 1 } }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 6
                    rowSpacing: 6
                    AppTextField { id: urlModelId; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.model_id") }
                    AppTextField { id: urlDisplayName; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.display_name") }
                    AppTextField { id: urlLicense; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.license") }
                    AppTextField { id: urlSha; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.sha256") }
                }
                AppButton {
                    Layout.alignment: Qt.AlignRight
                    text: root.bridge.activity.busyKeys.includes("model-import")
                        ? root.bridge.text("task.state.running") : root.bridge.text("action.import")
                    kind: "primary"
                    enabled: urlModel.text.length > 0 && urlModelId.text.length > 0
                        && urlDisplayName.text.length > 0 && urlLicense.text.length > 0
                        && urlSha.text.length === 64 && urlSize.text.length > 0
                        && !root.bridge.activity.busyKeys.includes("model-import")
                    onClicked: root.bridge.modelCatalog.importUrl(urlModel.text, urlModelId.text, urlDisplayName.text, urlLicense.text, urlSha.text, parseInt(urlSize.text), urlSource.text)
                }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
