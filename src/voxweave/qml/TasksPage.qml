pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    required property var bridge
    required property var theme
    property var tasks: []

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

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: taskList
                objectName: "taskList"
                anchors.fill: parent
                model: root.tasks
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
                    width: ListView.view.width
                    height: 108
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
                                        : root.bridge.text("label.stage") + ": " + (taskDelegate.modelData.stage || taskDelegate.modelData.state)
                                color: taskDelegate.modelData.error_summary.length > 0 ? root.theme.danger : root.theme.textMuted
                                font.family: taskDelegate.modelData.result_path.length > 0 ? root.theme.monoFont : root.theme.uiFont
                                font.pixelSize: 9
                                elide: Text.ElideMiddle
                            }
                            Label {
                                text: String(taskDelegate.modelData.updated_at || "").replace("T", " ").slice(0, 19)
                                color: root.theme.textDim
                                font.family: root.theme.monoFont
                                font.pixelSize: 8
                            }
                            AppButton {
                                visible: taskDelegate.modelData.result_path.length > 0
                                compact: true
                                text: root.bridge.text("action.open_result")
                                onClicked: root.bridge.taskList.openResult(taskDelegate.modelData.result_path)
                            }
                        }

                        RowLayout {
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
                    }
                }
            }

            EmptyState {
                anchors.centerIn: parent
                visible: root.tasks.length === 0
                title: root.bridge.text("empty.tasks.title")
                detail: root.bridge.text("empty.tasks.detail")
            }
        }
    }
}
