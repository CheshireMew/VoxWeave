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
        if (state === "queued") return root.bridge.text("task.state.queued")
        if (state === "running") return root.bridge.text("task.state.running")
        if (state === "completed") return root.bridge.text("task.state.completed")
        if (state === "failed") return root.bridge.text("task.state.failed")
        if (state === "cancelled") return root.bridge.text("task.state.cancelled")
        if (state === "interrupted") return root.bridge.text("task.state.interrupted")
        return state
    }

    objectName: "tasksPage"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.theme.pageMargin
        spacing: 12

        PageHeader {
            Layout.fillWidth: true
            title: root.bridge.text("nav.tasks")
            StatusPill { text: root.tasks.length + " " + root.bridge.text("label.tasks"); tone: root.tasks.length > 0 ? "info" : "neutral" }
            AppIconButton {
                glyph: "\uE72C"
                accessibleName: root.bridge.text("action.refresh")
                onClicked: root.bridge.taskList.refresh()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            AppTextField {
                id: searchField
                objectName: "taskSearchField"
                Layout.fillWidth: true
                placeholderText: root.bridge.text("task.search")
                Accessible.name: root.bridge.text("task.search")
            }
            Flow {
                Layout.fillWidth: true
                Layout.preferredHeight: childrenRect.height
                spacing: 6
                Repeater {
                    model: [
                        {"value": "all", "label": root.bridge.text("task.filter.all")},
                        {"value": "active", "label": root.bridge.text("task.filter.active")},
                        {"value": "failed", "label": root.bridge.text("task.filter.failed")},
                        {"value": "completed", "label": root.bridge.text("task.filter.completed")}
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
                    text: root.bridge.text("task.show_maintenance")
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
                    text: root.bridge.text("action.load_more")
                    onClicked: root.bridge.taskList.loadMore()
                }

                delegate: Rectangle {
                    id: taskDelegate
                    required property var modelData
                    required property int index
                    property bool expanded: false
                    width: ListView.view.width
                    height: taskDelegate.expanded ? 176
                        : taskDelegate.modelData.state === "completed" ? 82 : 112
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
                                text: root.bridge.text("action.cancel")
                                onClicked: root.bridge.taskList.cancel(taskDelegate.modelData.id)
                            }
                            AppButton {
                                visible: ["failed", "cancelled", "interrupted"].includes(taskDelegate.modelData.state)
                                compact: true
                                text: root.bridge.text("action.retry")
                                onClicked: root.bridge.taskList.retry(taskDelegate.modelData.id)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 26
                            spacing: 8

                            Label {
                                Layout.fillWidth: true
                                text: taskDelegate.modelData.error_summary.length > 0
                                    ? taskDelegate.modelData.error_summary
                                    : taskDelegate.modelData.result_path.length > 0
                                        ? taskDelegate.modelData.result_path
                                        : root.bridge.text("label.stage") + ": " + taskDelegate.modelData.localized_stage
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
                            AppButton {
                                visible: taskDelegate.modelData.result_path.length > 0
                                compact: true
                                text: root.bridge.text("action.open_result")
                                onClicked: root.bridge.taskList.openResult(taskDelegate.modelData.result_path)
                            }
                            AppButton {
                                visible: taskDelegate.modelData.result_path.length > 0
                                compact: true
                                text: root.bridge.text("action.open_folder")
                                onClicked: root.bridge.taskList.openResultFolder(taskDelegate.modelData.result_path)
                            }
                            AppButton {
                                visible: taskDelegate.modelData.error_summary.length > 0
                                compact: true
                                text: taskDelegate.expanded
                                    ? root.bridge.text("action.collapse")
                                    : root.bridge.text("action.details")
                                onClicked: taskDelegate.expanded = !taskDelegate.expanded
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
                                text: String(taskDelegate.modelData.error || taskDelegate.modelData.error_summary || "")
                                color: root.theme.danger
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
                                text: root.bridge.text("action.copy")
                                onClicked: root.bridge.taskList.copyText(
                                    String(taskDelegate.modelData.error || taskDelegate.modelData.error_summary || "")
                                )
                            }
                        }
                    }
                }
            }

            EmptyState {
                anchors.centerIn: parent
                visible: root.filteredTasks.length === 0
                title: root.bridge.text("empty.tasks.title")
                detail: root.bridge.text("empty.tasks.detail")
            }
        }
    }
}
