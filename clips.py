"""Calibration kit: time Cluely itself on the same AMI questions.

  python clips.py --n 20                 cut 20 question clips with a start beep into clips/
  python clips.py --report clips/cluely_times.csv   summarize the times you read off a screen recording

Each clip is a short beep, then LEAD seconds of meeting audio ending with the question, then TAIL seconds more.
The question ends exactly BEEP + LEAD seconds after the clip starts, so with a screen recording of Cluely running
while the clip plays, the wait is (time the answer appeared) - (beep time) - BEEP - LEAD.
"""
import argparse, csv, io, math, pathlib, random, statistics, struct, urllib.request, wave
import ami

AUDIO = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Mix-Headset.wav"
BEEP, LEAD, TAIL = 0.3, 12.0, 8.0
ROOT = pathlib.Path(__file__).parent

def beep(rate, width, channels, seconds=BEEP, hz=1000):
    n = int(rate * seconds); amp = 0.4 * (2 ** (8 * width - 1) - 1)
    frames = b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * hz * i / rate))) * channels for i in range(n))
    return frames if width == 2 else bytes(n * width * channels)

def cut(src_bytes, end_s, dst):
    with wave.open(io.BytesIO(src_bytes)) as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        start = max(0.0, end_s - LEAD)
        w.setpos(int(start * rate)); frames = w.readframes(int((end_s - start + TAIL) * rate))
    with wave.open(str(dst), "wb") as o:
        o.setnchannels(ch); o.setsampwidth(width); o.setframerate(rate)
        o.writeframes(beep(rate, width, ch) + frames)
    return BEEP + (end_s - start)                     # seconds from clip start to question end

def make(n, seed, meetings):
    qs = [q for q in ami.load(max_meetings=meetings) if q["end"] > LEAD]
    random.Random(seed).shuffle(qs); qs = qs[:n]
    out = ROOT / "clips"; out.mkdir(exist_ok=True); cache = ROOT / "data" / "audio"; cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, q in enumerate(sorted(qs, key=lambda q: (q["meeting"], q["end"])), 1):
        f = cache / f"{q['meeting']}.Mix-Headset.wav"
        if not f.exists():
            print(f"downloading audio for {q['meeting']}...", flush=True)
            f.write_bytes(urllib.request.urlopen(AUDIO.format(m=q["meeting"]), timeout=600).read())
        name = f"q{i:02d}.wav"
        qend = cut(f.read_bytes(), q["end"], out / name)
        rows.append({"clip": name, "meeting": q["meeting"], "question": " ".join(t for _, _, t in q["words"]),
                     "question_end_s": f"{qend:.2f}", "answer_appeared_s": ""})
    with open(out / "cluely_times.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    print(f"wrote {len(rows)} clips and clips/cluely_times.csv; fill in answer_appeared_s from your recording")

def report(path):
    waits = []
    for r in csv.DictReader(open(path)):
        if r.get("answer_appeared_s", "").strip():
            waits.append(float(r["answer_appeared_s"]) - float(r["question_end_s"]))
    if not waits:
        print("no answer times filled in yet"); return
    waits.sort()
    print(f"Cluely, {len(waits)} clips: question end to answer on screen, median {statistics.median(waits):.2f} s, "
          f"p90 {waits[min(len(waits)-1, int(0.9*len(waits)))]:.2f} s, range {waits[0]:.2f} to {waits[-1]:.2f} s")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20); ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--meetings", type=int, default=4, help="limit audio downloads to the first N meetings")
    ap.add_argument("--report")
    a = ap.parse_args()
    report(a.report) if a.report else make(a.n, a.seed, a.meetings)
