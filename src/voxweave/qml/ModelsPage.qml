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
    property bool showAdvancedImport: false
    property bool showArchived: false
    signal navigateRequested(int index)
    readonly property var filteredModels: root.models.filter(function(model) {
        if (!root.showArchived && model.archived) return false
        var query = modelSearch.text.trim().toLowerCase()
        return query.length === 0
            || String(model.localized_name || "").toLowerCase().includes(query)
            || String(model.id || "").toLowerCase().includes(query)
    })

    function statusText(status) {
        var key = "model.status." + status
        var value = root.bridge.text(key)
        return value === key ? status : value
    }

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
    readonly property var selectedModel: root.filteredModels.length > 0 && libraryModelSelector.currentIndex >= 0
        ? root.filteredModels[libraryModelSelector.currentIndex]
        : null
    AppScrollView {
        id: modelsScroll
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
        width: modelsScroll.availableWidth
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
                glyph: "\uE8B7"
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

            AppTextField {
                id: modelSearch
                objectName: "modelSearchField"
                Layout.fillWidth: true
                placeholderText: root.bridge.text("models.search")
                Accessible.name: root.bridge.text("models.search")
            }

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
                        model: root.filteredModels
                        textRole: "localized_name"
                        valueRole: "id"
                        emptyText: root.bridge.activity.busyKeys.includes("model-scan")
                            ? root.bridge.text("models.discovering_local")
                            : root.bridge.text("empty.models.title")
                        enabled: root.filteredModels.length > 0
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
                    ? (root.selectedModel.archived
                        ? root.bridge.text("model.status.archived")
                        : root.statusText(root.selectedModel.status))
                        + "  ·  " + (root.selectedModel.rvc_version || "-")
                        + "  ·  " + (root.selectedModel.sample_rate || "-") + " Hz"
                        + "  ·  " + (root.selectedModel.license_spdx || root.bridge.text("models.license_unknown"))
                    : ""
                color: root.theme.textMuted
                font.family: root.theme.uiFont
                font.pixelSize: 12
            }
            AppButton {
                visible: root.selectedModel !== null && root.selectedModel.status === "ready"
                text: root.bridge.text("models.try_in_conversion")
                enabled: root.selectedModel !== null && !root.selectedModel.archived
                onClicked: root.navigateRequested(1)
            }
            RowLayout {
                Layout.fillWidth: true
                AppCheckBox {
                    text: root.bridge.text("models.show_archived")
                    checked: root.showArchived
                    onToggled: root.showArchived = checked
                }
                Item { Layout.fillWidth: true }
                AppButton {
                    visible: root.selectedModel !== null
                    enabled: root.selectedModel !== null
                    text: root.selectedModel && root.selectedModel.archived
                        ? root.bridge.text("action.restore")
                        : root.bridge.text("models.archive")
                    onClicked: root.bridge.modelCatalog.setArchived(
                        root.selectedModel.id, !root.selectedModel.archived
                    )
                }
            }
        }

        SectionHeader {
            Layout.fillWidth: true
            title: root.bridge.text("models.recommended")
            badgeText: root.bridge.text("models.verified_badge")
            badgeTone: "info"
        }

        GridLayout {
            id: catalogList
            objectName: "recommendedModelList"
            Layout.fillWidth: true
            columns: width >= 700 ? 2 : 1
            columnSpacing: 8
            rowSpacing: 8

            Repeater {
                model: root.bridge.modelCatalog.catalogItems

                delegate: AppPanel {
                    id: catalogItem
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 50
                    padding: 8
                    contentSpacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Label {
                            Layout.fillWidth: true
                            text: catalogItem.modelData.localized_name
                            color: root.theme.text
                            font.family: root.theme.uiFont
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }

                        Label {
                            text: catalogItem.modelData.download_megabytes + " MiB"
                            color: root.theme.textDim
                            font.family: root.theme.monoFont
                            font.pixelSize: 10
                        }

                        AppButton {
                            objectName: "downloadRecommendedModelButton"
                            Layout.preferredWidth: 96
                            compact: true
                            visible: !catalogItem.modelData.downloading
                            kind: catalogItem.modelData.installed ? "quiet" : "primary"
                            text: catalogItem.modelData.installed
                                ? root.bridge.text("models.installed")
                                : root.bridge.text("models.download")
                            enabled: !catalogItem.modelData.installed
                                && root.bridge.maintenance.runtimeReady
                            onClicked: root.bridge.modelCatalog.installCatalogModel(catalogItem.modelData.id)
                        }

                        Item {
                            objectName: "recommendedModelDownloadProgress"
                            Layout.preferredWidth: 96
                            Layout.preferredHeight: 34
                            visible: catalogItem.modelData.downloading

                            Rectangle {
                                anchors.fill: parent
                                radius: root.theme.radiusSmall
                                color: root.theme.field
                                border.color: root.theme.border
                                border.width: 1

                                Rectangle {
                                    x: 1
                                    y: 1
                                    width: Math.max(0, (parent.width - 2) * catalogItem.modelData.download_progress)
                                    height: parent.height - 2
                                    radius: root.theme.radiusSmall
                                    color: root.theme.accent

                                    Behavior on width {
                                        NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
                                    }
                                }

                                Label {
                                    anchors.centerIn: parent
                                    text: Math.round(catalogItem.modelData.download_progress * 100) + "%"
                                    color: catalogItem.modelData.download_progress >= 0.55
                                        ? root.theme.accentInk : root.theme.text
                                    font.family: root.theme.monoFont
                                    font.pixelSize: 11
                                    font.weight: Font.DemiBold
                                }
                            }
                        }
                    }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: !root.bridge.maintenance.runtimeReady
            text: root.bridge.text("models.runtime_required")
            color: root.theme.warning
            font.family: root.theme.uiFont
            font.pixelSize: 11
            wrapMode: Text.Wrap
        }

        SectionHeader {
            Layout.fillWidth: true
            title: root.bridge.text("models.add")
        }

        AppCheckBox {
            text: root.bridge.text("models.show_advanced")
            checked: root.showAdvancedImport
            onToggled: root.showAdvancedImport = checked
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
                    visible: root.showAdvancedImport
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
                    AppTextField { id: urlSource; visible: root.showAdvancedImport; Layout.fillWidth: true; placeholderText: root.bridge.text("placeholder.source_optional") }
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
}
