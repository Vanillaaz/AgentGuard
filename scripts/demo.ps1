$py = if (Test-Path "$PSScriptRoot\..\.venv\Scripts\python.exe") { "$PSScriptRoot\..\.venv\Scripts\python.exe" } else { "python" }
& $py -m backend.app.demo_cli
