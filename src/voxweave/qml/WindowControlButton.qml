import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.AbstractButton {
    id: control

    Theme { id: theme }

    property string kind: "minimize"
    property bool restoreMode: false
    property string accessibleName: ""

    implicitWidth: 46
    implicitHeight: 39
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: accessibleName

    background: Rectangle {
        color: {
            if (control.kind === "close" && control.hovered)
                return theme.windowCloseHover
            if (control.pressed)
                return theme.surfaceRaised
            if (control.hovered)
                return theme.surfaceHover
            return "transparent"
        }
        Behavior on color { ColorAnimation { duration: 80 } }
    }

    contentItem: Item {
        Rectangle {
            visible: control.kind === "minimize"
            width: 12
            height: 1
            anchors.centerIn: parent
            anchors.verticalCenterOffset: 4
            color: theme.textMuted
        }

        Rectangle {
            visible: control.kind === "maximize" && !control.restoreMode
            width: 10
            height: 9
            anchors.centerIn: parent
            color: "transparent"
            border.width: 1
            border.color: theme.textMuted
        }

        Item {
            visible: control.kind === "maximize" && control.restoreMode
            width: 13
            height: 12
            anchors.centerIn: parent

            Rectangle {
                width: 9
                height: 8
                x: 4
                y: 0
                color: theme.sidebar
                border.width: 1
                border.color: theme.textMuted
            }
            Rectangle {
                width: 9
                height: 8
                x: 0
                y: 4
                color: theme.sidebar
                border.width: 1
                border.color: theme.textMuted
            }
        }

        Item {
            visible: control.kind === "close"
            width: 13
            height: 13
            anchors.centerIn: parent

            Rectangle {
                width: 14
                height: 1
                anchors.centerIn: parent
                rotation: 45
                color: control.hovered ? "white" : theme.textMuted
            }
            Rectangle {
                width: 14
                height: 1
                anchors.centerIn: parent
                rotation: -45
                color: control.hovered ? "white" : theme.textMuted
            }
        }
    }
}
