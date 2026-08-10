import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.ProgressBar {
    id: control

    Theme { id: theme }

    implicitHeight: 7

    background: Rectangle {
        implicitHeight: 7
        radius: 4
        color: theme.field
        border.color: theme.border
        border.width: 1
    }

    contentItem: Item {
        implicitHeight: 7
        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: 4
            color: theme.accent
            Behavior on width { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        }
    }
}
