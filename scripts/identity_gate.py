#!/usr/bin/env python3
"""
identity_gate.py — neutral, non-leading VLM identity verification.

Fixes the sycophancy failure of v1 critique (which asked "does it look like
Mr Bean?" — inviting YES). Here we never name the target identity in the
question: we show two faces and ask whether they are the same person.

Usage:
  python3 identity_gate.py <anchor_real_photo> <candidate1> [candidate2 ...] [--out results.json]
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw

def zvision(prompt, media, outfile):
    cmd = ["z-ai", "vision", "-p", prompt, "-i", media, "-o", outfile]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.load(open(outfile))
    except Exception:
        return {"raw": r.stdout[-500:], "err": r.stderr[-300:]}

def pair_grid(pa, pb, out, labels=("A", "B")):
    ia, ib = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
    h = 640
    ia = ia.resize((int(ia.width * h / ia.height), h))
    ib = ib.resize((int(ib.width * h / ib.height), h))
    cv = Image.new("RGB", (ia.width + ib.width + 12, h + 28), "white")
    cv.paste(ia, (0, 28)); cv.paste(ib, (ia.width + 12, 28))
    d = ImageDraw.Draw(cv)
    d.text((6, 6), f"IMAGE 1 = {labels[0]}", fill="black")
    d.text((ia.width + 18, 6), f"IMAGE 2 = {labels[1]}", fill="black")
    cv.save(out, quality=92)

def main():
    argv = sys.argv[1:]
    out_json = "identity_gate.json"
    if "--out" in argv:
        i = argv.index("--out")
        out_json = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    anchor, cands = argv[0], argv[1:]
    results = []
    for i, c in enumerate(cands):
        grid = f"/tmp/idgate_{i}.jpg"
        pair_grid(anchor, c, grid, labels=("REFERENCE PHOTO", "CANDIDATE"))
        rep = zvision(
            "You are doing forensic face comparison. Two photos are shown. "
            "IMAGE 1 is the reference person. IMAGE 2 is a candidate. "
            "Question: is the person in IMAGE 2 the SAME individual as in IMAGE 1? "
            "Judge by face geometry only (skull shape, eyes, brows, nose, mouth, hairline, ears), "
            "ignore clothing, background, lighting and image quality. "
            "Answer strictly as JSON: {\"same_person\": true|false, \"confidence\": 0-10, "
            "\"matching_features\": [...], \"mismatching_features\": [...]}", grid, f"/tmp/idgate_{i}.json")
        results.append({"candidate": c, "verdict": rep})
        print(f"[*] {os.path.basename(c)} -> {json.dumps(rep)[:220]}")
    json.dump(results, open(out_json, "w"), indent=2, ensure_ascii=False)
    print("[*] gate results ->", out_json)

if __name__ == "__main__":
    main()
