"""Voice-over with ElevenLabs (Turkish, eleven_multilingual_v2), same timing as make_vo.py.

Key: read from $ELEVENLABS_API_KEY or from ~/.config/pharmacam/elevenlabs.env (a line ELEVENLABS_API_KEY=...).
The key is never printed.

    python3 vo/make_vo_elevenlabs.py --list            # voices in your account (name, id, labels)
    python3 vo/make_vo_elevenlabs.py --voice <id>      # record all lines → vo/lineNN.wav + vo/plan.json
    python3 vo/mix_vo.py                               # mix → out/audio_vo.wav
"""
import argparse
import json
import os
import ssl
import subprocess
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
API = 'https://api.elevenlabs.io/v1'
# python.org builds ship without CA certs on macOS: fall back to the system bundle
CTX = ssl.create_default_context(cafile='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None)


def api_key() -> str:
    k = os.environ.get('ELEVENLABS_API_KEY')
    f = Path.home() / '.config' / 'pharmacam' / 'elevenlabs.env'
    if not k and f.exists():
        for line in f.read_text().splitlines():
            if line.strip().startswith('ELEVENLABS_API_KEY='):
                k = line.split('=', 1)[1].strip().strip('"\'')
    if not k:
        raise SystemExit(f'ElevenLabs anahtarı yok: ELEVENLABS_API_KEY ortam değişkeni ya da {f}')
    return k


def call(path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                 headers={'xi-api-key': api_key(), 'Content-Type': 'application/json',
                                          'Accept': 'audio/mpeg' if body else 'application/json'})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        return r.read()


def dur(p):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(p)]))


def tts(text, voice, speed, out):
    mp3 = out.with_suffix('.mp3')
    mp3.write_bytes(call(f'/text-to-speech/{voice}', {
        'text': text, 'model_id': 'eleven_multilingual_v2', 'language_code': 'tr',
        'voice_settings': {'stability': 0.55, 'similarity_boost': 0.8, 'style': 0.15, 'speed': speed},
    }))
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(mp3), '-af',
                    'silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse',
                    '-ar', '48000', '-ac', '1', str(out)], check=True)
    mp3.unlink()
    return dur(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--voice')
    a = ap.parse_args()
    if a.list:
        for v in json.loads(call('/voices'))['voices']:
            print(f"{v['name']:<28} {v['voice_id']}  {v.get('labels', {})}")
        return
    if not a.voice:
        raise SystemExit('--voice <voice_id> gerekli (önce --list)')
    plan = []
    lines = (HERE / 'lines.tsv').read_text(encoding='utf-8').strip().splitlines()
    for i, line in enumerate(lines, 1):
        t0, t1, text = line.split('\t')
        t0, win = float(t0), float(t1) - float(t0)
        text = text.replace('Farma Kam', 'PharmaCam')      # ElevenLabs reads the brand name correctly
        out = HERE / f'line{i:02d}.wav'
        speed = 1.0
        d = tts(text, a.voice, speed, out)
        while d > win and speed < 1.2:                      # ElevenLabs allows 0.7–1.2
            speed = min(1.2, round(speed * d / win * 1.02, 2))
            d = tts(text, a.voice, speed, out)
        plan.append({'i': i, 't': t0, 'file': out.name, 'dur': round(d, 2), 'win': round(win, 2), 'speed': speed, 'text': text})
        print(f"{i:2d}  t={t0:5.2f}  süre={d:4.2f}/{win:4.2f}  hız={speed}  {text}" + ('   ← SIĞMADI' if d > win + 0.05 else ''))
    (HERE / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
