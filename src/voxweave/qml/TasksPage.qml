pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    required property var bridge
    required property var theme
    property var tasks: []
    property string stateFilter: "all"
    property bool showMaintenance: false
    readonly property var filteredTasks: root.tasks.filter(function(task) {
        if (!root.showMaintenance && task.is_maintenance) return false
        if (root.stateFilter === "active"
                && ["queued", "running"].indexOf(task.state) < 0) return false
        if (root.stateFilter === "failed"
                && ["failed", "cancelled", "interrupted"].indexOf(task.state) < 0) return false
        if (root.stateFilter === "completed" && task.state !== "completed") return false
        var query = searchField.text.trim().toLowerCase()
        return query.length === 0
            || String(task.localized_title || "").toLowerCase().includes(query)
            || String(task.error_summary || "").toLowerCase().includes(query)
            || String(task.result_path || "").toLowerCase().includes(query)
    })

    function taskTone(state) {
        if (state === "completed") return "success"
        if (state === "failed") return "danger"
        if (state === "cancelled" || state === "interrupted") return "warning"
        return "info"
    }

    function taskStateLabel(state) {
        if (state === "queued") return root.bridge.text(root.bridge.language, "task.state.queued")
        if (state === "running") return root.bridge.text(root.bridge.language, "task.state.running")
        if (state === "completed") return root.bridge.text(root.bridge.language, "task.state.completed")
        if (state === "failed") return root.bridge.text(root.bridge.language, "task.state.failed")
        if (state === "cancelled") return root.bridge.text(root.bridge.language, "task.state.cancelled")
        if (state === "interrupted") return root.bridge.text(root.bridge.language, "task.state.interrupted")
        return state
    }

    function connectionLabel(state) {
        return root.bridge.text(root.bridge.language, "task.connection." + state)
    }

    objectName: "tasksPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text(root.bridge.language, "nav.tasks")
            StatusPill { text: root.tasks.length + " " + root.bridge.text(root.bridge.language, "label.tasks"); tone: root.tasks.length > 0 ? "info" : "neutral" }
            AppIconButton {
                glyph: "\uE72C"
                accessibleName: root.bridge.text(root.bridge.language, "action.refresh")
                onClicked: root.bridge.taskList.refresh()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 56 : 0
            visible: root.bridge.taskList.error.length > 0
                || root.bridge.taskList.connectionState === "reconnecting"
            radius: root.theme.radiusSmall
            color: root.theme.warningWash
            border.color: root.theme.warning
            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                Label {
                    Layout.fillWidth: true
                    text: root.bridge.taskList.error.length > 0
                        ? root.bridge.taskList.error
                        : root.bridge.text(root.bridge.language, "task.connection.reconnecting_detail")
                    color: root.theme.text
                    font.family: root.theme.uiFont
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
                AppButton {
                    compact: true
                    text: root.bridge.text(root.bridge.language, "action.retry")
                    onClicked: root.bridge.taskList.refresh()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            StatusPill {
                text: root.connectionLabel(root.bridge.taskList.connectionState)
                tone: root.bridge.taskList.connectionState === "connected" ? "success"
                    : root.bridge.taskList.connectionState === "offline" ? "neutral" : "warning"
            }
            Label {
                Layout.fillWidth: true
                visible: root.bridge.taskList.connectionDetail.length > 0
                text: root.bridge.taskList.connectionDetail
                color: root.theme.textDim
                font.family: root.theme.uiFont
                font.pixelSize: 10
                elide: Text.ElideRight
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            AppTextField {
                id: searchField
                objectName: "taskSearchField"
                Layout.fillWidth: true
                placeholderText: root.bridge.text(root.bridge.language, "task.search")
                Accessible.name: root.bridge.text(root.bridge.language, "task.search")
            }
            Flow {
                Layout.fillWidth: true
                Layout.preferredHeight: childrenRect.height
                spacing: 6
                Repeater {
                    model: [
                        {"value": "all", "label": root.bridge.text(root.bridge.language, "task.filter.all")},
                        {"value": "active", "label": root.bridge.text(root.bridge.language, "task.filter.active")},
                        {"value": "failed", "label": root.bridge.text(root.bridge.language, "task.filter.failed")},
                        {"value": "completed", "label": root.bridge.text(root.bridge.language, "task.filter.completed")}
                    ]
                    delegate: AppButton {
                        required property var modelData
                        compact: true
                        kind: root.stateFilter === modelData.value ? "primary" : "quiet"
                        text: modelData.label
                        onClicked: root.stateFilter = modelData.value
                    }
                }
                AppCheckBox {
                    text: root.bridge.text(root.bridge.language, "task.show_maintenance")
                    checked: root.showMaintenance
                    onToggled: root.showMaintenance = checked
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: taskList
                objectName: "taskList"
                anchors.fill: parent
                model: root.filteredTasks
                clip: true
                spacing: 6
                footer: AppButton {
                    width: ListView.view ? ListView.view.width : implicitWidth
                    visible: root.bridge.taskList.hasMore
                    text: root.bridge.text(root.bridge.language, "action.load_more")
                    onClicked: root.bridge.taskList.loadMore()
                }

                delegate: Rectangle {
                    id: taskDelegate
                    required property var modelData
                    required property int index
                    property bool expanded: false
                    width: ListView.view.width
                    height: taskDelegate.expanded ? 280 : 112
                    radius: root.theme.radiusMedium
                    color: root.theme.surface
                    border.color: root.theme.border
                    border.width: 1

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 5

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Label {
                                text: (taskDelegate.index + 1 < 10 ? "0" : "")
                                    + (taskDelegate.index + 1)
                                color: root.theme.textDim
                                font.family: root.theme.monoFont
                                font.pixelSize: 10
                            }
                            Label {
                                Layout.fillWidth: true
                                text: taskDelegate.modelData.localized_title
                                color: root.theme.text
                                font.family: root.theme.uiFont
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                                elide: Text.ElideMiddle
                            }
                            StatusPill { text: root.taskStateLabel(taskDelegate.modelData.state); tone: root.taskTone(taskDelegate.modelData.state) }
                            AppButton {
                                visible: !["completed", "failed", "cancelled", "interrupted"].includes(taskDelegate.modelData.state)
                                compact: true
                                kind: "danger"
                                text: root.bridge.text(root.bridge.language, "action.cancel")
                                onClicked: root.bridge.taskList.cancel(taskDelegate.modelData.id)
                            }
                            AppButton {
                                visible: ["failed", "cancelled", "interrupted"].includes(taskDelegate.modelData.state)
                                compact: true
                                text: root.bridge.text(root.bridge.language, "action.retry")
                                onClicked: root.bridge.taskList.retry(taskDelegate.modelData.id)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 26
                            spacing: 8

                            Label {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                Layout.preferredWidth: 0
                                text: taskDelegate.modelData.error_summary.length > 0
                                    ? taskDelegate.modelData.error_summary
                                    : taskDelegate.modelData.result_path.length > 0
                                        ? taskDelegate.modelData.result_path
                                        : root.bridge.text(root.bridge.language, "label.stage") + ": " + taskDelegate.modelData.localized_stage
                                color: taskDelegate.modelData.error_summary.length > 0 ? root.theme.danger : root.theme.textMuted
                                font.family: taskDelegate.modelData.result_path.length > 0 ? root.theme.monoFont : root.theme.uiFont
                                font.pixelSize: 11
                                elide: Text.ElideMiddle
                            }
                            Label {
                                text: taskDelegate.modelData.localized_timestamp || ""
                                color: root.theme.textDim
                                font.family: root.theme.monoFont
                                font.pixelSize: 10
                            }
                        }

                        Flow {
                            Layout.fillWidth: true
                            Layout.leftMargin: 26
                            Layout.preferredHeight: visible ? childrenRect.height : 0
                            spacing: 6
                            visible: ["completed", "failed", "cancelled", "interrupted"]
                                .includes(taskDelegate.modelData.state)
                            AppButton {
                                visible: taskDelegate.modelData.result_path.length > 0
                                compact: true
                                text: root.bridge.text(root.bridge.language, "action.open_result")
                                onClicked: root.bridge.taskList.openResult(taskDelegate.modelData.result_path)
                            }
                            AppButton {
                                visible: taskDelegate.modelData.result_path.length > 0
                                compact: true
                                text: root.bridge.text(root.bridge.language, "action.open_folder")
                                onClicked: root.bridge.taskList.openResultFolder(taskDelegate.modelData.result_path)
                            }
                            AppButton {
                                visible: ["completed", "failed", "cancelled", "interrupted"]
                                    .includes(taskDelegate.modelData.state)
                                compact: true
                                text: taskDelegate.expanded
                                    ? root.bridge.text(root.bridge.language, "action.collapse")
                                    : root.bridge.text(root.bridge.language, "action.details")
                                onClicked: {
                                    if (!taskDelegate.expanded
                                            && !taskDelegate.modelData.detail_loaded)
                                        root.bridge.taskList.loadDetails(taskDelegate.modelData.id)
                                    taskDelegate.expanded = !taskDelegate.expanded
                                }
                            }
                        }

                        RowLayout {
                            visible: !["completed", "failed", "cancelled", "interrupted"].includes(taskDelegate.modelData.state)
                            Layout.fillWidth: true
                            Layout.leftMargin: 26
                            spacing: 8
                            AppProgressBar { Layout.fillWidth: true; value: taskDelegate.modelData.progress }
                            Label {
                                Layout.preferredWidth: 34
                                text: Math.round(Number(taskDelegate.modelData.progress) * 100) + "%"
                                color: root.theme.textDim
                                font.family: root.theme.monoFont
                                font.pixelSize: 9
                                horizontalAlignment: Text.AlignRight
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            visible: taskDelegate.expanded
                            radius: root.theme.radiusSmall
                            color: root.theme.field
                            Label {
                                anchors.left: parent.left
                                anchors.right: copyErrorButton.left
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                anchors.margins: 8
                                text: taskDelegate.modelData.detail_loaded
                                    ? taskDelegate.modelData.details_text
                                    : String(taskDelegate.modelData.error || taskDelegate.modelData.error_summary
                                        || root.bridge.text(root.bridge.language, "task.details.loading"))
                                color: taskDelegate.modelData.error_summary.length > 0
                                    ? root.theme.danger : root.theme.text
                                font.family: root.theme.monoFont
                                font.pixelSize: 11
                                wrapMode: Text.WrapAnywhere
                                elide: Text.ElideRight
                            }
                            AppButton {
                                id: copyErrorButton
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 6
                                compact: true
                                text: root.bridge.text(root.bridge.language, "action.copy")
                                onClicked: root.bridge.taskList.copyText(
                                    taskDelegate.modelData.detail_loaded
                                        ? taskDelegate.modelData.details_text
                                        : String(taskDelegate.modelData.error || taskDelegate.modelData.error_summary || "")
                                )
                            }
                        }
                    }
                }
            }

            EmptyState {
                anchors.centerIn: parent
                visible: !root.bridge.taskList.loading && root.filteredTasks.length === 0
                title: root.tasks.length === 0
                    ? root.bridge.text(root.bridge.language, "empty.tasks.title")
                    : root.bridge.text(root.bridge.language, "task.filtered_empty.title")
                detail: root.tasks.length === 0
                    ? root.bridge.text(root.bridge.language, "empty.tasks.detail")
                    : root.bridge.text(root.bridge.language, "task.filtered_empty.detail")
            }
            EmptyState {
                anchors.centerIn: parent
                visible: root.bridge.taskList.loading && root.tasks.length === 0
                title: root.bridge.text(root.bridge.language, "task.loading.title")
                detail: root.bridge.text(root.bridge.language, "task.loading.detail")
            }
        }
    }
}
