# Timing Cluely on the same questions

The benchmark compares two policies under the same assumptions. This kit times Cluely itself on 20 of the same AMI
questions, so the comparison has a real anchor. It takes about an hour, most of it marking answer times in the video.

## 1. Cut the clips

```
python clips.py make --n 20
```

This downloads the meeting audio for up to 4 meetings (roughly 50 to 100 MB each, once), cuts 20 question clips into
`clips/`, writes `clips/sync.wav`, and lists every clip in `clips/cluely_times.csv`.

## 2. Check how Cluely answers, on one clip first

Open Cluely in its meeting mode with system audio capture on, and play `clips/q01.wav` once. Note whether Cluely shows
an answer to the question by itself. If it only answers when you press a key or click, stop here: timing would then
include your reaction time, and the protocol needs a change.

## 3. Set up OBS

- Add a Display Capture source for the screen where Cluely's window appears.
- Make sure Desktop Audio is recorded, so the sync beep is in the recording.
- Set the frame rate to 60 in Settings, Video. At 60 frames per second each frame is about 17 ms, which is the precision
  of every timing.

## 4. Record

1. Start Cluely and start the OBS recording.
2. Run `python clips.py play`. It waits 5 seconds, plays a 1-second sync beep, then plays every clip with 14 seconds of
   silence between them, so each answer has time to appear. It logs when every clip started, relative to the beep.
3. When it prints Done, stop the recording.

Leave the computer alone while it plays. Other sounds or windows over Cluely make marking harder.

## 5. Mark the times

1. Open the recording in a player that steps frame by frame and shows the time in seconds; Shotcut, Kdenlive and VLC
   with its frame step all work. Find the frame where the sync beep starts, and note its time, for example 7.412.
2. Run `python clips.py report --sync 7.412` with your value. This fills `question_end_video_s` for every clip in
   `clips/cluely_times.csv`: the video time each question ended.
3. For each clip, find the first frame where Cluely shows an answer to that question, and enter its video time under
   `answer_video_s`. Leave it blank if Cluely never answered that question.
4. Run `python clips.py report` to print Cluely's median, p90 and range, plus the count of unanswered questions.

## 6. Run headstart on the same 20 questions

```
python bench.py --from-clips clips/cluely_times.csv
```

This writes `results/clips-<date>.md`, so it does not overwrite the main 200-question result.

## What the comparison means

Cluely and headstart are different systems with different models, transcription and infrastructure. The comparison
shows how long a person waits on the same questions with each, not why. Record the Cluely plan, settings, app
version and date next to the numbers, since product behavior changes.
