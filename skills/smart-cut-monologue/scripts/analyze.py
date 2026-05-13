#!/usr/bin/env python3
"""
Stage 3 — Analyze transcript.json + silence.json → suggestions.json.

Unlike v1's /tmp/analyze.py, this writes a category-grouped schema with
word_indices so the UI can inline-highlight individual words.

Usage: python3 analyze.py <workdir>
"""
import json
import sys
from pathlib import Path

FILLER_WORDS = {"嗯", "啊", "呃", "唉", "哦", "呢"}
SOFT_PADDING = {("就", "是"), ("那", "个"), ("这", "个")}  # 2-char padding phrases

MIN_PAUSE = 0.8        # default pause threshold (seconds)
REPEAT_NGRAMS = [4, 3, 2]  # try 4-word, then 3, then 2


def analyze(workdir: Path):
    tr = json.loads((workdir / "transcript.json").read_text())
    sil = json.loads((workdir / "silence.json").read_text())
    manifest = json.loads((workdir / "manifest.json").read_text())
    words = tr["segments"]
    duration = manifest["duration"]

    suggestions = []
    sid = 0

    def add(category, start, end, text, word_indices):
        nonlocal sid
        suggestions.append({
            "id": sid,
            "category": category,
            "start": round(start, 3),
            "end": round(end, 3),
            "word_indices": list(word_indices),
            "text": text,
        })
        sid += 1

    # Track which word indices are already covered so categories don't overlap
    claimed = set()

    # 1) Filler: standalone filler words
    i = 0
    while i < len(words):
        t = words[i]["text"].strip()
        if t in FILLER_WORDS and i not in claimed:
            j = i
            while (j + 1 < len(words)
                   and words[j + 1]["text"].strip() in FILLER_WORDS
                   and (j + 1) not in claimed):
                j += 1
            add("filler",
                words[i]["start"], words[j]["end"],
                "".join(words[k]["text"] for k in range(i, j + 1)),
                range(i, j + 1))
            claimed.update(range(i, j + 1))
            i = j + 1
        else:
            i += 1

    # 2) Repeat: consecutive identical n-grams (earlier copy wins deletion)
    for n in REPEAT_NGRAMS:
        i = 0
        while i + 2 * n <= len(words):
            a = "".join(words[i + k]["text"] for k in range(n))
            b = "".join(words[i + n + k]["text"] for k in range(n))
            if a == b and len(a) >= 2:
                rng = range(i, i + n)
                if not any(k in claimed for k in rng):
                    add("repeat",
                        words[i]["start"], words[i + n - 1]["end"],
                        a,
                        rng)
                    claimed.update(rng)
                    i += n
                    continue
            i += 1

    # 3) Padding: soft-filler phrases like 就是/那个/这个
    i = 0
    while i < len(words) - 1:
        pair = (words[i]["text"].strip(), words[i + 1]["text"].strip())
        if pair in SOFT_PADDING and i not in claimed and (i + 1) not in claimed:
            add("padding",
                words[i]["start"], words[i + 1]["end"],
                pair[0] + pair[1],
                [i, i + 1])
            claimed.update([i, i + 1])
            i += 2
        else:
            i += 1

    # 4) Pause: ffmpeg silencedetect ranges that don't overlap a filler already
    filler_ranges = [(s["start"], s["end"]) for s in suggestions if s["category"] == "filler"]

    def overlaps_filler(a, b):
        return any(a < fb and b > fa for fa, fb in filler_ranges)

    for s in sil["ranges"]:
        a, b = s["start"], s["end"]
        if b - a < 0.3:  # ignore micro-silence — user can tune threshold in UI
            continue
        if overlaps_filler(a, b):
            continue
        # trim breathing room
        ta = a + 0.2
        tb = b - 0.2
        if tb - ta < 0.15:
            continue
        add("pause", ta, tb, f"[...{tb-ta:.1f}s]", [])

    # Sort by start time
    suggestions.sort(key=lambda s: s["start"])
    # Reassign ids after sort
    for new_id, s in enumerate(suggestions):
        s["id"] = new_id

    # Build categories with counts
    cats_order = ["filler", "repeat", "padding", "pause"]
    cats_meta = {
        "filler":  {"label": "语气词", "default_checked": True,  "min_duration": 0},
        "repeat":  {"label": "重复",   "default_checked": True,  "min_duration": 0},
        "padding": {"label": "冗余",   "default_checked": False, "min_duration": 0},
        "pause":   {"label": "停顿",   "default_checked": True,  "min_duration": MIN_PAUSE},
    }
    categories = {}
    for c in cats_order:
        categories[c] = dict(cats_meta[c])
        categories[c]["count"] = sum(1 for s in suggestions if s["category"] == c)

    # ==== Verification ====
    n_words = len(words)
    for s in suggestions:
        for wi in s["word_indices"]:
            assert 0 <= wi < n_words, f"suggestion {s['id']} word_index {wi} out of range"
        assert 0 <= s["start"] < s["end"] <= duration + 0.01, \
            f"suggestion {s['id']} time out of range: {s['start']}-{s['end']} vs duration {duration}"
    # categories consistency
    assert sum(c["count"] for c in categories.values()) == len(suggestions)

    out = {
        "video": manifest["video"],
        "duration": duration,
        "categories": categories,
        "suggestions": suggestions,
    }
    (workdir / "suggestions.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False))
    print(f"analyze: {len(suggestions)} suggestions")
    for c in cats_order:
        print(f"  {cats_meta[c]['label']}: {categories[c]['count']}")
    print(f"→ {workdir/'suggestions.json'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: analyze.py <workdir>", file=sys.stderr)
        sys.exit(1)
    analyze(Path(sys.argv[1]).resolve())
