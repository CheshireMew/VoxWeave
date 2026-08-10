pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic as Basic

Basic.ComboBox {
    id: control

    Theme { id: theme }

    property string emptyText: ""

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: theme.controlHeight
    leftPadding: 13
    rightPadding: 36
    font.family: theme.uiFont
    font.pixelSize: 13

    contentItem: Text {
        leftPadding: 0
        rightPadding: 0
        text: control.count > 0 ? control.displayText : control.emptyText
        font: control.font
        color: control.enabled ? theme.text : theme.textDim
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Text {
        x: control.width - width - 13
        y: (control.height - height) / 2 - 1
        text: "⌄"
        color: control.enabled ? theme.textMuted : theme.textDim
        font.family: theme.uiFont
        font.pixelSize: 17
    }

    background: Rectangle {
        radius: theme.radiusSmall
        color: control.pressed ? theme.surfaceHover : theme.field
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? theme.accent : (control.hovered ? theme.borderStrong : theme.border)
        Behavior on border.color { ColorAnimation { duration: 100 } }
    }

    delegate: Basic.ItemDelegate {
        id: option
        required property int index
        width: control.width - 12
        height: 38
        leftPadding: 11
        rightPadding: 11
        highlighted: control.highlightedIndex === index

        contentItem: Text {
            text: control.textAt(option.index)
            color: theme.text
            font.family: theme.uiFont
            font.pixelSize: 13
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        background: Rectangle {
            radius: theme.radiusSmall
            color: option.highlighted ? theme.accentWash : "transparent"
        }
    }

    popup: Basic.Popup {
        y: control.height + 5
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + 12, 300)
        padding: 6

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            Basic.ScrollIndicator.vertical: Basic.ScrollIndicator { }
        }

        background: Rectangle {
            radius: theme.radiusMedium
            color: theme.surfaceRaised
            border.color: theme.borderStrong
            border.width: 1
        }
    }
}
