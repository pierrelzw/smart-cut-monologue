#!/usr/bin/env bash
# Transcribe a video with mlx-qwen3-asr and run ffmpeg silencedetect.
# Writes transcript.json, transcript.srt, silence.json into <video-dir>/smart-cut/<stem>/
# Usage: transcribe.sh <video-path>

set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

# Allow overriding model / HF mirror from caller env. 1.7B is our default —
# better transcription quality than 0.6B, still fits comfortably on Apple Silicon.
: "${SMART_CUT_MODEL:=Qwen/Qwen3-ASR-1.7B}"
: "${SMART_CUT_ALIGNER:=Qwen/Qwen3-ForcedAligner-0.6B}"
: "${HF_ENDPOINT:=https://hf-mirror.com}"
export HF_ENDPOINT

# If model paths are local, force offline mode so huggingface_hub never tries
# to reach the network (otherwise a system proxy with mis-behaved connections
# will hang the process for minutes).
if [[ -d "$SMART_CUT_MODEL" && -d "$SMART_CUT_ALIGNER" ]]; then
    export HF_HUB_OFFLINE=1
    export TRANSFORMERS_OFFLINE=1
fi

VIDEO="${1:?usage: transcribe.sh <video-path>}"
[[ -f "$VIDEO" ]] || { echo "transcribe: video not found: $VIDEO" >&2; exit 1; }

VIDEO_ABS="$(cd "$(dirname "$VIDEO")" && pwd)/$(basename "$VIDEO")"
VIDEO_DIR="$(dirname "$VIDEO_ABS")"
STEM="$(basename "$VIDEO_ABS")"
STEM="${STEM%.*}"
WORKDIR="$VIDEO_DIR/smart-cut/$STEM"
mkdir -p "$WORKDIR"

# ---- Duration guard (15 min cap in v1) ----
DURATION=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VIDEO_ABS")
DUR_INT=${DURATION%.*}
if (( DUR_INT > 900 )); then
    echo "transcribe: video is ${DUR_INT}s (>15min). v1 scope is ≤15min — split or wait for v2." >&2
    exit 2
fi
echo "transcribe: video duration ${DURATION}s → $WORKDIR"

# ---- 1. ASR (skip if already done) ----
if [[ ! -s "$WORKDIR/transcript.json" ]]; then
    echo "transcribe: running mlx-qwen3-asr (first run downloads model weights, be patient)..."
    # Emit both JSON and SRT in one pass via --output-format all
    uv tool run mlx-qwen3-asr "$VIDEO_ABS" \
        --model "$SMART_CUT_MODEL" \
        --forced-aligner "$SMART_CUT_ALIGNER" \
        --timestamps \
        --output-format all \
        --output-dir "$WORKDIR/" \
        || { echo "transcribe: mlx-qwen3-asr failed" >&2; exit 3; }

    SRC_JSON="$WORKDIR/${STEM}.json"
    SRC_SRT="$WORKDIR/${STEM}.srt"
    [[ -f "$SRC_JSON" ]] && mv "$SRC_JSON" "$WORKDIR/transcript.json"
    [[ -f "$SRC_SRT" ]]  && mv "$SRC_SRT"  "$WORKDIR/transcript.srt"
else
    echo "transcribe: transcript.json already exists, skipping ASR"
fi

# ---- 2. Silence detection ----
if [[ ! -s "$WORKDIR/silence.json" ]]; then
    echo "transcribe: running silencedetect..."
    SILENCE_LOG=$(ffmpeg -hide_banner -nostats -i "$VIDEO_ABS" \
        -af "silencedetect=noise=-30dB:d=0.6" -f null - 2>&1 | grep silence_ || true)

    python3 - "$SILENCE_LOG" "$WORKDIR/silence.json" <<'PY'
import json, re, sys
log = sys.argv[1]
out = sys.argv[2]
starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", log)]
ends   = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)",   log)]
ranges = [{"start": s, "end": e} for s, e in zip(starts, ends) if e > s]
with open(out, "w") as f:
    json.dump({"ranges": ranges}, f, indent=2)
print(f"transcribe: {len(ranges)} silence ranges → {out}")
PY
else
    echo "transcribe: silence.json already exists, skipping"
fi

# ---- 3. Write a manifest for downstream stages (JSON-safe for weird filenames) ----
python3 - "$VIDEO_ABS" "$STEM" "$WORKDIR" "$DURATION" > "$WORKDIR/manifest.json" <<'PY'
import json, sys
video, stem, workdir, duration = sys.argv[1:5]
json.dump({"video": video, "stem": stem, "workdir": workdir, "duration": float(duration)},
          sys.stdout, indent=2, ensure_ascii=False)
PY

echo "transcribe: done"
echo "  transcript: $WORKDIR/transcript.json"
echo "  silence:    $WORKDIR/silence.json"
echo "  manifest:   $WORKDIR/manifest.json"
