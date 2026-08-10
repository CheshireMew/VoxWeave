import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.ScrollView {
    id: control

    Theme { id: theme }

    clip: true

    Basic.ScrollBar.vertical: Basic.ScrollBar {
        id: verticalBar
        parent: control
        x: control.width - width - 2
        y: control.topPadding + 2
        height: control.availableHeight - 4
        implicitWidth: 6
        policy: Basic.ScrollBar.AsNeeded

        contentItem: Rectangle {
            implicitWidth: 4
            radius: 2
            color: verticalBar.pressed ? theme.accent : theme.textDim
            opacity: verticalBar.hovered || verticalBar.pressed ? 0.85 : 0.42
            Behavior on opacity { NumberAnimation { duration: 100 } }
        }
        background: Item { }
    }

    Basic.ScrollBar.horizontal: Basic.ScrollBar {
        id: horizontalBar
        parent: control
        x: control.leftPadding + 3
        y: control.height - height - 3
        width: control.availableWidth - 6
        implicitHeight: 8
        policy: Basic.ScrollBar.AlwaysOff

        contentItem: Rectangle {
            implicitHeight: 5
            radius: 3
            color: horizontalBar.pressed ? theme.accent : theme.textDim
            opacity: horizontalBar.hovered || horizontalBar.pressed ? 0.85 : 0.42
            Behavior on opacity { NumberAnimation { duration: 100 } }
        }
        background: Item { }
    }
}
