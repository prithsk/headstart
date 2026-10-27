# Timing Cluely itself

The benchmark compares two policies under the same assumptions. This kit measures Cluely on the same questions, so
the comparison has a real anchor.

1. `python clips.py --n 20` cuts 20 AMI question clips into `clips/`. Each starts with a short beep, and the question
   ends at the time listed in `clips/cluely_times.csv` under `question_end_s`, measured from the start of the clip.
2. Start Cluely in meeting mode and start a screen recording that captures system audio (OBS works).
3. Play each clip through your speakers or a virtual audio cable so Cluely hears it.
4. In the recording, find the beep for each clip, then the first frame where Cluely shows an answer to that question.
   Enter the time between them, in seconds, under `answer_appeared_s`.
5. `python clips.py --report clips/cluely_times.csv` prints Cluely's median and p90 wait.

Note which Cluely plan and settings you used, and the date, since product behavior changes. Clips that Cluely never
answers count as misses; leave `answer_appeared_s` blank and report the miss count alongside the timings.
