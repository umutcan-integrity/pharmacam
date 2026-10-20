"""Record voice-over lines with a TTS command, trim silence and fit each line into its window."""
import subprocess, sys, json
from pathlib import Path
HERE = Path(__file__).parent
def dur(p): return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(p)]))
def say(text, rate, out):
    raw = out.with_suffix('.aiff')
    subprocess.run(['say', '-v', 'Yelda', '-r', str(int(rate)), '-o', str(raw), text], check=True)
    # trim leading/trailing silence, 48 kHz mono wav
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(raw), '-af',
                    'silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse',
                    '-ar', '48000', '-ac', '1', str(out)], check=True)
    raw.unlink()
    return dur(out)
plan = []
for i, line in enumerate((HERE / 'lines.tsv').read_text(encoding='utf-8').strip().splitlines(), 1):
    t0, t1, text = line.split('\t')
    t0, t1 = float(t0), float(t1)
    win = t1 - t0
    out = HERE / f'line{i:02d}.wav'
    rate = 175
    d = say(text, rate, out)
    while d > win and rate < 240:
        rate = min(240, rate * d / win * 1.03)
        d = say(text, rate, out)
    plan.append({'i': i, 't': t0, 'file': out.name, 'dur': round(d, 2), 'win': round(win, 2), 'rate': int(rate), 'text': text})
    print(f"{i:2d}  t={t0:5.2f}  süre={d:4.2f}/{win:4.2f}  hız={int(rate)}  {text}")
(HERE / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1))
