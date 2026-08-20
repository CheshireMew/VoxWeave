pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts

Basic.ApplicationWindow {
    id: root
    required property var bridge

    width: 960
    height: 720
    minimumWidth: 540
    minimumHeight: 620
    visible: true
    title: root.bridge.text("app.title")
    readonly property bool useNativeTitleBar: Qt.platform.os === "windows"
    flags: root.useNativeTitleBar ? Qt.Window : Qt.Window | Qt.FramelessWindowHint
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
    readonly property bool expandedSidebar: root.width >= 840

    Shortcut { sequence: "Ctrl+1"; onActivated: root.currentPage = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: root.currentPage = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: root.currentPage = 2 }
    Shortcut { sequence: "Ctrl+4"; onActivated: root.currentPage = 3 }
    Shortcut { sequence: "Ctrl+5"; onActivated: root.currentPage = 4 }
    Shortcut { sequence: "Ctrl+6"; onActivated: root.currentPage = 5 }


    Connections {
        target: root.bridge.modelCatalog
        function onItemsChanged() {
            root.models = root.bridge.modelCatalog.items
            root.readyModels = root.models.filter(function(item) {
                return item.status === "ready" && !item.archived
            })
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
        visible: !root.useNativeTitleBar
        title: root.title
        minimizeLabel: root.bridge.text("window.minimize")
        maximizeLabel: root.bridge.text("window.maximize")
        restoreLabel: root.bridge.text("window.restore")
        closeLabel: root.bridge.text("window.close")
    }

    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: root.useNativeTitleBar ? parent.top : titleBar.bottom
        anchors.bottom: parent.bottom
        spacing: 0

        Rectangle {
            id: sidebar
            objectName: "appSidebar"
            Layout.preferredWidth: root.expandedSidebar ? 156 : theme.sidebarWidth
            Layout.minimumWidth: root.expandedSidebar ? 156 : theme.sidebarWidth
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
                    showLabel: root.expandedSidebar
                    onClicked: root.currentPage = 0
                }
                NavButton {
                    objectName: "navButton1"
                    Layout.fillWidth: true
                    iconName: "convert"
                    text: root.bridge.text("nav.convert")
                    selected: root.currentPage === 1
                    showLabel: root.expandedSidebar
                    onClicked: root.currentPage = 1
                }
                NavButton {
                    objectName: "navButton2"
                    Layout.fillWidth: true
                    iconName: "models"
                    text: root.bridge.text("nav.models")
                    selected: root.currentPage === 2
                    showLabel: root.expandedSidebar
                    onClicked: root.currentPage = 2
                }
                NavButton {
                    objectName: "navButton3"
                    Layout.fillWidth: true
                    iconName: "batch"
                    text: root.bridge.text("nav.batch")
                    selected: root.currentPage === 3
                    showLabel: root.expandedSidebar
                    onClicked: root.currentPage = 3
                }
                NavButton {
                    objectName: "navButton4"
                    Layout.fillWidth: true
                    iconName: "tasks"
                    text: root.bridge.text("nav.tasks")
                    selected: root.currentPage === 4
                    showLabel: root.expandedSidebar
                    onClicked: root.currentPage = 4
                }
                NavButton {
                    objectName: "navButton5"
                    Layout.fillWidth: true
                    iconName: "settings"
                    text: root.bridge.text("nav.settings")
                    selected: root.currentPage === 5
                    showLabel: root.expandedSidebar
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
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: root.expandedSidebar ? 12 : (parent.width - width) / 2
                        color: root.bridge.statusKind === "danger" ? theme.danger
                            : root.bridge.statusKind === "warning" ? theme.warning
                            : root.bridge.statusKind === "success" ? theme.success : theme.info
                    }

                    Basic.Label {
                        visible: root.expandedSidebar
                        anchors.left: parent.left
                        anchors.leftMargin: 30
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.bridge.status === "Ready"
                            ? root.bridge.text("status.ready") : root.bridge.status
                        color: theme.textMuted
                        font.family: theme.uiFont
                        font.pixelSize: 11
                        elide: Text.ElideRight
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
                    square: !root.expandedSidebar
                    kind: "quiet"
                    text: root.expandedSidebar
                        ? root.bridge.text("label.language") + " · " + (root.bridge.language === "zh-CN" ? "中文" : "EN")
                        : (root.bridge.language === "zh-CN" ? "中" : "EN")
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
                    pageActive: root.currentPage === 0
                    onNavigateRequested: function(index) { root.currentPage = index }
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
                    onNavigateRequested: function(index) { root.currentPage = index }
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

            Rectangle {
                id: statusBanner
                objectName: "statusBanner"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 10
                height: visible ? 44 : 0
                visible: root.bridge.status !== "Ready"
                z: 900
                radius: theme.radiusMedium
                color: root.bridge.statusKind === "danger" ? theme.dangerWash
                    : root.bridge.statusKind === "warning" ? theme.warningWash
                    : root.bridge.statusKind === "success" ? theme.successWash : theme.infoWash
                border.width: 1
                border.color: root.bridge.statusKind === "danger" ? theme.danger
                    : root.bridge.statusKind === "warning" ? theme.warning
                    : root.bridge.statusKind === "success" ? theme.success : theme.info

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 7
                    spacing: 8
                    Basic.Label {
                        Layout.fillWidth: true
                        text: root.bridge.status
                        color: theme.text
                        font.family: theme.uiFont
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                    AppButton {
                        compact: true
                        visible: root.bridge.statusKind === "danger"
                            || root.bridge.statusKind === "warning"
                        text: root.bridge.text("action.copy")
                        onClicked: root.bridge.copyStatus()
                    }
                    AppButton {
                        compact: true
                        kind: "quiet"
                        text: "×"
                        Accessible.name: root.bridge.text("action.dismiss")
                        onClicked: root.bridge.dismissStatus()
                    }
                }
            }
        }
    }

    WindowResizeHandles {
        anchors.fill: parent
        targetWindow: root
        z: 1000
        visible: !root.useNativeTitleBar
    }
}
