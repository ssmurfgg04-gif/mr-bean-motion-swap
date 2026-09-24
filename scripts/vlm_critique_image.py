#!/usr/bin/env python3
"""
vlm_critique_image.py — 4-round VLM critique using FRAME GRIDS only
(video inputs are rejected by the vision endpoint; images work reliably).

Round 1: overall quality grid (full reel timeline)
Round 2: identity — neutral gate vs real reference photo + stability strip
Round 3: motion fidelity — matched-time pairs original vs swapped
Round 4: technical — zoomed crops (hair/hands/background/edges)

Usage:
  python3 vlm_critique_image.py <final_reel> <full_source_video> <cut_time_s> <real_ref_photo> <outdir>
"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw

def zvision(prompt, media, outfile):
    cmd = ["z-ai", "vision", "-p", prompt, "-i", media, "-o", outfile]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == 0 and os.path.exists(outfile)
    print(f"[vision] {os.path.basename(outfile)} rc={r.returncode}", flush=True)
    try:
        return json.load(open(outfile))
    except Exception:
        return {"raw": r.stdout[-400:], "err": r.stderr[-300:]}

def frame(video, t, path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", video,
                    "-frames:v", "1", path], check=True)

def grid(paths, out, labels, h=420, cols=None):
    ims = [Image.open(p) for p in paths]
    ims = [im.resize((int(im.width * h / im.height), h)) for im in ims]
    cols = cols or len(ims)
    rows = (len(ims) + cols - 1) // cols
    wmax = max(i.width for i in ims)
    cv = Image.new("RGB", (cols * (wmax + 8) + 8, rows * (h + 30) + 8), "white")
    d = ImageDraw.Draw(cv)
    for k, (im, lb) in enumerate(zip(ims, labels)):
        x = 8 + (k % cols) * (wmax + 8)
        y = 8 + (k // cols) * (h + 30)
        cv.paste(im, (x, y + 22))
        d.text((x, y + 4), lb, fill="black")
    cv.save(out, quality=88)

def main():
    final, source, cut, real_ref, out = sys.argv[1:6]
    cut = float(cut)
    os.makedirs(out, exist_ok=True)

    # ---------- ROUND 1: overall ----------
    ts = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5]
    p1 = []
    for t in ts:
        p = f"{out}/r1_{t}.png"; frame(final, t, p); p1.append(p)
    grid(p1, f"{out}/round1_grid.jpg", [f"t={t}s" for t in ts], h=300, cols=4)
    r1 = zvision(
        "You are a strict video QA reviewer. This grid shows 8 frames across a short "
        "social reel: first ~3s is a creator speaking, after a hard cut a different "
        "man performs hand/arm moves in the same room. Evaluate: 1) overall visual "
        "quality 2) is the man after the cut visually CONSISTENT across frames (same "
        "person, same clothes)? 3) any morphing/ghosting/background warping? "
        "4) any abruptly broken body parts (hands, jaw)? Score each 1-10 with reasons.",
        f"{out}/round1_grid.jpg", f"{out}/round1_overall.json")

    # ---------- ROUND 2: identity (NEUTRAL, target never named) ----------
    p2 = []
    for t in [3.8, 5.2, 6.6, 8.0]:
        p = f"{out}/r2_{t}.png"; frame(final, t, p); p2.append(p)
    pair_imgs = p2 + [real_ref]
    grid(pair_imgs, f"{out}/round2_grid.jpg",
         ["CANDIDATE 1", "CANDIDATE 2", "CANDIDATE 3", "CANDIDATE 4", "REFERENCE PERSON"],
         h=340, cols=5)
    r2 = zvision(
        "Forensic identity comparison. Five photos: four CANDIDATES and one REFERENCE "
        "PERSON. For EACH candidate judge ONLY facial geometry (skull shape, eye shape "
        "and spacing, eyebrows, nose, mouth, hairline): is it the SAME individual as "
        "the reference? Ignore clothing/background/quality/expression. "
        "Answer strictly as JSON: {\"c1\": {\"same\": true/false, \"confidence\": 0-10}, "
        "\"c2\": ..., \"c3\": ..., \"c4\": ...}",
        f"{out}/round2_grid.jpg", f"{out}/round2_identity.json")

    # ---------- ROUND 3: motion fidelity pairs ----------
    times = [0.6, 1.5, 2.4, 3.3, 4.2]
    pairs = []
    for i, t in enumerate(times):
        a, b = f"{out}/r3o_{i}.png", f"{out}/r3s_{i}.png"
        frame(source, cut + t, a)
        frame(final, cut + t, b)
        grid([a, b], f"{out}/round3_pair_{i}.jpg",
             [f"ORIGINAL t+{t}s", f"SWAPPED t+{t}s"], h=460, cols=2)
        pairs.append((f"{out}/round3_pair_{i}.jpg", t))
    r3_reports = []
    for p, t in pairs:
        rep = zvision(
            "LEFT = original video frame, RIGHT = swapped frame at the SAME relative "
            "time (a different man should perform the SAME move). Compare: 1) same "
            "hand/arm gesture? 2) same head position/orientation? 3) same body framing? "
            "4) rendering defects on the right (extra/missing fingers, smearing)? "
            "Answer per point 1-4 concisely.",
            p, p.replace(".jpg", ".json"))
        r3_reports.append({"t": t, "report": rep})
    r3 = {"pairs": r3_reports}

    # ---------- ROUND 4: technical zoomed ----------
    zooms = []
    for i, t in enumerate([4.3, 5.2, 6.1, 7.0]):
        p = f"{out}/r4_{i}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", final,
                        "-frames:v", "1", "-vf", "crop=576:700:0:150,scale=768:933", p],
                       check=True)
        zooms.append(p)
    grid(zooms, f"{out}/round4_grid.jpg", [f"zoom t={t}s" for t in [4.3, 5.2, 6.1, 7.0]],
         h=430, cols=4)
    r4 = zvision(
        "Technical QA on zoomed crops from an AI-replaced character video. Evaluate: "
        "1) face/hair artifacts (smearing, double jaws, edge halos) 2) hands/fingers "
        "anatomy 3) clothing texture consistency 4) background stability 5) upscale "
        "softness or waxy texture. Score each 1-10 and name the worst frame.",
        f"{out}/round4_grid.jpg", f"{out}/round4_technical.json")

    summary = {"round1": r1, "round2": r2, "round3": r3, "round4": r4}
    json.dump(summary, open(f"{out}/critique_summary.json", "w"), indent=2, ensure_ascii=False)
    print("[*] all 4 rounds done ->", f"{out}/critique_summary.json", flush=True)


if __name__ == "__main__":
    main()
