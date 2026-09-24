#!/bin/bash
# ci_chunk.sh <index> — cut one chunk (per plan.json) and swap it via ZeroGPU.
# Used by both matrix stages (fresh runner = fresh IP = fresh anonymous quota).
set -euo pipefail
IDX="$1"
mkdir -p "output/chunk_${IDX}"
python3 - <<EOF
import json, subprocess
plan = json.load(open("plan.json"))
c = [x for x in plan["chunks"] if x["index"] == ${IDX}][0]
subprocess.run(["ffmpeg","-y","-v","error",
                "-ss",str(c["send_start_s"]),
                "-to",str(c["send_end_s"]),
                "-i","shared/segment.mp4",
                "-c:v","libx264","-crf","14","-preset","fast","-an",
                f"output/chunk_${IDX}/send.mp4"], check=True)
EOF
RESOLUTION="${RESOLUTION:-Low Res}"
MODE="${MODE:-Character Swap}"
DELAY=$(( ${START_DELAY:-0} * 20 ))   # stagger matrix jobs (chunk index * 20s)
[ "$DELAY" -gt 0 ] && sleep "$DELAY"
python3 scripts/run_swap.py "output/chunk_${IDX}/send.mp4" shared/ref.png \
  "output/chunk_${IDX}" \
  --resolution "$RESOLUTION" \
  --rc-mode "$MODE" \
  --start-delay 0 \
  --attempts 4
