"""headstart: speculative answering for live meeting questions, measured on the AMI Meeting Corpus.

A meeting copilot can answer only after the question ends. Cluely quotes 300 ms for transcription, but the wait
also includes endpointing (deciding the speaker is done) and the model's time to first token. This replays real
questions word by word at their recorded times and compares:
  baseline     - send the question after an endpoint silence, like a standard voice pipeline
  speculative  - start the answer as soon as the question's content looks complete, show it after a short pause
Answer speed uses real time-to-first-token from the model. Answer quality is judged by a second model.
"""
import argparse, asyncio, datetime as dt, json, os, pathlib, random, re, statistics, time

ROOT = pathlib.Path(__file__).parent
STT_DELAY = 0.30   # Cluely's quoted transcription response time
ENDPOINT = 0.80    # silence a standard pipeline waits for before treating the question as finished
SOFT = 0.25        # pause the speculative path waits before showing an answer it already has
FUNCTION = set("""the a an of to and or but with for in on at about from by as is are was were be do does did can could
would will should have has had i you we they he she it my your our their this that these those so then um uh like
just if than um-hmm mm-hmm uh-huh""".split())
CUES = set("what how why when where who which whose do does did can could would should is are was were will have has any".split())

def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def looks_complete(words):
    """Cheap, model-free check that a partial question carries its content: enough words, ends on a content word,
    and contains a question cue. Runs on every new word, so it must cost nothing."""
    toks = [w.lower().strip("?.,!") for w in words]
    content = [t for t in toks if t not in FUNCTION]
    return len(toks) >= 4 and len(content) >= 2 and toks[-1] not in FUNCTION and any(t in CUES for t in toks)

def launch_points(q):
    """Word indices where the speculative path would start (or restart) an answer."""
    words = [t for _, _, t in q["words"]]
    return [i for i in range(len(words)) if looks_complete(words[: i + 1])]

PROMPT = ("You are a meeting copilot. Using the recent meeting transcript, give the person a short, useful answer to the "
          "question just asked, in at most two sentences.\n\nTranscript:\n{ctx}\n\nQuestion just asked: {q}")

_client = None
def client():
    """Create the API client on first use, whichever call comes first (all answers may be cached)."""
    global _client
    if _client is None:
        from openai import AsyncOpenAI; _client = AsyncOpenAI(max_retries=6)
    return _client
async def ask(model, ctx, qtext):
    """Stream an answer and measure real time to first token."""
    global _client
    if _client is None:
        from openai import AsyncOpenAI; _client = AsyncOpenAI(max_retries=6)
    t0 = time.perf_counter(); first = None; parts = []
    stream = await _client.chat.completions.create(model=model, temperature=0, stream=True, max_tokens=120,
                                                   messages=[{"role": "user", "content": PROMPT.format(ctx=ctx or "(none)", q=qtext)}])
    async for ch in stream:
        d = ch.choices[0].delta.content if ch.choices else None
        if d:
            if first is None: first = time.perf_counter() - t0
            parts.append(d)
    return {"ttft": first if first is not None else time.perf_counter() - t0, "text": "".join(parts).strip()}

JUDGE = ('A person in a meeting asked: "{q}"\nAnswer A: {a}\nAnswer B: {b}\n'
         'Answer B was written from the full question. Is answer A as useful an answer to the full question as B? '
         'Reply as JSON: {{"same": true or false}}')
async def judge(model, q, a, b):
    r = await client().chat.completions.create(model=model, temperature=0, response_format={"type": "json_object"},
                                              messages=[{"role": "user", "content": JUDGE.format(q=q, a=a, b=b)}])
    try: return bool(json.loads(r.choices[0].message.content).get("same"))
    except Exception: return False

