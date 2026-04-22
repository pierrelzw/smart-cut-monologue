# smart-cut-monologue

Claude Code skill for one-command cleanup of monologue / talking-head videos on macOS (Apple Silicon).

Hand Claude a video; get back a tighter version with filler words (嗯/啊/呃/那个), repetitions, restarts, and long pauses removed — plus a synced SRT and clean transcript. A human-in-the-loop review step (剪映-style transcript-inline UI) always runs before any cutting.

## Requirements

- macOS on Apple Silicon (MLX-only)
- `ffmpeg`, `ffprobe`, `uv` (the skill's preflight helps install what's missing)
- `mlx-qwen3-asr` installed as a `uv tool`
- First run downloads the Qwen3 ASR + ForcedAligner weights (several GB, cached thereafter)

## Install

```bash
/plugin marketplace add pierrelzw/zhiwei_skills
/plugin install smart-cut-monologue@pierrelzw
```

Then just ask Claude things like "帮我剪这个视频 /path/to/video.mp4" or "remove filler from this video".

## Workflow

1. **Preflight** — checks Apple Silicon, ffmpeg, uv, mlx-qwen3-asr
2. **Transcribe** — word-level JSON + SRT from mlx-qwen3-asr, silence ranges from ffmpeg
3. **Analyze** — categorize suggestions: 语气词 / 重复 / 冗余 / 停顿
4. **Review** — opens localhost page; click word = seek, Shift+click/drag = toggle for deletion
5. **Cut** — ffmpeg trim/concat with 5ms audio fades, re-timed SRT + clean transcript

## License

MIT
