
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

audio_file = os.path.join(
    BASE_DIR,
    "jarvis_voice.mp3"
)

if not os.path.isfile(audio_file):
    print("ERROR: jarvis_voice.mp3 nahi mili!")
    raise SystemExit

print("Testing JARVIS Hindi voice...")

powershell_script = r"""
Add-Type -AssemblyName PresentationCore

$player = New-Object System.Windows.Media.MediaPlayer

try {
    $player.Open([Uri]::new($args[0]))

    $limit = [DateTime]::UtcNow.AddSeconds(15)

    while (
        -not $player.NaturalDuration.HasTimeSpan -and
        [DateTime]::UtcNow -lt $limit
    ) {
        Start-Sleep -Milliseconds 100
    }

    $player.Volume = 1.0
    $player.Play()

    if ($player.NaturalDuration.HasTimeSpan) {
        $duration = $player.NaturalDuration.TimeSpan.TotalSeconds
        Start-Sleep -Milliseconds ([int](($duration + 0.5) * 1000))
    }
    else {
        Start-Sleep -Seconds 8
    }
}
finally {
    $player.Close()
}
"""

result = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        powershell_script,
        audio_file
    ],
    capture_output=True,
    text=True,
    timeout=40
)

if result.returncode == 0:
    print("Playback test finished.")
else:
    print("Playback error:")
    print(result.stderr)
