"""Mix the voice-over over the film soundtrack: place lines on the timeline, duck the music under speech,
then loudness-normalise to -14 LUFS / -1.5 dBTP.  Output: out/audio_vo.wav"""
import json, subprocess
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from scipy.ndimage import uniform_filter1d

HERE = Path(__file__).parent
OUT = HERE.parent / 'out'
SR = 48000
OFFSETS = {8: 13.55, 10: 16.05}   # small manual nudges (line index → start time)

sr, music = wavfile.read(OUT / 'audio.wav')
assert sr == SR
music = music.astype(np.float32)
if music.dtype.kind == 'i' or np.abs(music).max() > 2:
    music /= 32768.0
if music.ndim == 1:
    music = np.stack([music, music], 1)
N = len(music)
vo = np.zeros(N, np.float32)
for item in json.loads((HERE / 'plan.json').read_text(encoding='utf-8')):
    r, x = wavfile.read(HERE / item['file'])
    x = x.astype(np.float32)
    x /= (32768.0 if np.abs(x).max() > 2 else 1.0)
    x = x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 0.12          # even level per line
    t = OFFSETS.get(item['i'], item['t'])
    a = int(t * SR)
    b = min(N, a + len(x))
    fade = np.minimum(1, np.minimum(np.arange(b - a), np.arange(b - a)[::-1]) / (0.01 * SR))
    vo[a:b] += x[:b - a] * fade

# ducking envelope: fast attack, slow release, -7 dB under speech
env = (np.abs(vo) > 0.004).astype(np.float32)
env = uniform_filter1d(env, int(0.25 * SR))
env = np.clip(env * 3, 0, 1)
duck = 1 - 0.55 * uniform_filter1d(env, int(0.12 * SR))
mix = music * duck[:, None] + np.stack([vo, vo], 1) * 1.0
tmp = OUT / 'audio_vo_raw.wav'
wavfile.write(tmp, SR, mix.astype(np.float32))
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(tmp), '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11', '-ar', '48000',
                str(OUT / 'audio_vo.wav')], check=True)
tmp.unlink()
r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(OUT / 'audio_vo.wav'), '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                   capture_output=True, text=True).stderr.splitlines()
print('loudness', next(l for l in reversed(r) if l.strip().startswith('I:')).split(':')[1].strip(),
      '| true peak', next(l for l in reversed(r) if l.strip().startswith('Peak:')).split(':')[1].strip())
