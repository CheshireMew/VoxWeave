pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Basic.ApplicationWindow {
    id: root
    required property var bridge

    width: 560
    height: 700
    minimumWidth: 540
    minimumHeight: 620
    visible: true
    title: root.bridge.text("app.title")
    flags: Qt.Window | Qt.FramelessWindowHint
    color: theme.canvas
    font.family: theme.uiFont

    Theme { id: theme }

    palette {
        window: theme.canvas
        windowText: theme.text
        base: theme.field
        alternateBase: theme.surface
        text: theme.text
        button: theme.surfaceRaised
        buttonText: theme.text
        highlight: theme.accent
        highlightedText: theme.accentInk
        placeholderText: theme.textDim
        toolTipBase: theme.surfaceRaised
        toolTipText: theme.text
    }

    property var models: bridge.modelCatalog.items
    property var readyModels: []
    property var tasks: bridge.taskList.items
    property var speakers: bridge.media.speakers
    property var previewOutputs: bridge.media.previewOutputs
    property var presets: bridge.media.presets
    property var batches: bridge.batchRules.items
    property var realtimeDevices: bridge.realtime.devices
    property var realtimeStatus: bridge.realtime.status
    property int currentPage: 0


    Connections {
        target: root.bridge.modelCatalog
        function onItemsChanged() {
            root.models = root.bridge.modelCatalog.items
            root.readyModels = root.models.filter(function(item) { return item.status === "ready" })
        }
    }
    Connections {
        target: root.bridge.taskList
        function onItemsChanged() { root.tasks = root.bridge.taskList.items }
    }
    Connections {
        target: root.bridge.media
        function onSpeakersChanged() {
            root.speakers = root.bridge.media.speakers
        }
        function onPreviewOutputsChanged() { root.previewOutputs = root.bridge.media.previewOutputs }
        function onPresetsChanged() { root.presets = root.bridge.media.presets }
    }
    Connections {
        target: root.bridge.batchRules
        function onItemsChanged() { root.batches = root.bridge.batchRules.items }
    }
    Connections {
        target: root.bridge.realtime
        function onDevicesChanged() {
            root.realtimeDevices = root.bridge.realtime.devices
        }
        function onStatusChanged() {
            root.realtimeStatus = root.bridge.realtime.status
        }
    }


    AppTitleBar {
        id: titleBar
        objectName: "windowTitleBar"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        targetWindow: root
        title: root.title
        minimizeLabel: root.bridge.text("window.minimize")
        maximizeLabel: root.bridge.text("window.maximize")
        restoreLabel: root.bridge.text("window.restore")
        closeLabel: root.bridge.text("window.close")
    }

    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: titleBar.bottom
        anchors.bottom: parent.bottom
        spacing: 0

        Rectangle {
            id: sidebar
            objectName: "appSidebar"
            Layout.preferredWidth: theme.sidebarWidth
            Layout.minimumWidth: theme.sidebarWidth
            Layout.fillHeight: true
            color: theme.sidebar

            Rectangle {
                width: 1
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                color: theme.border
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                anchors.topMargin: 10
                anchors.bottomMargin: 10
                spacing: 6

                NavButton {
                    objectName: "navButton0"
                    Layout.fillWidth: true
                    iconName: "realtime"
                    text: root.bridge.text("nav.realtime")
                    selected: root.currentPage === 0
                    onClicked: root.currentPage = 0
                }
                NavButton {
                    objectName: "navButton1"
                    Layout.fillWidth: true
                    iconName: "convert"
                    text: root.bridge.text("nav.convert")
                    selected: root.currentPage === 1
                    onClicked: root.currentPage = 1
                }
                NavButton {
                    objectName: "navButton2"
                    Layout.fillWidth: true
                    iconName: "models"
                    text: root.bridge.text("nav.models")
                    selected: root.currentPage === 2
                    onClicked: root.currentPage = 2
                }
                NavButton {
                    objectName: "navButton3"
                    Layout.fillWidth: true
                    iconName: "batch"
                    text: root.bridge.text("nav.batch")
                    selected: root.currentPage === 3
                    onClicked: root.currentPage = 3
                }
                NavButton {
                    objectName: "navButton4"
                    Layout.fillWidth: true
                    iconName: "tasks"
                    text: root.bridge.text("nav.tasks")
                    selected: root.currentPage === 4
                    onClicked: root.currentPage = 4
                }
                NavButton {
                    objectName: "navButton5"
                    Layout.fillWidth: true
                    iconName: "settings"
                    text: root.bridge.text("nav.settings")
                    selected: root.currentPage === 5
                    onClicked: root.currentPage = 5
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    id: statusIndicator
                    Layout.fillWidth: true
                    Layout.preferredHeight: 38
                    radius: theme.radiusMedium
                    color: theme.surface
                    border.color: theme.border
                    border.width: 1
                    Accessible.name: root.bridge.status === "Ready" ? root.bridge.text("status.ready") : root.bridge.status
                    Accessible.description: root.bridge.statusKind

                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        anchors.centerIn: parent
                        color: root.bridge.statusKind === "danger" ? theme.danger
                            : root.bridge.statusKind === "warning" ? theme.warning
                            : root.bridge.statusKind === "success" ? theme.success : theme.info
                    }

                    HoverHandler { id: statusHover }
                    Basic.ToolTip.visible: statusHover.hovered
                    Basic.ToolTip.text: root.bridge.status === "Ready" ? root.bridge.text("status.ready") : root.bridge.status
                    Basic.ToolTip.delay: 350
                }

                AppButton {
                    objectName: "languageSelector"
                    Layout.fillWidth: true
                    compact: true
                    square: true
                    kind: "quiet"
                    text: root.bridge.language === "zh-CN" ? "中" : "EN"
                    onClicked: root.bridge.language = root.bridge.language === "zh-CN" ? "en" : "zh-CN"
                    Basic.ToolTip.visible: hovered
                    Basic.ToolTip.text: root.bridge.text("label.language")
                    Basic.ToolTip.delay: 350
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.canvas

            StackLayout {
                id: pageStack
                objectName: "pageStack"
                anchors.fill: parent
                currentIndex: root.currentPage

                RealtimePage {
                    bridge: root.bridge
                    theme: theme
                    readyModels: root.readyModels
                    session: root.realtimeStatus
                }

                ConversionPage {
                    bridge: root.bridge
                    theme: theme
                    readyModels: root.readyModels
                    speakers: root.speakers
                    previewOutputs: root.previewOutputs
                    presets: root.presets
                }

                ModelsPage {
                    bridge: root.bridge
                    theme: theme
                    models: root.models
                }

                BatchPage {
                    bridge: root.bridge
                    theme: theme
                    models: root.readyModels
                    batches: root.batches
                }

                TasksPage {
                    bridge: root.bridge
                    theme: theme
                    tasks: root.tasks
                }

                SettingsPage {
                    bridge: root.bridge
                    theme: theme
                    devicePayload: root.realtimeDevices
                }
            }
        }
    }

    WindowResizeHandles {
        anchors.fill: parent
        targetWindow: root
        z: 1000
    }
}
