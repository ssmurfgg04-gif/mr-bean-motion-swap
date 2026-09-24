#!/bin/bash
# ci_assemble.sh — normalize chunk artifacts, check completeness, assemble,
# enhance, mux audio. If chunks are missing (quota failures), writes
# missing.json and exits 42 so CI can spawn a fresh-IP retry matrix.
set -euo pipefail
STAGE_DIR="${1:-work}"

mkdir -p chunks "$STAGE_DIR"
MISSING="[]"
python3 - <<EOF
import json, os
plan = json.load(open("plan.json"))
missing = [c["index"] for c in plan["chunks"]
           if not os.path.exists(f"chunks/chunk_{c['index']}/swapped.mp4")]
json.dump(missing, open("missing.json", "w"))
print("missing chunks:", missing)
EOF
MISSING=$(cat missing.json)

if [ "$MISSING" != "[]" ]; then
  echo "MISSING=$MISSING" > "$STAGE_DIR/missing.env"
  exit 42
fi

python3 scripts/assemble_chunks.py --plan plan.json --chunks-dir chunks \
  --out "$STAGE_DIR/stitched.mp4"

python3 scripts/enhance_swap.py \
  --swapped "$STAGE_DIR/stitched.mp4" --original shared/segment.mp4 \
  --out "$STAGE_DIR/enhanced.mp4" --debug-dir "$STAGE_DIR/enh_debug"

# reattach original audio (fall back to silent video if segment has none)
if ffprobe -v error -select_streams a -show_entries stream=codec_type \
     -of csv=p=0 shared/segment.mp4 | grep -q audio; then
  ffmpeg -y -v error -i "$STAGE_DIR/enhanced.mp4" -i shared/segment.mp4 \
    -map 0:v -map 1:a -c:v copy -c:a aac -b:a 128k \
    -shortest -movflags +faststart "$STAGE_DIR/swapped_final.mp4"
else
  cp "$STAGE_DIR/enhanced.mp4" "$STAGE_DIR/swapped_final.mp4"
fi
echo "assembled -> $STAGE_DIR/swapped_final.mp4"
