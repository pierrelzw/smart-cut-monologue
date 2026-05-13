#!/usr/bin/env bash
# Preflight check for smart-cut-monologue.
# Exits 0 if all prerequisites are satisfied; non-zero with a clear message if not.
# Does NOT install anything automatically — the calling skill asks the user first.

set -u

# uv installs to ~/.local/bin which may not be on non-interactive PATH
export PATH="$HOME/.local/bin:$PATH"

fail() { echo "preflight: $*" >&2; exit 1; }
ok()   { echo "preflight: $*"; }

# 1. Apple Silicon macOS
[[ "$(uname)" == "Darwin" ]] || fail "macOS only (found $(uname))"
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon only (found $(uname -m); mlx-qwen3-asr is MLX-exclusive)"
ok "platform: macOS arm64 ✓"

# 2. ffmpeg + ffprobe
command -v ffmpeg  >/dev/null 2>&1 || fail "ffmpeg not found — run: brew install ffmpeg"
command -v ffprobe >/dev/null 2>&1 || fail "ffprobe not found — run: brew install ffmpeg"
ok "ffmpeg: $(ffmpeg -version | head -n1)"

# 3. uv
command -v uv >/dev/null 2>&1 || fail "uv not found — run: brew install uv"
ok "uv: $(uv --version)"

# 4. mlx-qwen3-asr installed as uv tool
if ! uv tool list 2>/dev/null | grep -q '^mlx-qwen3-asr'; then
    fail "mlx-qwen3-asr not installed as uv tool — run: uv tool install mlx-qwen3-asr (first run will download several GB of model weights)"
fi
ok "mlx-qwen3-asr: installed as uv tool ✓"

ok "all checks passed"
