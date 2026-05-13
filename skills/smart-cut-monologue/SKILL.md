---
name: smart-cut-monologue
description: Smart-cut a monologue / talking-head video on macOS (Apple Silicon) by auto-transcribing with mlx-qwen3-asr, having Claude identify filler words (嗯/啊/呃/那个), repetitions, restarts, and long pauses, then showing the user an interactive review page (like 剪映 智能剪口播) to approve cuts before ffmpeg produces a clean video + synced SRT + clean transcript. Use this skill whenever the user wants to clean up a monologue, lecture, podcast-style video, or "口播视频" by removing filler / stumbles / dead air — even if they only say things like "帮我剪这个视频", "清理一下口癖", "smart cut", "自动剪辑口播", "像剪映那样剪口播", "remove filler from this video". Only works on Apple Silicon macOS.
---

# smart-cut-monologue

A skill for one-command cleanup of monologue videos. The user hands you a video, you hand back a tighter version of the same video with filler words, pauses, and restarts removed — always with a human-in-the-loop review step before any cutting happens.

## When this skill applies

Videos that are primarily one person talking to camera: course recordings, video essays, product demos, tutorial voice-overs, podcasts with video, 小红书/抖音 口播, YouTube monologues. Not suitable for: multi-speaker interviews (v1), music videos, scripted drama, anything where pauses are intentional pacing.