def timeline(q, calls, endpoint=ENDPOINT, soft=SOFT, stt=STT_DELAY, policy="newest"):
    """Seconds from question end to the first answer on screen, for both paths, given measured calls.
    calls: {"full": call on the whole question, i: call launched at word i}.
    The speculative path shows the latest answer that is ready by the soft pause; if none is ready, it shows
    whichever comes first, the latest launch or the endpoint answer.
    The replay knows where each question truly ends; a live system does not. So every pause inside the question
    that is long enough to trigger a policy, with an answer on screen before the speaker resumes, counts as a
    premature answer for that policy.
    policy "newest" shows only the answer to the newest question text, waiting for it to stream if needed.
    policy "any" (ablation) shows whatever answer is ready first, even one written from an older, shorter version.
    Returns (baseline, speculative, word index used, baseline premature, speculative premature)."""
    words, end = q["words"], q["end"]
    ttft_full = calls["full"]["ttft"]
    base = endpoint + ttft_full
    pts = [i for i in launch_points(q) if i in calls]
    launch = {i: words[i][1] + stt for i in pts}
    base_pre = spec_pre = False
    for k in range(len(words) - 1):
        pause_start, resume = words[k][1], words[k + 1][0]
        if pause_start + endpoint + ttft_full < resume:
            base_pre = True
        if resume - pause_start >= soft:
            so_far = [i for i in pts if i <= k]
            if policy == "newest":
                ready = so_far and launch[so_far[-1]] + calls[so_far[-1]]["ttft"] < resume
            else:
                ready = any(launch[i] + calls[i]["ttft"] < resume for i in so_far)
            if ready or (not so_far and pause_start + soft + ttft_full < resume):
                spec_pre = True
    show = end + soft
    if not pts:                                       # nothing looked complete: ask at the soft pause
        return base, min(soft + ttft_full, base), None, base_pre, spec_pre
    if policy == "any":
        ready = [i for i in pts if launch[i] + calls[i]["ttft"] <= show]
        if ready:
            return base, soft, ready[-1], base_pre, spec_pre
    i = pts[-1]
    spec = max(show, launch[i] + calls[i]["ttft"]) - end
    # The speculative copilot also has the final transcript at the endpoint, so it can always fire there too;
    # it shows whichever answer is ready first and is never slower than the baseline.
    return base, min(spec, base), (i if spec <= base else len(words) - 1), base_pre, spec_pre

async def run(n, model, judge_model, seed, workers):
    load_env()
    import ami
    qs = ami.load(max_meetings=None)
    random.Random(seed).shuffle(qs); qs = qs[:n]
    cache = ROOT / "results" / "calls.json"; cache.parent.mkdir(exist_ok=True)
    calls = json.loads(cache.read_text()) if cache.exists() else {}
    sem = asyncio.Semaphore(workers)
    async def one(k, q):
        key = f"{model}|{q['meeting']}|{q['speaker']}|{q['end']:.2f}"
        if key in calls: return
        words = [t for _, _, t in q["words"]]; rec = {}
        async with sem:
            rec["full"] = await ask(model, q["context"], " ".join(words))
            for i in launch_points(q):
                rec[str(i)] = rec["full"] if i == len(words) - 1 else await ask(model, q["context"], " ".join(words[: i + 1]))
        calls[key] = rec
        if k % 10 == 0: cache.write_text(json.dumps(calls)); print(f"  {k}/{len(qs)} questions", flush=True)
    await asyncio.gather(*(one(k, q) for k, q in enumerate(qs, 1)))
    cache.write_text(json.dumps(calls))
    rows, todo = [], []
    for q in qs:
        key = f"{model}|{q['meeting']}|{q['speaker']}|{q['end']:.2f}"; rec = calls[key]
        c = {"full": rec["full"], **{int(k): v for k, v in rec.items() if k.isdigit()}}
        for pol in ("newest", "any"):
            base, spec, used, bpre, spre = timeline(q, c, policy=pol)
            early = used is not None and used != len(q["words"]) - 1
            if early and f"same{used}" not in rec and (key, used) not in {(t[0], t[1]) for t in todo}:
                todo.append((key, used, q))
            rows.append((pol, key, q, base, spec, used, early, bpre, spre))
    async def check(key, used, q):                    # judge only answers actually shown before the full question
        async with sem:
            rec = calls[key]; words = " ".join(t for _, _, t in q["words"])
            rec[f"same{used}"] = await judge(judge_model, words, rec[str(used)]["text"], rec["full"]["text"])
    await asyncio.gather(*(check(*t) for t in todo))

    # Judge controls: identical answers must read as the same, an answer to a different question must not.
    # Without these, a lenient judge would make every early answer look fine.
    main = [r[1:] for r in rows if r[0] == "newest"]
    keys = [k for k, *_ in main]
    rnd = random.Random(seed + 1)
    async def control(i, key, q):
        rec = calls[key]
        if "ctl_pos" in rec and "ctl_neg" in rec:
            return
        other = calls[keys[(i + 1 + rnd.randrange(len(keys) - 1)) % len(keys)]]["full"]["text"] if len(keys) > 1 else "I am not sure."
        async with sem:
            words = " ".join(t for _, _, t in q["words"])
            rec["ctl_pos"] = await judge(judge_model, words, rec["full"]["text"], rec["full"]["text"])
            rec["ctl_neg"] = await judge(judge_model, words, other, rec["full"]["text"])
    await asyncio.gather(*(control(i, k, q) for i, (k, q, *_) in enumerate(main[:100])))

    # Vague questions ("what do you think?") make any two answers look alike, so flag them.
    async def vague(key, q):
        rec = calls[key]
        if "vague" in rec:
            return
        async with sem:
            rec["vague"] = await vague_check(judge_model, " ".join(t for _, _, t in q["words"]))
    await asyncio.gather(*(vague(k, q) for k, q, *_ in main))

    # Sensitivity: the endpoint silence and transcription delay are assumptions, so sweep them.
    sweep = []
    for endpoint in (0.5, 0.8, 1.0):
        for stt in (0.3, 0.6):
            sub, need = [], []
            for key, q, *_ in main:
                rec = calls[key]
                c = {"full": rec["full"], **{int(k): v for k, v in rec.items() if k.isdigit()}}
                b, sp, u, bp, spp = timeline(q, c, endpoint=endpoint, stt=stt)
                e = u is not None and u != len(q["words"]) - 1
                if e and f"same{u}" not in rec: need.append((key, u, q))
                sub.append((key, b, sp, u, e, bp, spp))
            await asyncio.gather(*(check(*t) for t in need))
            sweep.append((endpoint, stt, [(b, sp, e, calls[k].get(f"same{u}", True) if e else True, bp, spp) for k, b, sp, u, e, bp, spp in sub]))
    cache.write_text(json.dumps(calls))
    def pack(pol):
        return [{"base": b, "spec": sp, "early": e, "same": calls[k].get(f"same{u}", True) if e else True,
                 "extra": len(launch_points(q)), "vague": calls[k].get("vague", False), "base_pre": bp, "spec_pre": spp}
                for p_, k, q, b, sp, u, e, bp, spp in rows if p_ == pol]
    ctl = [(calls[k]["ctl_pos"], calls[k]["ctl_neg"]) for k, *_ in main if "ctl_pos" in calls[k]]
    return pack("newest"), ctl, sweep, pack("any")

