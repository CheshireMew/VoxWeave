import QtQuick
import QtQuick.Controls

Label {
    Theme { id: theme }

    color: theme.textMuted
    font.family: theme.uiFont
    font.pixelSize: 12
    font.weight: Font.Medium
}
