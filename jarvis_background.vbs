
Set shell = CreateObject("WScript.Shell")
folder = "C:\Users\mangi\OneDrive\Desktop\JARVIS"
shell.CurrentDirectory = folder
shell.Run "pyw.exe jarvis.py", 0, False
