pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Window

Rectangle {
    id: control

    required property var targetWindow
    property string title: ""
    property string minimizeLabel: "Minimize"
    property string maximizeLabel: "Maximize"
    property string restoreLabel: "Restore"
    property string closeLabel: "Close"

    Theme { id: theme }

    implicitHeight: theme.titleBarHeight
    color: theme.sidebar

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: theme.border
    }

    MouseArea {
        anchors.left: parent.left
        anchors.right: windowControls.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        acceptedButtons: Qt.LeftButton
        onPressed: control.targetWindow.startSystemMove()
        onDoubleClicked: {
            if (control.targetWindow.visibility === Window.Maximized)
                control.targetWindow.showNormal()
            else
                control.targetWindow.showMaximized()
        }
    }

    RowLayout {
        anchors.left: parent.left
        anchors.leftMargin: 14
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: windowControls.left
        spacing: 9

        Rectangle {
            Layout.preferredWidth: 19
            Layout.preferredHeight: 19
            radius: 5
            color: theme.accent

            Row {
                anchors.centerIn: parent
                spacing: 1
                Repeater {
                    model: [6, 11, 15, 10, 7]
                    delegate: Rectangle {
                        required property int modelData
                        width: 2
                        height: modelData
                        radius: 1
                        anchors.verticalCenter: parent.verticalCenter
                        color: theme.accentInk
                    }
                }
            }
        }

        Basic.Label {
            Layout.fillWidth: true
            text: control.title
            color: theme.textMuted
            font.family: theme.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
    }

    Row {
        id: windowControls
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        WindowControlButton {
            objectName: "minimizeButton"
            kind: "minimize"
            accessibleName: control.minimizeLabel
            onClicked: control.targetWindow.showMinimized()
        }
        WindowControlButton {
            objectName: "maximizeButton"
            kind: "maximize"
            restoreMode: control.targetWindow.visibility === Window.Maximized
            accessibleName: restoreMode ? control.restoreLabel : control.maximizeLabel
            onClicked: {
                if (control.targetWindow.visibility === Window.Maximized)
                    control.targetWindow.showNormal()
                else
                    control.targetWindow.showMaximized()
            }
        }
        WindowControlButton {
            objectName: "closeButton"
            kind: "close"
            accessibleName: control.closeLabel
            onClicked: control.targetWindow.close()
        }
    }
}
