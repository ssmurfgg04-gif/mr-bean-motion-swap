#!/usr/bin/env python3
"""
enhance_swap.py — post-processing quality booster for Wan2.2-Animate character swaps.

Fixes the four artifacts found by VLM critique:
  1. Background "breathing"  -> background-plate compositing: keep the ORIGINAL
     full-resolution background pixels, take only the swapped PERSON (alpha matte).
  2. Upscale softness        -> lanczos upscale + unsharp before matting, plus
     optional grain matching to the source.
  3. Lighting/color shift    -> LAB color transfer of the person region to the
     original scene statistics (global, per-segment => no flicker).
  4. Matte flicker           -> temporal median(3) smoothing of the alpha matte.

The original and swapped videos are aligned by normalized time progress
(frame j of swapped maps to round(j*N/M) of original), so no audio sync is lost.

Usage:
  python3 enhance_swap.py --swapped swapped.mp4 --original original_segment.mp4 \
      --out enhanced.mp4 [--matte auto|diff|rembg|none] [--no-colormatch]
      [--no-unsharp] [--grain 1.0] [--debug-dir DIR]
"""
import argparse, os, subprocess, sys
import numpy as np
import cv2

W_OUT, H_OUT = 576, 1024  # output geometry = original reel resolution


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h, fr = r.stdout.strip().split(",")
    num, den = fr.split("/")
    return int(w), int(h), float(num) / float(den)


def read_frames(path, w, h):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
           "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 8)
    n = w * h * 3
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    p.stdout.close(); p.wait()


class Writer:
    def __init__(self, path, w, h, fps):
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
             "-c:v", "libx264", "-crf", "16", "-preset", "medium",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", path],
            stdin=subprocess.PIPE)

    def write(self, frm):
        self.p.stdin.write(frm.tobytes())

    def close(self):
        self.p.stdin.close(); self.p.wait()


def diff_matte(sw_frames, orig_aligned, thr_pct=82, debug=None):
    """Person matte from |swapped - original| difference. Backgrounds are
    near-identical, so the diff concentrates on the swapped person."""
    mats = []
    acc = None
    for i, (sw, og) in enumerate(zip(sw_frames, orig_aligned)):
        d = cv2.absdiff(cv2.cvtColor(sw, cv2.COLOR_BGR2GRAY),
                        cv2.cvtColor(og, cv2.COLOR_BGR2GRAY))
        d = cv2.GaussianBlur(d, (5, 5), 1.2)
        mats.append(d)
    for d in mats:
        t = np.percentile(d, thr_pct)
        m = (d > max(t, 14)).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # keep large components only
        ncc, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        keep = np.zeros_like(m)
        for k in range(1, ncc):
            if stats[k, cv2.CC_STAT_AREA] > 0.02 * m.size:
                keep[lab == k] = 255
        keep = cv2.dilate(keep, np.ones((5, 5), np.uint8), iterations=2)
        mats_out = keep
        yield keep
    return


def temporal_median(alpha_seq):
    """alpha_seq: list of HxW uint8 -> per-pixel median of neighbors (t-1,t,t+1)."""
    out = []
    for i in range(len(alpha_seq)):
        a = alpha_seq[max(0, i - 1)]
        b = alpha_seq[i]
        c = alpha_seq[min(len(alpha_seq) - 1, i + 1)]
        out.append(np.median(np.stack([a, b, c]), axis=0).astype(np.uint8))
    return out


