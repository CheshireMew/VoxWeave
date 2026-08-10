import QtQuick
import QtQuick.Controls

Rectangle {
    id: pill

    property string text: ""
    property string tone: "neutral"

    Theme { id: theme }

    implicitWidth: label.implicitWidth + 18
    implicitHeight: 27
    radius: height / 2
    color: {
        if (tone === "success") return theme.successWash
        if (tone === "warning") return theme.warningWash
        if (tone === "danger") return theme.dangerWash
        if (tone === "info") return theme.infoWash
        if (tone === "accent") return theme.accentWash
        return theme.surfaceRaised
    }
    border.width: 1
    border.color: {
        if (tone === "success") return Qt.alpha(theme.success, 0.42)
        if (tone === "warning") return Qt.alpha(theme.warning, 0.42)
        if (tone === "danger") return Qt.alpha(theme.danger, 0.42)
        if (tone === "info") return Qt.alpha(theme.info, 0.42)
        if (tone === "accent") return Qt.alpha(theme.accent, 0.42)
        return theme.border
    }

    Label {
        id: label
        anchors.centerIn: parent
        text: pill.text
        color: {
            if (pill.tone === "success") return theme.success
            if (pill.tone === "warning") return theme.warning
            if (pill.tone === "danger") return theme.danger
            if (pill.tone === "info") return theme.info
            if (pill.tone === "accent") return theme.accent
            return theme.textMuted
        }
        font.family: theme.uiFont
        font.pixelSize: 11
        font.weight: Font.DemiBold
    }
}
