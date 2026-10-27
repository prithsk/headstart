# headstart

How long does a meeting copilot make you wait after someone asks a question, and how much of that wait can be hidden?

Cluely quotes a 300 ms transcription response time. A copilot still cannot answer until it decides the question is over,
which in a standard voice pipeline means waiting for a stretch of silence, and the model then needs time to produce its
first token. Neither delay is in the 300 ms figure.

headstart replays real meeting questions from the AMI Meeting Corpus word by word, at the times the words were actually
spoken, and compares two policies with real model latencies:

1. **Baseline.** Wait for endpoint silence, then ask the model.
2. **Speculative.** Start an answer as soon as the question's content looks complete, while the speaker is still
   finishing, and show the latest answer that is ready after a short pause.

A second model judges whether each early answer is as useful as the answer to the full question, so the speedup comes
with its cost attached: how often the early answer would have to be revised on screen.

## Results

<!-- results:start -->
_Run it to populate._
<!-- results:end -->

## Checks that keep the result honest

1. **Judge controls.** On up to 100 questions, the judge also compares an answer with itself, which it must rate the
   same, and with an answer to a different question, which it must not. The report prints both rates and warns if the
   judge rates mismatched answers the same more than 15% of the time, since then the quality numbers mean nothing.
2. **Vague questions reported separately.** Questions like "what do you think?" make any two answers look alike, so a
   second model flags them and the report gives answer quality on specific questions alone.
3. **Sensitivity sweep.** Endpoint silence and transcription delay are assumptions, so the report recomputes the result
   for endpoints of 500, 800 and 1000 ms and transcription delays of 300 and 600 ms. Speculation pays off only when
   transcription is fast; the sweep shows how much.
4. **Premature answers counted.** The replay knows where each question ends; a live copilot does not. Every pause
   inside a question long enough to trigger a policy, with an answer on screen before the speaker resumes, counts
   as a premature answer for that policy, so a shorter pause threshold cannot buy speed for free.
5. **Never slower than the baseline.** The speculative copilot also fires on the endpoint, so the comparison can only
   show a real gain or a tie.
6. **Cluely itself.** `clips.py` and [CALIBRATE.md](CALIBRATE.md) time Cluely on 20 of the same questions from a screen
   recording, and `bench.py --from-clips` runs headstart on exactly those questions.

## Data

[AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/) manual annotations, CC BY 4.0: 100 hours of meetings with
word-level timings from forced alignment and dialogue acts. Questions are Elicit-Inform dialogue acts with at least
four words. The context for each question is the transcript of the 90 seconds before it. The annotation zip downloads
automatically on first run, and `clips.py` downloads meeting audio only for the meetings it cuts clips from.

## Timing model

- A word reaches the copilot 300 ms after it ends, Cluely's quoted transcription figure.
- The baseline sends the question after 800 ms of silence, a typical endpointing setting.
- The speculative path checks every new word with a free heuristic: at least four words, a question cue, and no
  trailing function word like "the" or "for". Each time it fires, a new answer starts. After a 250 ms pause it shows the
  latest answer that has a first token; otherwise it shows whichever comes first, the latest launch or the endpoint answer.
- Time to first token is measured live from the model for every launch.

## Run it

```
cp .env.example .env            # add OPENAI_API_KEY
python -m pip install -r requirements.txt
python test_bench.py
python bench.py --inspect       # check that questions parse correctly
python bench.py --n 200
python clips.py make --n 20     # optional: time Cluely itself on the same questions, see CALIBRATE.md
```

Every model call is cached in results/calls.json, so a rerun only does what is missing.

## Honest limits

- AMI word timings come from forced alignment of headset audio, and the transcription delay is a fixed 300 ms, so this
  models a good streaming transcriber in a quiet room.
- The endpoint silence is a typical value, not Cluely's internal setting, which is not public. Both policies run under
  the same assumptions and the sweep covers a range, but only CALIBRATE.md measures Cluely's product.
- Speculation costs extra model calls, reported in the results.
- Preemptive generation exists in voice-agent frameworks such as LiveKit Agents. The contribution here is measuring it
  for a meeting copilot on real meeting questions, with an answer-quality check.
- AMI meetings are scripted design meetings with mostly non-native English speakers.

## Scope

This measures answer latency for meeting assistance. It does not touch, test or improve hiding an assistant from screen
sharing or any interview or exam use.

## License

MIT. AMI Meeting Corpus data is CC BY 4.0, by the AMI Consortium.
