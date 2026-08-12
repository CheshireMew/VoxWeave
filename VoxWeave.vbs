Option Explicit

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & Replace(CStr(value), Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function

Dim fileSystem, shell, repository, launcher, command, argument, exitCode
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repository = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = fileSystem.BuildPath(repository, "scripts\run.ps1")
command = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " _
    & QuoteArgument(launcher) & " -Windowless"

For Each argument In WScript.Arguments
    command = command & " " & QuoteArgument(argument)
Next

exitCode = shell.Run(command, 0, True)
If exitCode <> 0 Then
    MsgBox "VoxWeave failed to start. Run scripts\run.ps1 to view the error.", 16, "VoxWeave"
End If
WScript.Quit exitCode
