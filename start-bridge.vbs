' start-bridge.vbs — launch the host<->Kali bridge with no console window.
' Put a shortcut/copy of this file in:
'   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
' so the bridge comes back automatically after a login.
Dim shell, here, script
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
script = here & "\run_bridge.ps1"
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """", 0, False