async def vague_check(model, q):
    r = await client().chat.completions.create(model=model, temperature=0, response_format={"type": "json_object"},
                                              messages=[{"role": "user", "content": VAGUE.format(q=q)}])
    try: return bool(json.loads(r.choices[0].message.content).get("vague"))
    except Exception: return False

VAGUE = ('A person in a meeting asked: "{q}"\nIs this a vague or open-ended question (for example "what do you think?" '
         'or "any ideas?") where many different answers would be equally acceptable? Reply as JSON: {{"vague": true or false}}')

def report(result, model, judge_model):
    rows, ctl, sweep, any_rows = result
    p = lambda v, x: sorted(v)[min(len(v) - 1, int(x * len(v)))]
    med = statistics.median
    b = [r["base"] for r in rows]; s = [r["spec"] for r in rows]
    def quality(sub):
        early = [r for r in sub if r["early"]]; ok = sum(r["same"] for r in early)
        return len(early), ok
    n_e, ok = quality(rows)
    spec_rows = [r for r in rows if not r["vague"]]; n_es, ok_s = quality(spec_rows)
    pos = sum(c[0] for c in ctl); neg = sum(c[1] for c in ctl)
    md = [f"# headstart on AMI meeting questions, {dt.date.today()}", "",
          f"{len(rows)} real questions (Elicit-Inform dialogue acts) from the AMI Meeting Corpus, replayed at their recorded word times. "
          f"Answers from `{model}` with real time to first token; equivalence judged by `{judge_model}`. "
          f"Main setting: transcription delay {STT_DELAY*1000:.0f} ms (Cluely's quoted figure), endpoint silence {ENDPOINT*1000:.0f} ms, soft pause {SOFT*1000:.0f} ms.", "",
          "| Question end to first answer on screen | p50 | p90 |", "|---|---|---|",
          f"| Baseline: wait for endpoint silence, then ask | {med(b):.2f} s | {p(b, .9):.2f} s |",
          f"| Speculative: start answers while the question is still being asked, show the newest | {med(s):.2f} s | {p(s, .9):.2f} s |",
          f"| Ablation: same, but show any ready answer, even to an older version of the question | {med([r['spec'] for r in any_rows]):.2f} s | {p([r['spec'] for r in any_rows], .9):.2f} s |", "",
          f"**Median wait cut from {med(b):.2f} s to {med(s):.2f} s, {med(b)/max(med(s),1e-9):.1f}x faster.**", "",
          f"Premature answers, shown during a pause before the speaker finished the question: baseline {100*sum(r['base_pre'] for r in rows)/len(rows):.0f}% of questions, "
          f"speculative {100*sum(r['spec_pre'] for r in rows)/len(rows):.0f}%. This is the price of answering on shorter pauses.", "",
          "## Answer quality", "",
          (lambda e, ok, e2, ok2: f"Early answers, shown before the newest full-question answer existed: main rule {e2} of {len(rows)}, "
                                  f"{ok2} ({100*ok2/max(e2,1):.0f}%) rated as useful as the full-question answer. "
                                  f"Ablation rule {e} of {len(any_rows)}, {ok} ({100*ok/max(e,1):.0f}%) rated as useful.")(
              sum(r["early"] for r in any_rows), sum(r["early"] and r["same"] for r in any_rows),
              sum(r["early"] for r in rows), sum(r["early"] and r["same"] for r in rows)), "",
          f"Answers shown before the full question was transcribed: {n_e} of {len(rows)}. "
          + (f"The judge rated {ok} ({100*ok/n_e:.0f}%) as useful as the full-question answer; the rest would be revised on screen." if n_e else ""),
          f"On the {len(spec_rows)} specific questions (excluding {len(rows)-len(spec_rows)} the judge flagged as vague, like \"what do you think?\"): "
          + (f"{ok_s} of {n_es} early answers ({100*ok_s/n_es:.0f}%) rated as useful." if n_es else "no early answers."), "",
          f"Judge controls on {len(ctl)} questions: it rated identical answers the same {100*pos/max(len(ctl),1):.0f}% of the time, "
          f"and an answer to a different question the same {100*neg/max(len(ctl),1):.0f}% of the time."
          + (" **The mismatched rate is high enough that the quality numbers above should not be trusted.**" if ctl and neg / len(ctl) > 0.15 else ""),
          f"Model calls per question: {1 + statistics.mean(r['extra'] for r in rows):.1f} on average, against 1 for the baseline.", "",
          "## Sensitivity to the assumptions", "",
          "| Endpoint silence | Transcription delay | Baseline p50 | Speculative p50 | Speedup | Early answers rated as useful | Premature, baseline / speculative |", "|---|---|---|---|---|---|---|"]
    for endpoint, stt, sub in sweep:
        bb = [x[0] for x in sub]; ss = [x[1] for x in sub]; ee = [x for x in sub if x[2]]
        q = f"{100*sum(x[3] for x in ee)/len(ee):.0f}% of {len(ee)}" if ee else "none early"
        pb = 100 * sum(x[4] for x in sub) / len(sub); ps = 100 * sum(x[5] for x in sub) / len(sub)
        md.append(f"| {endpoint*1000:.0f} ms | {stt*1000:.0f} ms | {med(bb):.2f} s | {med(ss):.2f} s | {med(bb)/max(med(ss),1e-9):.1f}x | {q} | {pb:.0f}% / {ps:.0f}% |")
    md += ["", "## Honest limits", "",
           "- This compares two policies under the same assumptions. It does not measure Cluely's product; CALIBRATE.md describes how to time Cluely itself on the same questions.",
           "- Word timings come from forced alignment of headset audio, and transcription delay is modeled as fixed, so this assumes a good streaming transcriber in a quiet room.",
           "- Time to first token is measured one call at a time. Concurrent speculative calls in a real app could slow each other.",
           "- An early answer rated worse is replaced when the full-question answer arrives, so it costs a visible revision, not a wrong final answer.",
           "- Preemptive generation exists in voice-agent frameworks such as LiveKit Agents. The contribution here is measuring it for a meeting copilot on real meeting questions, with an answer-quality check.",
           "- AMI meetings are scripted design meetings with mostly non-native English speakers."]
    out = "\n".join(md); (ROOT / "results" / f"{dt.date.today()}.md").write_text(out, encoding="utf-8"); print(out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200); ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--judge", default="gpt-4o-mini"); ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--inspect", action="store_true", help="print a few parsed questions and stop, to check the data")
    a = ap.parse_args()
    if a.inspect:
        import ami
        qs = ami.load(max_meetings=3)
        print(f"{len(qs)} questions in the first 3 meetings")
        for q in qs[:5]:
            print(f"- [{q['meeting']} {q['speaker']} ends {q['end']:.2f}s] {' '.join(t for _, _, t in q['words'])}  | launch at words {launch_points(q)}")
    else:
        report(asyncio.run(run(a.n, a.model, a.judge, a.seed, a.workers)), a.model, a.judge)
