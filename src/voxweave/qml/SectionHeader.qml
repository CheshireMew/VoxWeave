import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: header

    property string title: ""
    property string badgeText: ""
    property string badgeTone: "neutral"

    Theme { id: theme }

    spacing: 9

    Label {
        text: header.title
        color: theme.text
        font.family: theme.uiFont
        font.pixelSize: 15
        font.weight: Font.DemiBold
    }
    Item { Layout.fillWidth: true }

    StatusPill {
        visible: header.badgeText.length > 0
        text: header.badgeText
        tone: header.badgeTone
    }
}