def estimate_grain(frame, x0=8, y0=8, s=110):
    patch = cv2.cvtColor(frame[y0:y0 + s, x0:x0 + s], cv2.COLOR_BGR2GRAY).astype(np.float32)
    med = cv2.medianBlur(patch.astype(np.uint8), 3).astype(np.float32)
    return float(np.std(patch - med))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--swapped", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--matte", default="auto", choices=["auto", "diff", "rembg", "none"])
    ap.add_argument("--grain", type=float, default=1.0)
    ap.add_argument("--no-colormatch", action="store_true")
    ap.add_argument("--no-unsharp", action="store_true")
    ap.add_argument("--debug-dir", default=None)
    args = ap.parse_args()

    sw_w, sw_h, sw_fps = probe(args.swapped)
    og_w, og_h, og_fps = probe(args.original)
    print(f"[*] swapped {sw_w}x{sw_h}@{sw_fps:.0f} | original {og_w}x{og_h}@{og_fps:.0f}")

    # --- load both frame sequences ------------------------------------------
    sw = list(read_frames(args.swapped, sw_w, sw_h))
    og = list(read_frames(args.original, og_w, og_h))
    print(f"[*] frames: swapped={len(sw)} original={len(og)}")
    n = min(len(sw), len(og))

    # time-normalized mapping: swapped frame j -> original frame idx
    idx_map = [round(j * (len(og) - 1) / max(1, len(sw) - 1)) for j in range(len(sw))]

    # --- upscale swapped to output geometry (+unsharp) -----------------------
    sw_up = []
    for f in sw:
        u = cv2.resize(f, (W_OUT, H_OUT), interpolation=cv2.INTER_LANCZOS4)
        if not args.no_unsharp:
            blur = cv2.GaussianBlur(u, (0, 0), 1.0)
            u = cv2.addWeighted(u, 1.45, blur, -0.45, 0)
        sw_up.append(u)
    og_up = [cv2.resize(f, (W_OUT, H_OUT), interpolation=cv2.INTER_LANCZOS4) for f in og]

    # --- global alignment check via phase correlation ------------------------
    g1 = cv2.cvtColor(cv2.resize(sw_up[0], (144, 256)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(cv2.resize(og_up[idx_map[0]], (144, 256)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    (dx, dy), _ = cv2.phaseCorrelate(g2, g1)
    print(f"[*] global alignment shift: dx={dx:.2f}px dy={dy:.2f}px (output scale)")
    if abs(dx) > 3 or abs(dy) > 3:
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        sw_up = [cv2.warpAffine(f, M, (W_OUT, H_OUT)) for f in sw_up]

    # --- matte ---------------------------------------------------------------
    use_diff = args.matte in ("auto", "diff")
    alphas = None
    if use_diff:
        og_small = [cv2.resize(og[i], (sw_w, sw_h), interpolation=cv2.INTER_AREA) for i in idx_map]
        raw = list(diff_matte(sw, og_small))
        alphas = temporal_median(raw)
        cov = float(np.mean([a > 0 for a in alphas]))
        print(f"[*] diff matte coverage: {cov*100:.1f}% of frame")
        if cov < 0.02 or cov > 0.75:
            print("[!] diff matte implausible -> fallback")
            alphas = None
    if alphas is None and args.matte in ("auto", "rembg"):
        try:
            from rembg import remove, new_session
            sess = new_session("u2net")
            alphas = []
            for f in sw:
                r = remove(cv2.cvtColor(f, cv2.COLOR_BGR2RGB), session=sess)
                a = np.array(r)[:, :, 3]
                alphas.append(cv2.dilate(a, np.ones((3, 3), np.uint8)))
            alphas = temporal_median(alphas)
            print("[*] rembg matte used")
        except Exception as e:
            print(f"[!] rembg unavailable ({e}); matte=none (upscale-only)")
            alphas = None
    if args.matte == "none":
        alphas = None

    # --- color match (global LAB stats over person region) -------------------
    if alphas and not args.no_colormatch:
        acc_sw, acc_og = [], []
        for j, a in enumerate(alphas):
            m = (cv2.resize(a, (sw_w, sw_h)) > 140)
            if m.sum() < 500:
                continue
            lab_s = cv2.cvtColor(sw[j], cv2.COLOR_BGR2LAB)
            lab_o = cv2.cvtColor(og_small[j], cv2.COLOR_BGR2LAB)
            acc_sw.append(lab_s[m].astype(np.float32))
            acc_og.append(lab_o[m].astype(np.float32))
        if acc_sw:
            ms, ss = np.vstack(acc_sw).mean(0), np.vstack(acc_sw).std(0) + 1e-6
            mo, so = np.vstack(acc_og).mean(0), np.vstack(acc_og).std(0) + 1e-6
            ratio = np.clip(so / ss, 0.7, 1.4)
            print(f"[*] color match: dL={ms[0]-mo[0]:.1f} ratio={ratio.round(2).tolist()}")
        else:
            ms = ss = ratio = mo = None
    else:
        ms = None

    # --- grain target --------------------------------------------------------
    grain_sigma = estimate_grain(og[len(og) // 2]) * args.grain
    print(f"[*] target grain sigma: {grain_sigma:.2f}")

    # --- composite loop ------------------------------------------------------
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    wr = Writer(args.out, W_OUT, H_OUT, 30)
    rng = np.random.default_rng(7)
    dbg = args.debug_dir
    if dbg:
        os.makedirs(dbg, exist_ok=True)
    for j in range(len(sw_up)):
        person = sw_up[j]
        alpha = None
        if alphas is not None:
            a = cv2.resize(alphas[j], (W_OUT, H_OUT), interpolation=cv2.INTER_LINEAR)
            a = cv2.GaussianBlur(a, (7, 7), 2.0)
            alpha = (a.astype(np.float32) / 255.0)[..., None]
            # color match applied in LAB on the upscaled person
            if ms is not None:
                lab = cv2.cvtColor(person, cv2.COLOR_BGR2LAB).astype(np.float32)
                lab = (lab - ms) * ratio + mo
                person = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
        if alpha is None:
            out = person
        else:
            out = og_up[idx_map[j]].astype(np.float32) * (1 - alpha) + person.astype(np.float32) * alpha
            out = out.astype(np.uint8)
        # grain only on person region (background is original -> already has grain)
        if alpha is not None and grain_sigma > 0.6:
            noise = rng.normal(0, grain_sigma, out.shape).astype(np.float32)
            out = np.clip(out.astype(np.float32) + noise * alpha, 0, 255).astype(np.uint8)
        wr.write(out)
        if dbg and j in (5, len(sw_up) // 3, len(sw_up) // 2, 3 * len(sw_up) // 4, len(sw_up) - 6):
            side = np.hstack([og_up[idx_map[j]], out, person])
            cv2.imwrite(os.path.join(dbg, f"cmp_{j:04d}.jpg"), side,
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
            cv2.imwrite(os.path.join(dbg, f"alpha_{j:04d}.png"), alphas[j] if alphas else np.zeros((sw_h, sw_w), np.uint8))
    wr.close()
    print("[*] wrote", args.out)


if __name__ == "__main__":
    main()
