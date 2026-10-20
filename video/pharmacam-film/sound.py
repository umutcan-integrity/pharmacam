"""PharmaCam film — soundtrack on the same timeline as scene.js (120 BPM, bar = 2 s).
Run:  python examples/pharmacam-film/sound.py  →  examples/pharmacam-film/out/audio.wav"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'audio'))
from ftsynth import *  # noqa: E402,F403

m = Mix.from_project(__file__)
BAR = m.bar

# harmony — calm, warm; one chord per bar, the brightest on the end card
prog_ = [('Em9', 3), ('Cmaj7', 3), ('G6', 3), ('Dadd9', 3), ('Em9', 3), ('Cmaj7', 3), ('Am9', 3), ('Dadd9', 3)]
for b, (name, octave) in enumerate(prog_):
    m.pad(m.at(b), chord(name, octave), dur=BAR, att=1.2 if b == 0 else 0.12, gain=0.2 if b == 0 else 0.26, cutoff=700 if b == 0 else 1300)
m.pad(16.0, chord('Gmaj9', 3) + [74, 78], dur=3.6, att=0.03, rel=1.4, gain=0.36, cutoff=2800, wet=0.6)

# bar 0 — the plan is born from the door outwards; headline whoosh
for i in range(10):
    m.add(0.08 + i * 0.15, blip(midi(76 + (i % 5) * 2)), 0.035, -0.5 + 0.1 * i, 0.6)
m.whoosh(0.22, 0.45, 800, 4200, 0.09)

# bar 1 — customers cross the door: a rising blip per crossing, the counter rolls
m.drums(1, kick=[0, 8], hats=range(2, 16, 4), root=40)
for i, tc in enumerate([2.6, 2.8, 2.95, 3.15, 3.4, 3.55]):
    m.add(tc, pluck(midi(76 + [0, 2, 4, 7, 9, 12][i]), dec=0.3), 0.14, -0.3 + 0.12 * i, 0.4)
for k in range(18):
    m.add(2.3 + 1.4 * (1 - (1 - k / 18) ** 2), keyclick(), 0.05 + 0.04 * k / 18, 0.1)
m.whoosh(2.15, 0.4, 900, 4000, 0.08)

# bar 2 — camera glides to the shelf; chips pop; dwell seconds tick
m.drums(2, kick=[0, 8, 10], hats=range(0, 16, 2), root=36)
m.whoosh(3.82, 0.6, 300, 1400, 0.14, bw=0.8)
m.add(4.2, pop(640), 0.16, 0.2, 0.3)
m.add(4.45, pop(760), 0.14, 0.25, 0.3)
for k in range(6):
    m.add(4.6 + k * 0.18, blip(midi(88)), 0.03, 0.3, 0.3)

# bar 3 — the queue: clock ticks on the wait chip, a bell when served
m.drums(3, kick=[0, 8], snare=[12], hats=range(0, 16, 2), root=38)
m.whoosh(5.82, 0.6, 300, 1400, 0.14, bw=0.8, pan=-0.2)
m.add(6.25, pop(700), 0.15, 0.3, 0.3)
for k in range(8):
    m.add(6.3 + k * 0.125, hat(0.018), 0.08, 0.35)
m.add(7.7, bell(midi(86), 1.0), 0.09, 0.1, 0.6)
m.add(7.75, pop(820), 0.12, 0.2, 0.3)

# bar 4 — staff recognised: two pops, then a soft downward glide as they grey out
m.drums(4, kick=[0, 8], hats=range(0, 16, 2), root=40)
m.whoosh(7.82, 0.6, 300, 1400, 0.14, bw=0.8, pan=0.2)
m.add(8.35, pop(600), 0.15, -0.2, 0.3)
m.add(8.47, pop(680), 0.15, 0.2, 0.3)
m.glide(8.9, 0.5, 900, 420, 0.05, dec=0.3, wet=0.5)

# bar 5 — pull back; the heatmap blooms; build into the privacy turn
m.drums(5, kick=[0, 8, 10], snare=[8], hats=range(16), root=36)
m.whoosh(9.82, 0.9, 1400, 260, 0.14, bw=0.9)
for i in range(7):
    m.add(10.1 + i * 0.12, pluck(midi(64 + [0, 3, 7, 10, 12, 15, 19][i]), dec=0.6), 0.08, -0.4 + 0.13 * i, 0.6)
m.riser(10.8, 12.0, gain=0.16)

# bar 6 — the camera view appears, is sucked into the chip, numbers come out
m.add(12.0, blip(midi(81)), 0.07, 0, 0.5)
m.suck(12.5, 13.0, 0.3)
m.impact(13.0, 0.8)
for i, tt in enumerate([13.15, 13.27, 13.39]):
    m.add(tt, pop(720 + 140 * i), 0.16, (-0.4, 0.4, 0)[i], 0.3)

# bar 7 — pills morph into the dashboard, the forecast line draws, riser into the end card
m.drums(7, kick=[0, 8], hats=range(0, 16, 2), root=38)
m.whoosh(14.0, 0.5, 600, 3000, 0.12)
m.add(14.25, pop(900), 0.13, 0.3, 0.3)
m.glide(14.55, 0.9, 300, 900, 0.04, dec=0.6, wet=0.5)
m.add(15.3, pop(980), 0.14, 0.2, 0.3)
m.add(15.32, bell(midi(91), 0.6), 0.05, 0.2, 0.6)
m.riser(15.1, 16.0, gain=0.2)

# bars 8–9 — end card: the logo lands, wordmark, tagline, CTA
m.impact(16.05, 0.55, bright=False)
m.whoosh(16.3, 0.6, 700, 3600, 0.1)
m.add(16.85, bell(midi(88), 1.4), 0.07, 0, 0.7)
m.add(17.5, mouseclick(), 0.18, 0.15)
m.add(17.55, pop(760, 0.08), 0.13, 0.15, 0.4)

m.render()
