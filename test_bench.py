import asyncio, io, json, pathlib, tempfile, zipfile
import ami, bench

NS = 'xmlns:nite="http://nite.sourceforge.net/"'
def words_xml(meeting, spk, toks):
    rows = []
    for i, (text, s, e, punc) in enumerate(toks):
        attrs = f'nite:id="{meeting}.{spk}.words{i}" starttime="{s}" endtime="{e}"' + (' punc="true"' if punc else "")
        rows.append(f"<w {attrs}>{text}</w>")
    return f'<nite:root {NS} nite:id="{meeting}.{spk}.words">' + '<vocalsound nite:id="x" starttime="0.1" endtime="0.2" type="cough"/>' + "".join(rows) + "</nite:root>"

DA_TYPES = f'''<nite:root {NS} nite:id="da-types"><da-type nite:id="ami_da_1" gloss="Inform"/>
  <da-type nite:id="ami_da_10" gloss="Elicit-Inform" name="el.inf"/><da-type nite:id="ami_da_2" gloss="Suggest"/></nite:root>'''

def dacts(meeting, spk, spans):
    rows = [f'<dact nite:id="d{i}"><nite:pointer role="da-aspect" href="da-types.xml#id({t})"/>'
            f'<nite:child href="{meeting}.{spk}.words.xml#id({meeting}.{spk}.words{a})..id({meeting}.{spk}.words{b})"/></dact>'
            for i, (t, a, b) in enumerate(spans)]
    return f'<nite:root {NS} nite:id="{meeting}.{spk}.dialog-act">' + "".join(rows) + "</nite:root>"

def fake_zip(path):
    a = [("so", 10.0, 10.2, 0), ("we", 10.2, 10.3, 0), ("agreed", 10.3, 10.7, 0), ("on", 10.7, 10.8, 0), ("rubber", 10.8, 11.2, 0), (".", 11.2, 11.2, 1)]
    b = [("what", 12.0, 12.2, 0), ("price", 12.2, 12.5, 0), ("did", 12.5, 12.6, 0), ("we", 12.6, 12.7, 0), ("settle", 12.7, 13.0, 0),
         ("on", 13.0, 13.1, 0), ("for", 13.1, 13.2, 0), ("the", 13.2, 13.3, 0), ("remote", 13.3, 13.7, 0), ("?", 13.7, 13.7, 1),
         ("okay", 14.5, 14.8, 0), ("yeah", 14.8, 15.0, 0), ("sure", 15.0, 15.2, 0), ("fine", 15.2, 15.4, 0)]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ontologies/da-types.xml", DA_TYPES)
        # A meeting with words but no dialogue acts sorts first, like EN2001 in the real corpus.
        z.writestr("words/EN2001a.A.words.xml", words_xml("EN2001a", "A", [("hello", 1.0, 1.2, 0)]))
        z.writestr("words/ES2002a.A.words.xml", words_xml("ES2002a", "A", a))
        z.writestr("words/ES2002a.B.words.xml", words_xml("ES2002a", "B", b))
        z.writestr("dialogueActs/ES2002a.A.dialog-act.xml", dacts("ES2002a", "A", [("ami_da_1", 0, 5)]))
        # The question range ends on the "?" punctuation token, as real AMI ranges often do; the second act is not a question.
        z.writestr("dialogueActs/ES2002a.B.dialog-act.xml", dacts("ES2002a", "B", [("ami_da_10", 0, 9), ("ami_da_1", 10, 13)]))

# 1. Parsing: one Elicit-Inform question, punctuation-ended range resolved, context from the other speaker.
tmp = pathlib.Path(tempfile.mkdtemp()); (tmp / "data").mkdir()
fake_zip(tmp / "data" / "ami_public_manual_1.6.2.zip"); ami.ROOT = tmp
qs = ami.load()
assert len(qs) == 1, qs
assert len(ami.load(max_meetings=1)) == 1, "an unannotated meeting that sorts first must be skipped"
q = qs[0]
assert " ".join(t for _, _, t in q["words"]) == "what price did we settle on for the remote", q["words"]
assert q["end"] == 13.7 and "Speaker A: so we agreed on rubber" in q["context"], q["context"]
assert ami.question_types(DA_TYPES.encode()) == {"ami_da_10"}

# 2. The completeness check fires once the content is in, never on a trailing function word.
words = [t for _, _, t in q["words"]]
assert not bench.looks_complete(words[:3]) and not bench.looks_complete(words[:8])   # "...for the"
assert bench.looks_complete(words[:5]) and bench.looks_complete(words)
assert bench.launch_points(q)[-1] == len(words) - 1

