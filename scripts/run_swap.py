#!/usr/bin/env python3
"""
run_swap.py v2 — Wan2.2-Animate character swap on HF ZeroGPU.

v2 improvements over v1:
  * ANONYMOUS-FIRST: no HF token needed — anonymous ZeroGPU quota is PER-IP,
    and each GitHub Actions runner gets a fresh IP (matrix jobs = fresh quota each).
  * Saves ALL intermediate artifacts (pose / bg / mask / face videos) for QA —
    these let you diagnose identity/mask failures without re-running.
  * Error taxonomy via exit codes so CI can react:
      3 = FATAL (bad input / API gone)
      4 = QUOTA (exhausted on this IP -> rerun on a fresh runner)
      5 = RETRYABLE (space sleeping/starting -> backoff and retry here)
  * Fallback spaces: tries a list of Wan-Animate ZeroGPU spaces in order.

Usage:
  python3 run_swap.py <input_video> <ref_image> <out_dir> \
      [--duration N] [--resolution "Low Res"|"Medium Res"] [--space URL] \
      [--hf-token TOKEN] [--attempts N]
"""
import argparse, os, shutil, sys, time

QUOTA_MARKERS = ("exceeded zerogpu", "quota", "runs limit", "gpu quota")
RETRY_MARKERS = ("sleeping", "starting", "building", "paused", "timeout",
                 "connection error", "503", "502", "no gpu was available",
                 "queue", "task aborted", "aborted")


def classify(err_text: str) -> int:
    t = err_text.lower()
    if any(m in t for m in QUOTA_MARKERS):
        return 4
    if any(m in t for m in RETRY_MARKERS):
        return 5
    return 3


def save_result_file(res, out_dir, name):
    if res is None:
        return None
    src = None
    if isinstance(res, (list, tuple)):
        res = res[0]
    if isinstance(res, dict):
        src = res.get("video") or res.get("path")
    else:
        src = res
    if src and os.path.exists(src):
        dst = os.path.join(out_dir, name)
        shutil.copy(src, dst)
        return dst
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_video")
    ap.add_argument("ref_image")
    ap.add_argument("out_dir")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="Low Res", choices=["Low Res", "Medium Res"])
    ap.add_argument("--space", default="alexnasa/Wan2.2-Animate-ZEROGPU")
    ap.add_argument("--fallback-spaces", default="")  # comma-separated
    ap.add_argument("--hf-token", default=None)
    ap.add_argument("--hf-token-file", default=None)
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--start-delay", type=int, default=0)  # stagger matrix jobs
    ap.add_argument("--rc-mode", default="Character Swap")  # or "Pose Retarget"
    args = ap.parse_args()

    token = args.hf_token or os.environ.get("HF_TOKEN")
    if not token and args.hf_token_file and os.path.exists(args.hf_token_file):
        token = open(args.hf_token_file).read().strip()

    from gradio_client import Client, handle_file

    spaces = [args.space] + [s for s in args.fallback_spaces.split(",") if s.strip()]
    os.makedirs(args.out_dir, exist_ok=True)

    last_err = ""
    if args.start_delay > 0:
        print(f"[*] staggering: sleeping {args.start_delay}s", flush=True)
        time.sleep(args.start_delay)
    for space in spaces:
        for attempt in range(1, args.attempts + 1):
            try:
                print(f"[*] connecting {space} (attempt {attempt}/{args.attempts}, "
                      f"{'token' if token else 'anonymous'})", flush=True)
                client = Client(space, token=token, verbose=False)
                t0 = time.time()
                result = client.predict(
                    input_video=handle_file(args.input_video),
                    max_duration_s=args.duration,
                    edited_frame=handle_file(args.ref_image),
                    rc_str=args.rc_mode,
                    resolution_choice=args.resolution,
                    api_name="/animate_scene",
                )
                print(f"[*] finished in {time.time()-t0:.1f}s", flush=True)

                # result = (final_video, pose_video, bg_video, mask_video, face_video)
                names = ["swapped.mp4", "qa_pose.mp4", "qa_bg.mp4", "qa_mask.mp4", "qa_face.mp4"]
                if isinstance(result, (list, tuple)):
                    for r, n in zip(result, names):
                        p = save_result_file(r, args.out_dir, n)
                        if p and n != "swapped.mp4":
                            print(f"[*] QA artifact: {p}", flush=True)
                else:
                    save_result_file(result, args.out_dir, "swapped.mp4")
                print("[*] saved:", os.path.join(args.out_dir, "swapped.mp4"), flush=True)
                return 0

            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)}"
                code = classify(last_err)
                print(f"[!] attempt {attempt} failed ({code}): {last_err[:500]}", flush=True)
                if code == 4:
                    print("[!] QUOTA exhausted on this IP -> exit 4 (CI should rerun on fresh runner)")
                    return 4
                if code == 5 and attempt < args.attempts:
                    wait = 60 * attempt if "no gpu" in last_err.lower() else 45 * attempt
                    print(f"[*] retryable -> sleeping {wait}s", flush=True)
                    time.sleep(wait)
                elif code == 3:
                    break  # fatal for this space -> try next space
        print(f"[!] space {space} exhausted/unavailable, trying next", flush=True)

    print(f"[!] all attempts failed. last error: {last_err[:500]}")
    return 4 if classify(last_err) == 4 else 3


if __name__ == "__main__":
    sys.exit(main())
