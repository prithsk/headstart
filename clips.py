"""Calibration kit: time Cluely itself on the same AMI questions headstart is measured on.

  python clips.py make --n 20            cut 20 question clips into clips/, list them in clips/cluely_times.csv
  python clips.py play                   play a sync beep, then every clip, logging when each one started
  python clips.py report                 Cluely's wait per clip, from the times you read off your screen recording

Timing works from one anchor. `play` starts with a 1-second sync beep and logs every clip's start relative to it.
You find that one beep in the recording, enter its video time once, and the video time each question ended is
computed for you. You then mark only the video time each Cluely answer first appears.
"""
import argparse, csv, io, math, pathlib, random, statistics, struct, sys, time, urllib.request, wave
import ami

AUDIO = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Mix-Headset.wav"
LEAD, TAIL, GAP = 12.0, 6.0, 14.0     # seconds of meeting before the question end, after it, and silence between clips
ROOT = pathlib.Path(__file__).parent
CSV = ROOT / "clips" / "cluely_times.csv"
FIELDS = ["clip", "meeting", "meeting_end_s", "question", "question_end_in_clip_s", "clip_start_after_sync_s",
          "question_end_video_s", "answer_video_s"]

def tone(path, seconds=1.0, hz=1000, rate=16000):
    with wave.open(str(path), "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(rate)
        o.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / rate))) for i in range(int(rate * seconds))))

def cut(src_bytes, end_s, dst):
    """Write [end - LEAD, end + TAIL] of the meeting to dst. Returns the question end, in seconds from the clip start."""
    with wave.open(io.BytesIO(src_bytes)) as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        start = max(0.0, end_s - LEAD)
        w.setpos(int(start * rate)); frames = w.readframes(int((end_s - start + TAIL) * rate))
    with wave.open(str(dst), "wb") as o:
        o.setnchannels(ch); o.setsampwidth(width); o.setframerate(rate); o.writeframes(frames)
    return end_s - start

def read_rows():
    return list(csv.DictReader(open(CSV, newline="")))

def write_rows(rows):
    with open(CSV, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=FIELDS); wr.writeheader(); wr.writerows(rows)

def make(n, seed, meetings):
    qs = [q for q in ami.load(max_meetings=meetings) if q["end"] > LEAD]
    random.Random(seed).shuffle(qs); qs = sorted(qs[:n], key=lambda q: (q["meeting"], q["end"]))
    out = ROOT / "clips"; out.mkdir(exist_ok=True); cache = ROOT / "data" / "audio"; cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, q in enumerate(qs, 1):
        f = cache / f"{q['meeting']}.Mix-Headset.wav"
        if not f.exists():
            print(f"downloading audio for {q['meeting']}...", flush=True)
            f.write_bytes(urllib.request.urlopen(AUDIO.format(m=q["meeting"]), timeout=900).read())
        name = f"q{i:02d}.wav"
        qend = cut(f.read_bytes(), q["end"], out / name)
        rows.append({"clip": name, "meeting": q["meeting"], "meeting_end_s": f"{q['end']:.2f}",
                     "question": " ".join(t for _, _, t in q["words"]), "question_end_in_clip_s": f"{qend:.2f}",
                     "clip_start_after_sync_s": "", "question_end_video_s": "", "answer_video_s": ""})
    tone(out / "sync.wav")
    write_rows(rows)
    print(f"wrote {len(rows)} clips, clips/sync.wav and clips/cluely_times.csv")

def play(player=None, clock=time.perf_counter, sleep=time.sleep):
    """Sync beep, then each clip with GAP seconds of silence between, logging start times relative to the beep."""
    if player is None:
        if sys.platform != "win32":
            raise SystemExit("play uses the Windows sound API; on other systems, play the clips yourself and log start times")
        import winsound
        player = lambda p: winsound.PlaySound(str(p), winsound.SND_FILENAME)   # blocks until the sound ends
    rows = read_rows(); out = ROOT / "clips"
    print("Start your screen recording and Cluely now. Playing the sync beep in 5 seconds.", flush=True); sleep(5)
    t0 = clock(); player(out / "sync.wav"); sleep(GAP / 2)
    for r in rows:
        r["clip_start_after_sync_s"] = f"{clock() - t0:.3f}"
        print(f"  playing {r['clip']}: {r['question'][:60]}", flush=True)
        player(out / r["clip"]); sleep(GAP)
    write_rows(rows)
    print("Done. Stop the recording, then run: python clips.py report --sync <video time of the sync beep, in seconds>")

def report(sync_video_s=None):
    rows = read_rows()
    if sync_video_s is not None:
        for r in rows:
            if r["clip_start_after_sync_s"]:
                r["question_end_video_s"] = f"{sync_video_s + float(r['clip_start_after_sync_s']) + float(r['question_end_in_clip_s']):.3f}"
        write_rows(rows)
        print("filled question_end_video_s; now enter answer_video_s for each clip from the recording, then run report again")
    waits = [float(r["answer_video_s"]) - float(r["question_end_video_s"]) for r in rows
             if r["answer_video_s"].strip() and r["question_end_video_s"].strip()]
    misses = sum(1 for r in rows if r["question_end_video_s"].strip() and not r["answer_video_s"].strip())
    if not waits:
        print("no answer times entered yet"); return None
    waits.sort()
    print(f"Cluely on {len(waits)} clips: question end to answer on screen, median {statistics.median(waits):.2f} s, "
          f"p90 {waits[min(len(waits)-1, int(0.9*len(waits)))]:.2f} s, range {waits[0]:.2f} to {waits[-1]:.2f} s; "
          f"{misses} clips with no answer")
    return waits

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make"); m.add_argument("--n", type=int, default=20); m.add_argument("--seed", type=int, default=3)
    m.add_argument("--meetings", type=int, default=4, help="limit audio downloads to the first N annotated meetings")
    sub.add_parser("play")
    r = sub.add_parser("report"); r.add_argument("--sync", type=float, help="video time of the sync beep, in seconds")
    a = ap.parse_args()
    if a.cmd == "make": make(a.n, a.seed, a.meetings)
    elif a.cmd == "play": play()
    else: report(a.sync)