# 3. Timing model: baseline pays the endpoint; speculative overlaps generation with the rest of the question.
calls = {"full": {"ttft": 0.6, "text": "x"}, **{i: {"ttft": 0.6, "text": "x"} for i in bench.launch_points(q)}}
base, spec, used, _, _ = bench.timeline(q, calls)
assert abs(base - (0.8 + 0.6)) < 1e-9, base
# The early launch at word 4 ("settle", ends 13.0) is ready at 13.0 + 0.3 + 0.6 = 13.9 <= 13.7 + 0.25? No: 13.9 > 13.95 is false, so it is ready.
assert used == 4 and abs(spec - 0.25) < 1e-9, (spec, used)
slow = {"full": {"ttft": 2.0, "text": "x"}, **{i: {"ttft": 2.0, "text": "x"} for i in bench.launch_points(q)}}
b2, s2, u2, _, _ = bench.timeline(q, slow)                                          # nothing ready in time: latest launch streams
assert u2 == len(words) - 1 and abs(s2 - (0.30 + 2.0)) < 1e-9 and abs(b2 - 2.8) < 1e-9, (s2, b2)
b3, s3, _, _, _ = bench.timeline(q, slow, endpoint=0.2, stt=0.6)                   # slow transcriber, short endpoint
assert abs(s3 - b3) < 1e-9 and abs(b3 - 2.2) < 1e-9, (s3, b3)                # never slower than the baseline
assert bench.timeline({"words": [(0, 0.2, "okay"), (0.2, 0.4, "so"), (0.4, 0.5, "um"), (0.5, 0.6, "the")], "end": 0.6},
                      {"full": {"ttft": 0.5, "text": "x"}})[1] == 0.25 + 0.5
# A 0.9 s pause after "what price did we settle" (an answer is ready by then) flags the speculative path only;
# a 3 s pause there also flags the baseline.
def paused(gap):
    ws = [(12.0, 12.2, "what"), (12.2, 12.5, "price"), (12.5, 12.6, "did"), (12.6, 12.7, "we"), (12.7, 13.0, "settle"),
          (13.0 + gap, 13.1 + gap, "on"), (13.1 + gap, 13.2 + gap, "for"), (13.2 + gap, 13.3 + gap, "the"), (13.3 + gap, 13.7 + gap, "remote")]
    pq = {"words": ws, "end": ws[-1][1]}
    c = {"full": {"ttft": 0.5, "text": "x"}, **{i: {"ttft": 0.5, "text": "x"} for i in bench.launch_points(pq)}}
    return bench.timeline(pq, c)
assert paused(0.9)[3:] == (False, True), paused(0.9)
assert paused(3.0)[3:] == (True, True), paused(3.0)
assert paused(0.0)[3:] == (False, False), paused(0.0)      # nothing launched: soft pause then ask

# 4. End to end with a fake model: calls cached, judge controls run, vague flag set, sweep computed, report written.
async def fake_ask(model, ctx, qtext):
    return {"ttft": 0.5 + 0.01 * len(qtext.split()), "text": "answer to " + qtext}
async def fake_judge(model, q, a, b):
    return a == b or a.startswith("answer to what price")      # same text, or an early answer to this question
async def fake_vague(model, q):
    return "think" in q
bench.ask, bench.judge, bench.vague_check, bench.ROOT = fake_ask, fake_judge, fake_vague, tmp
import os; os.environ["OPENAI_API_KEY"] = "test"
rows, ctl, sweep = asyncio.run(bench.run(5, "fake", "fake", 1, 2))
assert "spec_pre" in rows[0] and len(sweep[0][2][0]) == 6
assert len(rows) == 1 and rows[0]["spec"] < rows[0]["base"] and rows[0]["early"] and rows[0]["same"], rows
assert ctl == [(True, False)] or ctl == [(True, True)], ctl          # one question: identical pair passes the control
assert len(sweep) == 6 and all(len(sub) == 1 for _, _, sub in sweep)
rec = json.load(open(tmp / "results" / "calls.json")).popitem()[1]
assert rec["ctl_pos"] is True and "vague" in rec and any(k.startswith("same") for k in rec), rec.keys()
bench.report((rows, ctl, sweep), "fake", "fake")
txt = sorted((tmp / "results").glob("2*.md"))[-1].read_text()
assert "Judge controls" in txt and "Sensitivity to the assumptions" in txt and "Premature answers" in txt and "| 500 ms | 300 ms |" in txt

# 5. Clip cutter: the question ends exactly where the CSV says, after the beep and lead-in.
import wave, struct, clips
rate = 16000; src = io.BytesIO()
with wave.open(src, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", i % 1000) for i in range(rate * 40)))   # 40 s of audio
dst = tmp / "c.wav"
qend = clips.cut(src.getvalue(), 25.0, dst)
assert abs(qend - (clips.BEEP + clips.LEAD)) < 1e-9
with wave.open(str(dst)) as w:
    assert abs(w.getnframes() / rate - (clips.BEEP + clips.LEAD + clips.TAIL)) < 0.01
    w.setpos(int(qend * rate)); first = struct.unpack("<h", w.readframes(1))[0]
assert first == int(25.0 * rate) % 1000, first                                   # same sample as 25.0 s in the source
near = clips.cut(src.getvalue(), 5.0, tmp / "n.wav"); assert abs(near - (clips.BEEP + 5.0)) < 1e-9   # short lead near the start
import csv
with open(tmp / "t.csv", "w", newline="") as fh:
    wr = csv.writer(fh); wr.writerow(["clip", "meeting", "question", "question_end_s", "answer_appeared_s"])
    wr.writerow(["q01.wav", "m", "x", "12.30", "15.30"]); wr.writerow(["q02.wav", "m", "y", "12.30", ""])
clips.report(tmp / "t.csv")
print("all tests passed")
