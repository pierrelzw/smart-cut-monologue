#!/usr/bin/env python3
"""
Cut the source video per user-confirmed cuts.json, producing a frame-accurate
re-encoded output plus a re-timed SRT and plain-text transcript.

Usage: python3 cut_video.py <workdir>
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

MIN_DELETE = 0.10   # drop delete spans shorter than 100ms
MIN_KEEP   = 0.05   # drop keep spans shorter than this
MERGE_GAP  = 0.15   # merge deletes whose gap is ≤150ms (mirrors review.html;
                    # covers ASR inter-word silences so adjacent ticked words
                    # collapse into one delete)
SLIVER_KEEP = 0.30  # backstop: any kept span between two deletes shorter
                    # than this gets absorbed into the surrounding deletes.
                    # Independent of UI to guarantee output cleanliness.
FADE       = 0.005  # 5ms audio fade at concat boundaries


def run(cmd, **kw):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ]).decode().strip()
    return float(out)


def normalize_deletes(cuts, duration):
    ranges = []
    for c in cuts:
        a = max(0.0, float(c["start"]))
        b = min(duration, float(c["end"]))
        if b - a >= MIN_DELETE:
            ranges.append([a, b])
    ranges.sort()
    merged = []
    for r in ranges:
        if merged and r[0] - merged[-1][1] <= MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(r[:])
    return merged


def sliver_backstop(deletes):
    """Last-resort: absorb sub-SLIVER_KEEP gaps between deletes regardless of
    word content. Only meant to catch what absorb_wordless_gaps couldn't (e.g.
    transcript.json missing or malformed). MUST run AFTER absorb_wordless_gaps
    so the semantic check has first refusal — otherwise a kept span that holds
    a real ≤300ms word would be silently merged away."""
    absorbed = []
    for r in deletes:
        if absorbed and r[0] - absorbed[-1][1] < SLIVER_KEEP:
            absorbed[-1][1] = max(absorbed[-1][1], r[1])
        else:
            absorbed.append(r[:])
    return absorbed


def absorb_wordless_gaps(deletes, workdir: Path, duration: float):
    """Merge two adjacent deletes if the kept span between them contains no
    transcript word (and is therefore audio-empty by ASR's reckoning).

    User rule: silence at the head/tail of a kept passage is intentional
    breathing room — leave it. But a kept sliver sandwiched between two
    deletes, with no spoken word inside, is dead air that survived only
    because the word-level UI can't tick non-word regions. Absorb it.

    transcript.json is the source of truth (silencedetect's 0.6s threshold
    misses sub-second inter-word gaps).
    """
    tj = workdir / "transcript.json"
    if not tj.is_file() or not deletes:
        return deletes
    try:
        doc = json.loads(tj.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"cut: transcript.json unreadable, skipping wordless-gap absorb: {e}", file=sys.stderr)
        return deletes
    segs = doc.get("segments", doc if isinstance(doc, list) else [])
    if not segs:
        print("cut: transcript.json had no recognizable 'segments' — skipping wordless-gap absorb.", file=sys.stderr)
        return deletes
    # Skip zero-duration words: ASR sometimes emits multi-token bursts at a
    # single timestamp inside a silent region (no real audio). Counting them
    # as "present" would block legitimate silence absorption.
    words = sorted(
        (float(s["start"]), float(s["end"]))
        for s in segs
        if "start" in s and "end" in s and float(s["end"]) - float(s["start"]) > 0.01
    )

    def has_word(a, b):
        for ws, we in words:
            if ws >= b:
                return False
            if we > a:
                return True
        return False

    merged = [deletes[0][:]]
    for r in deletes[1:]:
        gap_a, gap_b = merged[-1][1], r[0]
        if gap_b > gap_a and not has_word(gap_a, gap_b):
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(r[:])
    # Head: a wordless region before the first delete is dead air the user
    # couldn't tick — absorb. Runs even for a single-delete clip (the most
    # common single-filler case). Tail is intentionally NOT symmetric: if
    # the user didn't explicitly cut into the trailing silence, leave it
    # as ending breathing room.
    if merged[0][0] > 0 and not has_word(0.0, merged[0][0]):
        merged[0][0] = 0.0
    _ = duration  # kept for API symmetry; tail extension intentionally skipped
    return merged


def invert_to_keeps(deletes, duration):
    keeps = []
    cursor = 0.0
    for a, b in deletes:
        if a - cursor >= MIN_KEEP:
            keeps.append([cursor, a])
        cursor = b
    if duration - cursor >= MIN_KEEP:
        keeps.append([cursor, duration])
    return keeps


def build_filter(keeps):
    v_labels, a_labels = [], []
    parts = []
    for i, (a, b) in enumerate(keeps):
        parts.append(f"[0:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(
            f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(0, b-a-FADE):.6f}:d={FADE}[a{i}]"
        )
        v_labels.append(f"[v{i}]")
        a_labels.append(f"[a{i}]")
    concat = (
        "".join(v + a for v, a in zip(v_labels, a_labels))
        + f"concat=n={len(keeps)}:v=1:a=1[vout][aout]"
    )
    parts.append(concat)
    return ";".join(parts)


def cut_srt(srt_path: Path, deletes, out_path: Path):
    if not srt_path.is_file():
        return
    text = srt_path.read_text()
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    parsed = []
    for b in blocks:
        lines = b.splitlines()
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a_s, a_e = lines[1].split(" --> ")
        parsed.append((srt_time(a_s), srt_time(a_e), "\n".join(lines[2:])))

    def shift(t):
        drop = 0.0
        for a, b in deletes:
            if t >= b:
                drop += b - a
            elif t > a:
                drop += t - a
        return t - drop

    def keep(t_s, t_e):
        # Drop cue if it overlaps ANY delete range — keeping a partial cue
        # would leave text whose video has been cut.
        for a, b in deletes:
            if t_s < b and t_e > a:
                return False
        return True

    out = []
    idx = 1
    for s, e, text in parsed:
        if not keep(s, e):
            continue
        ns, ne = shift(s), shift(e)
        if ne - ns < 0.05:
            continue
        out.append(f"{idx}\n{fmt_srt(ns)} --> {fmt_srt(ne)}\n{text}\n")
        idx += 1
    out_path.write_text("\n".join(out))


def srt_time(s: str) -> float:
    # Strip trailing cue-settings like "X1:0 X2:160 ..." that some SRTs include
    s = s.strip().split()[0]
    h, m, rest = s.split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def fmt_srt(t: float) -> str:
    total_ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    if len(sys.argv) != 2:
        print("usage: cut_video.py <workdir>", file=sys.stderr); sys.exit(1)
    workdir = Path(sys.argv[1]).resolve()
    manifest = json.loads((workdir / "manifest.json").read_text())
    cuts_doc = json.loads((workdir / "cuts.json").read_text())

    video = Path(manifest["video"])
    duration = ffprobe_duration(video)
    stem = manifest["stem"]

    out_video = workdir / f"{stem}_cut.mp4"
    out_srt   = workdir / f"{stem}_cut.srt"
    out_txt   = workdir / f"{stem}_cut.txt"

    deletes = normalize_deletes(cuts_doc.get("cuts", []), duration)
    # Semantic pass first: absorb inter-delete gaps that contain no real word.
    deletes = absorb_wordless_gaps(deletes, workdir, duration)
    # Dumb backstop last: any sub-300ms residue gets merged regardless of
    # content. Runs *after* the semantic check so it can't destroy a real
    # short word the user intentionally kept.
    deletes = sliver_backstop(deletes)
    if not deletes:
        # User confirmed zero cuts → they want the original unchanged. Deliver
        # a copy under the _cut name so downstream consumers always see output.
        print("cut: no cuts confirmed — copying original as output.")
        shutil.copy2(video, out_video)
        if (workdir / "transcript.srt").is_file():
            shutil.copy2(workdir / "transcript.srt", out_srt)
            lines = []
            for block in out_srt.read_text().strip().split("\n\n"):
                bl = block.splitlines()
                if len(bl) >= 3:
                    lines.append(" ".join(bl[2:]))
            out_txt.write_text("\n".join(lines))
        print(f"\n✓ no-op copy done: {out_video}")
        sys.exit(0)

    keeps = invert_to_keeps(deletes, duration)
    if not keeps:
        print("cut: everything would be deleted, aborting.", file=sys.stderr); sys.exit(1)

    filter_complex = build_filter(keeps)

    run([
        "ffmpeg", "-y", "-i", str(video),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_video),
    ])

    # SRT re-time
    cut_srt(workdir / "transcript.srt", deletes, out_srt)

    # Clean transcript (from re-timed SRT)
    if out_srt.is_file():
        lines = []
        for block in out_srt.read_text().strip().split("\n\n"):
            bl = block.splitlines()
            if len(bl) >= 3:
                lines.append(" ".join(bl[2:]))
        out_txt.write_text("\n".join(lines))

    # Verify duration AFTER all outputs written. Allow 200ms drift — dense-cut
    # videos (many short segments) accumulate ~1 frame of PTS rounding per
    # concat boundary. Hard-fail past 200ms because that signals a real issue
    # (VFR source, broken codec).
    out_dur = ffprobe_duration(out_video)
    expected = sum(b - a for a, b in keeps)
    drift = abs(out_dur - expected)
    print(f"cut: expected {expected:.3f}s, got {out_dur:.3f}s (drift {drift*1000:.1f}ms)")
    if drift > 0.2:
        print(
            f"cut: FAIL — output drifted {drift*1000:.1f}ms from expected "
            f"({expected:.3f}s). Check ffmpeg output; VFR sources may need "
            "-fps_mode cfr. See references/workflow.md.",
            file=sys.stderr,
        )
        sys.exit(4)

    deleted_total = sum(b - a for a, b in deletes)
    print(f"\n✓ cut done")
    print(f"  video:  {out_video}")
    print(f"  srt:    {out_srt}")
    print(f"  txt:    {out_txt}")
    print(f"  原 {fmt_min(duration)} → 剪后 {fmt_min(out_dur)} (删除 {fmt_min(deleted_total)})")


def fmt_min(t):
    return f"{int(t//60)}:{t%60:05.2f}"


if __name__ == "__main__":
    main()
