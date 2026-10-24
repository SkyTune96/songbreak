# Speak the test song's lyrics to WAV files with Windows' built-in voice (used by make_test_song.py).
param([string]$OutDir)
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force $OutDir | Out-Null
$parts = @{
  "verse1"  = "I walked along the river when the city lights were low. I heard a voice behind me that I used to know.";
  "chorus"  = "Hold on to the night, don't let the morning come. Hold on to the night, we're not done.";
  "verse2"  = "She said the years had changed her, but her eyes were still the same. We stood there in the silence and I whispered her name.";
}
foreach ($k in $parts.Keys) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.Rate = -1
  $s.SetOutputToWaveFile((Join-Path $OutDir "$k.wav"))
  $s.Speak($parts[$k])
  $s.Dispose()
}
