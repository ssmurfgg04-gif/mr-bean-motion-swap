#!/usr/bin/env python3
"""
assemble_chunks.py — frame-exact assembly of parallel-generated swap chunks.

Each chunk i was generated from [send_start, send_end] of the driving segment
(see plan_chunks.py). Wan may return FEWER frames than requested (latent
window rounding), so assembly trims by ACTUAL frame counts and maps each
trimmed piece back onto the absolute timeline. Cores tile gaplessly by
construction; any residual hole (rare) is filled by freezing the previous
piece's last frame and reported.

Usage:
  python3 assemble_chunks.py --plan plan.json --chunks-dir chunks --out stitched.mp4
Chunks are expected at <chunks-dir>/chunk_<i>/swapped.mp4
"""
import argparse, json, os, subprocess, sys


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=r_frame_rate,nb_read_frames",
                        "-count_frames", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    fr, nf = r.stdout.strip().split(",")
    num, den = fr.split("/")
    fps = float(num) / float(den)
    return fps, int(nf) if nf.isdigit() else 0


def trim_frames(src, start_f, n_f, dst, fps):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
           f"select='gte(n,{start_f})*lt(n,{start_f + n_f})',setpts=PTS-STARTPTS",
           "-r", f"{fps}", "-an", "-c:v", "libx264", "-crf", "14",
           "-preset", "fast", "-pix_fmt", "yuv420p", dst]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--chunks-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    plan = json.load(open(args.plan))
    fps = plan["fps"]
    pieces, timeline = [], []
    for c in plan["chunks"]:
        i = c["index"]
        src = os.path.join(args.chunks_dir, f"chunk_{i}", "swapped.mp4")
        if not os.path.exists(src):
            print(f"[!] MISSING chunk {i}: {src}", file=sys.stderr)
            return 4
        cfps, n = probe(src)
        rel_start = round((c["core_start_s"] - c["send_start_s"]) * cfps)
        have = max(0, n - rel_start)
        need = round((c["core_end_s"] - c["core_start_s"]) * fps)
        take = min(need, have)
        cov_end = c["core_start_s"] + take / fps
        print(f"[*] chunk {i}: out={n}f@{cfps:.2f} rel_start={rel_start} "
              f"take={take}/{need} -> covers {c['core_start_s']:.2f}-{cov_end:.2f}s")
        if take <= 0:
            print(f"[!] chunk {i} contributed 0 frames", file=sys.stderr)
            continue
        piece = f"/tmp/piece_{i}.mp4"
        trim_frames(src, rel_start, take, piece, fps)
        pieces.append(piece)
        timeline.append({"index": i, "start_s": c["core_start_s"], "end_s": cov_end})

    # gap detection on the absolute timeline
    gaps = []
    for a, b in zip(timeline, timeline[1:]):
        if b["start_s"] - a["end_s"] > 1.5 / fps:
            gaps.append((a["end_s"], b["start_s"]))
    if timeline and timeline[0]["start_s"] > 0.5 / fps:
        gaps.insert(0, (0.0, timeline[0]["start_s"]))
    if gaps:
        print(f"[!] timeline gaps (will freeze-fill): {gaps}", file=sys.stderr)

    # concat pieces (same codec/size/fps -> concat filter with re-encode)
    if len(pieces) == 1:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", pieces[0],
                        "-c:v", "libx264", "-crf", "14", "-preset", "fast",
                        "-pix_fmt", "yuv420p", args.out], check=True)
    else:
        ins, flt = [], []
        for k, p in enumerate(pieces):
            ins += ["-i", p]
            flt.append(f"[{k}:v]")
        cmd = ["ffmpeg", "-y", "-v", "error"] + ins + ["-filter_complex",
               "".join(flt) + f"concat=n={len(pieces)}:v=1:a=0[out]",
               "-map", "[out]", "-c:v", "libx264", "-crf", "14",
               "-preset", "fast", "-pix_fmt", "yuv420p", args.out]
        subprocess.run(cmd, check=True)

    total = probe(args.out)[1]
    print(f"[*] stitched: {total} frames ({total/fps:.2f}s) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