Hard requirements:
- macOS on Apple Silicon (mlx-qwen3-asr is MLX-only)
- Source video ≤ ~15 minutes in v1 (longer videos make the review UI sluggish and push Claude's analysis context)

If either is violated, say so and stop rather than half-working.

## Prerequisites & preflight

Before anything, run `scripts/preflight.sh`. It verifies / helps install:

1. Apple Silicon macOS (`uname -m` == `arm64`). If not, abort.
2. `ffmpeg` and `ffprobe` on PATH. If missing, ask the user before running `brew install ffmpeg`.
3. `uv` on PATH. If missing, ask the user before running `brew install uv` — don't silently install.
4. `mlx-qwen3-asr` installed as a `uv tool` (isolated env, no global Python pollution). If missing, ask the user before running `uv tool install mlx-qwen3-asr`. Warn the user that the first transcription will download the Qwen3 model weights (several GB).

All later scripts assume these prerequisites pass. If any step can't continue, report the exact failing check to the user — don't improvise a fallback.

## Workflow

The full flow is five stages. Walk through them in order. Each stage writes into a per-video work directory so you can resume if interrupted:

```
<video-dir>/smart-cut/<video-stem>/
  ├── transcript.json        # mlx-qwen3-asr word-level output
  ├── transcript.srt         # original subtitle
  ├── silence.json           # ffmpeg silencedetect ranges
  ├── suggestions.json       # Claude's proposed cuts
  ├── cuts.json              # user-confirmed cuts (written by review server)
  ├── <stem>_cut.mp4         # final output
  ├── <stem>_cut.srt
  └── <stem>_cut.txt
```

If a later stage's output already exists and the user hasn't asked to redo, skip that stage.

### Stage 1 — Preflight
Run `bash scripts/preflight.sh`. Stop on any failure.

### Stage 2 — Transcribe + silence detect
Run `bash scripts/transcribe.sh <video-path>`. This:
- Creates the work directory
- Runs `uv tool run mlx-qwen3-asr <video> -f json --timestamps -o <workdir>/` → `transcript.json`
- Also emits SRT for backup
- Runs `ffmpeg -af silencedetect=noise=-30dB:d=0.6` and parses stderr → `silence.json` (ranges of media-level silence ≥ 0.6s)

### Stage 3 — Analyze (Claude does this, not a script)
Read `transcript.json` and `silence.json`. Group words into phrase-level segments (split on sentence terminators and >400ms gaps). For each segment decide whether it should be a cut candidate and why. Write `suggestions.json` with this exact shape:

```json
{
  "video": "/abs/path/to/source.mp4",
  "duration": 423.12,
  "suggestions": [
    {
      "start": 12.340,
      "end": 12.780,
      "text": "嗯",
      "reason": "filler",
      "aggressiveness": "safe"
    }
  ]
}
```

Classification rules (use theory of mind — these are heuristics not laws):
- `filler` + `safe`: standalone 嗯 / 啊 / 呃 / 唉 / um / uh / er shorter than 800ms with speech before and after
- `pause` + `safe`: silence ≥ 1.2s confirmed by both ASR gap and `silence.json`; trim to leave 200ms of breathing room on each side
- `repeat` + `moderate`: speaker says the same phrase twice in a row — cut the earlier/incomplete one, keep the cleaner one. Be conservative: only mark when the repeated text ≥80% overlaps.
- `restart` + `moderate`: false starts cut off mid-phrase (common pattern: short fragment + self-correction). Look for "我想说... 我是说..." style.
- `padding` + `aggressive`: vague connectors like 然后那个/就是说/anyway, so, right — flag but do NOT default-check these; let the user decide.

Never propose cuts that would leave <300ms of kept audio between them (the review tool will merge such slivers, but prefer not to generate them).

### Stage 4 — Review (human-in-the-loop, required)
Run `python3 scripts/review_server.py <workdir>`. This:
- Starts a local HTTP server on a random port binding 127.0.0.1
- Opens `http://127.0.0.1:<port>/review.html` in the user's browser
- Serves the source video, `transcript.json`, and `suggestions.json` to the page
- Waits (blocking) for the page to POST to `/confirm` with the user's final cut selection → writes `cuts.json` → shuts itself down and exits

The page lets the user play the video, see every suggested segment with its reason tag, tick/untick each one, and click **Confirm & Cut**. Until they click, nothing is cut.

Tell the user plainly: "The review page is open in your browser. Tick the segments you want removed, then click **Confirm & Cut**. I'm waiting for you here."

Do not proceed until the script exits normally (exit code 0 = user confirmed; non-zero = user cancelled or closed the server).

### Stage 5 — Cut + sync
Run `python3 scripts/cut_video.py <workdir>`. This:
- Loads `cuts.json` and normalizes the delete intervals (sort, clamp, merge overlaps, drop <100ms slivers)
- Computes keep intervals
- Builds an ffmpeg filter_complex using `trim`/`atrim` + `setpts`/`asetpts` + `concat=v=1:a=1` — **precise frame-accurate re-encode**, not `-c copy` (keyframe snapping would be visible)
- Re-encodes once to H.264 + AAC with `+faststart`
- Adds 5ms audio fades at each concat boundary to avoid clicks
- Rewrites the SRT with new timestamps and produces a clean plain-text transcript
- Verifies output duration via `ffprobe` matches `original_duration − sum(deleted)` within 2 frames

Report the final paths and the time saved (e.g. "原 7:03 → 5:18, 删除 1:45").

## Failure modes to watch for

- First run after install stalls with no output: Qwen3 weights are downloading. Tell the user to wait — the download is cached for next time.
- Video has no audio track: `transcribe.sh` will fail loudly. Report and stop.
- User closes the review tab without confirming: `review_server.py` exits non-zero. Don't retry automatically — ask the user whether they want to re-open the review or abort.
- Output video duration off by more than 2 frames: treat as a bug, surface the mismatch to the user with both numbers.

## Notes for the model

- The whole point of this skill is that cut decisions pass through the user. Resist the urge to auto-confirm or to narrow-scope the review step "to save time".
- Don't re-invent stages. The scripts are the deterministic parts; your job is the judgment in stage 3 and coordination across stages.
- If something unexpected happens (weird ASR output, ffmpeg error), read the error and think about the root cause before retrying. Don't loop on the same command.

See `references/workflow.md` for deeper notes on ffmpeg filter choices, VFR handling, and SRT re-timing.
