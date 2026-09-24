#!/usr/bin/env python3
"""
plan_chunks.py — motion-aware chunk planner for ZeroGPU quota slicing.

Splits a video segment into chunks whose lengths fit the anonymous GPU budget,
cutting at LOW-MOTION valleys so that seams land where the body barely moves
(makes crossfade seams invisible). Each chunk carries an overlap on both sides
so neighbouring chunks share motion context for blending.

Also enforces the ZeroGPU budget: approx GPU cost ~= 40 GPU-s per video-second
at Low Res, x2 at Medium Res; anonymous cap ~160 GPU-s per IP.

Usage:
  python3 plan_chunks.py <video> --out plan.json [--target-s 4.5] [--overlap-s 0.5]
                         [--resolution "Low Res"] [--max-gpu-s 150]
"""
import argparse, json, math, subprocess, sys
import numpy as np

GPU_S_PER_SECOND = {"Low Res": 40.0, "Medium Res": 80.0}


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h, fr, nf = r.stdout.strip().split(",")
    num, den = fr.split("/")
    return int(w), int(h), float(num) / float(den), int(nf) if nf.isdigit() else None


def activity_curve(path, fps):
    """Per-second-frame activity: mean abs frame diff on 48x48 gray thumbnails."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf",
           f"fps={fps},scale=48:48,format=gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    raw = p.stdout.read()
    p.wait()
    n = len(raw) // (48 * 48)
    frames = np.frombuffer(raw[:n * 48 * 48], np.uint8).reshape(n, 48, 48).astype(np.int16)
    act = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2)) if n > 1 else np.zeros(1)
    # prepend a 0 so activity[i] corresponds to sample i (frame i*fps/fps ...)
    return np.concatenate([[0.0], act])


def smooth(x, k=5):
    if len(x) < k:
        return x
    ker = np.ones(k) / k
    return np.convolve(x, ker, mode="same")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="plan.json")
    ap.add_argument("--target-s", type=float, default=4.5)
    ap.add_argument("--overlap-s", type=float, default=0.5)
    ap.add_argument("--resolution", default="Low Res")
    ap.add_argument("--max-gpu-s", type=float, default=150.0)
    ap.add_argument("--snap-window-s", type=float, default=0.8)
    args = ap.parse_args()

    w, h, fps, nf = probe(args.video)
    dur = nf / fps if nf else None
    if not dur:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", args.video], capture_output=True, text=True)
        dur = float(r.stdout.strip())
    print(f"[*] {w}x{h}@{fps:.2f}, {dur:.2f}s")

    # budget => max SEND length (core + overlaps must fit the GPU budget)
    rate = GPU_S_PER_SECOND.get(args.resolution, 40.0)
    max_send = max(1.0, args.max_gpu_s / rate)
    ov = args.overlap_s
    target = min(args.target_s, max_send - 2 * ov)
    if target < 0.9:
        # overlaps too large for the budget -> shrink them
        ov = max(0.2, (max_send - 1.0) / 2)
        target = max(0.9, min(args.target_s, max_send - 2 * ov))
    print(f"[*] resolution {args.resolution}: send <= {max_send:.2f}s "
          f"(core target {target:.2f}s, overlap {ov:.2f}s)")

    act = smooth(activity_curve(args.video, fps), 5)
    n_act = len(act)

    def snap_to_valley(t):
        """Snap time t to the nearest low-motion sample within +-snap window."""
        i0 = max(1, int((t - args.snap_window_s) * fps))
        i1 = min(n_act - 1, int((t + args.snap_window_s) * fps))
        if i1 <= i0:
            return t
        window = act[i0:i1]
        best = i0 + int(np.argmin(window))
        return best / fps

    cuts = [0.0]
    t = 0.0
    max_core = max_send - 2 * ov
    while True:
        remaining = dur - t
        max_this = max_send - (ov if t > 0 else 0) - 0.02
        if remaining <= max_this:
            break
        nxt = min(t + target, dur)
        snapped = snap_to_valley(nxt)
        snapped = min(snapped, t + max_core)   # budget clamp after valley snap
        snapped = max(snapped, t + 0.8)        # min chunk length
        if dur - snapped < 0.5:                # avoid sliver tail
            snapped = t + remaining / 2
        cuts.append(round(snapped, 3))
        t = snapped
    cuts.append(round(dur, 3))

    chunks = []
    for i in range(len(cuts) - 1):
        cs, ce = cuts[i], cuts[i + 1]
        chunks.append({
            "index": i,
            "core_start_s": cs, "core_end_s": ce,
            "core_frames": int(round((ce - cs) * fps)),
            "send_start_s": max(0.0, cs - ov if i > 0 else cs),
            "send_end_s": min(dur, ce + ov if i < len(cuts) - 2 else ce),
            "est_gpu_s": int(round((ce - cs + (ov if i > 0 else 0) + (ov if i < len(cuts) - 2 else 0)) * rate)),
        })
        chunks[-1]["send_frames"] = int(round((chunks[-1]["send_end_s"] - chunks[-1]["send_start_s"]) * fps))
        assert (chunks[-1]["send_end_s"] - chunks[-1]["send_start_s"]) * rate <= args.max_gpu_s + 1, \
            f"chunk {i} send exceeds GPU budget"

    plan = {
        "video": args.video, "fps": fps, "duration_s": dur,
        "resolution": args.resolution, "overlap_s": ov,
        "gpu_rate": rate, "cuts_s": cuts, "chunks": chunks,
    }
    json.dump(plan, open(args.out, "w"), indent=2)
    print(f"[*] plan: {len(chunks)} chunks, cuts at {[round(c,2) for c in cuts]}")
    for c in chunks:
        print(f"    chunk {c['index']}: core {c['core_start_s']:.2f}-{c['core_end_s']:.2f}s "
              f"(send {c['send_start_s']:.2f}-{c['send_end_s']:.2f}, ~{c['est_gpu_s']} GPU-s)")


if __name__ == "__main__":
    main()
