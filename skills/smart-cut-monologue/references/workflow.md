# smart-cut-monologue — workflow notes

Deeper notes for when something goes wrong or you need to extend the skill.

## Why a localhost server, not a static file

A static `file://` HTML page can't write back to disk, and Chrome blocks
`file://` video playback without flags. The review server solves both:
the page fetches `/data.json` and `/video`, and POSTs cuts back to `/confirm`.
The server then writes `cuts.json` and shuts down — no file-drag handoff needed.

Port is chosen at random on 127.0.0.1. If the user's firewall blocks
localhost (uncommon on macOS), the browser will fail to load; fall back
to printing the URL and letting the user paste it.

## Why precise re-encode, not `-c copy`

`ffmpeg -ss X -to Y -c copy` snaps to the nearest keyframe. For an H.264
video with 2–10s keyframe interval, cuts can be off by seconds — audible
and visible. The filter pipeline in `cut_video.py`:

```
[0:v]trim=start=X:end=Y,setpts=PTS-STARTPTS[vN]
[0:a]atrim=start=X:end=Y,asetpts=PTS-STARTPTS,afade...[aN]
...concat=n=K:v=1:a=1[vout][aout]
```

decodes the whole video and re-encodes once. That's slower but
frame-accurate, and audio fades avoid clicks at boundaries.

## VFR inputs

Monologue videos are usually CFR, but phone recordings can be VFR. If
cuts drift on VFR source, add `-fps_mode cfr -r 30` before the
`filter_complex` flag (or re-mux the source to CFR with `-vsync cfr`
first). `cut_video.py` doesn't do this automatically — it prints a
warning if final duration drifts >100ms, which is the main symptom.

## SRT re-timing

`cut_video.py::cut_srt` does two passes:
1. Drop any cue entirely inside a delete range
2. Shift remaining cues left by the cumulative deleted time before
   their start

The shift function walks through all delete ranges, accumulating total
drop time up to `t`. This handles the SRT correctly as long as cue
boundaries don't straddle delete ranges. In practice mlx-qwen3-asr's
SRT cues line up closely enough that straddling is rare; if you see
broken timestamps, regenerate SRT from the word-level JSON after cutting.

## Extending: multi-speaker

v1 is monologue-only. For interviews:
- Run mlx-qwen3-asr with `--diarize --num-speakers N` → transcript has
  speaker tags
- Classification rules need speaker-aware context (someone else's "嗯"
  is backchannel and must stay)
- Review UI should show speaker tags — current UI doesn't

## Extending: >15 min

For a 30-min lecture:
- Split transcript by topic / long pause
- Analyze per-chunk, merge suggestions
- Review UI needs pagination (the current design loads all segments
  into the DOM — fine for <500 items, sluggish past ~2000)
