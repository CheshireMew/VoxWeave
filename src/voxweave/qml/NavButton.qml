import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.Button {
    id: control

    property string iconName: "convert"
    property bool selected: false
    property bool showLabel: false

    Theme { id: theme }

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: 44
    implicitWidth: 44
    leftPadding: control.showLabel ? 12 : 0
    rightPadding: control.showLabel ? 10 : 0

    contentItem: Row {
        spacing: 10
        NavIcon {
            width: 24
            height: 24
            anchors.verticalCenter: parent.verticalCenter
            kind: control.iconName
            color: control.selected ? theme.accent : (control.hovered ? theme.text : theme.textMuted)
        }
        Basic.Label {
            anchors.verticalCenter: parent.verticalCenter
            visible: control.showLabel
            text: control.text
            color: control.selected ? theme.text : theme.textMuted
            font.family: theme.uiFont
            font.pixelSize: 12
            elide: Text.ElideRight
            width: control.showLabel ? Math.max(0, control.width - 58) : 0
        }
    }

    background: Rectangle {
        radius: theme.radiusMedium
        color: control.selected ? theme.surfaceRaised : (control.hovered ? theme.surface : "transparent")
        border.width: control.activeFocus ? 1 : 0
        border.color: theme.info

        Rectangle {
            width: 3
            height: 22
            radius: 2
            anchors.left: parent.left
            anchors.leftMargin: 1
            anchors.verticalCenter: parent.verticalCenter
            color: theme.accent
            visible: control.selected
        }

        Behavior on color { ColorAnimation { duration: 110 } }
    }

    Basic.ToolTip.visible: control.hovered
    Basic.ToolTip.text: control.text
    Basic.ToolTip.delay: 350
}
