import QtQuick
import QtQuick.Window

Item {
    id: control

    required property var targetWindow
    property int borderSize: 5
    property int cornerSize: 11

    visible: targetWindow.visibility !== Window.Maximized && targetWindow.visibility !== Window.FullScreen

    MouseArea {
        x: 0
        y: control.cornerSize
        width: control.borderSize
        height: parent.height - control.cornerSize * 2
        cursorShape: Qt.SizeHorCursor
        onPressed: control.targetWindow.startSystemResize(Qt.LeftEdge)
    }
    MouseArea {
        x: parent.width - control.borderSize
        y: control.cornerSize
        width: control.borderSize
        height: parent.height - control.cornerSize * 2
        cursorShape: Qt.SizeHorCursor
        onPressed: control.targetWindow.startSystemResize(Qt.RightEdge)
    }
    MouseArea {
        x: control.cornerSize
        y: 0
        width: parent.width - control.cornerSize * 2
        height: control.borderSize
        cursorShape: Qt.SizeVerCursor
        onPressed: control.targetWindow.startSystemResize(Qt.TopEdge)
    }
    MouseArea {
        x: control.cornerSize
        y: parent.height - control.borderSize
        width: parent.width - control.cornerSize * 2
        height: control.borderSize
        cursorShape: Qt.SizeVerCursor
        onPressed: control.targetWindow.startSystemResize(Qt.BottomEdge)
    }

    MouseArea {
        x: 0
        y: 0
        width: control.cornerSize
        height: control.cornerSize
        cursorShape: Qt.SizeFDiagCursor
        onPressed: control.targetWindow.startSystemResize(Qt.LeftEdge | Qt.TopEdge)
    }
    MouseArea {
        x: parent.width - control.cornerSize
        y: 0
        width: control.cornerSize
        height: control.cornerSize
        cursorShape: Qt.SizeBDiagCursor
        onPressed: control.targetWindow.startSystemResize(Qt.RightEdge | Qt.TopEdge)
    }
    MouseArea {
        x: 0
        y: parent.height - control.cornerSize
        width: control.cornerSize
        height: control.cornerSize
        cursorShape: Qt.SizeBDiagCursor
        onPressed: control.targetWindow.startSystemResize(Qt.LeftEdge | Qt.BottomEdge)
    }
    MouseArea {
        x: parent.width - control.cornerSize
        y: parent.height - control.cornerSize
        width: control.cornerSize
        height: control.cornerSize
        cursorShape: Qt.SizeFDiagCursor
        onPressed: control.targetWindow.startSystemResize(Qt.RightEdge | Qt.BottomEdge)
    }
}
