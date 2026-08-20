import QtQuick
import QtQuick.Layouts

Rectangle {
    id: panel

    default property alias contentData: contentLayout.data
    property int padding: theme.panelPadding
    property int contentSpacing: 9
    property Component overlay: null

    Theme { id: theme }

    radius: theme.radiusMedium
    color: theme.surface
    border.color: theme.border
    border.width: 1
    implicitWidth: 300
    implicitHeight: contentLayout.implicitHeight + padding * 2

    ColumnLayout {
        id: contentLayout
        anchors.fill: parent
        anchors.margins: panel.padding
        spacing: panel.contentSpacing
    }

    Loader {
        anchors.fill: parent
        z: 100
        sourceComponent: panel.overlay
    }
}
