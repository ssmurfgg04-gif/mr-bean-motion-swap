#!/usr/bin/env python3
"""
Wan2.2-Animate Character Swap — runs a full-body character replacement job
on HuggingFace ZeroGPU (space: alexnasa/Wan2.2-Animate-ZEROGPU) via gradio_client.

This is the core "swap" step of the Jackie Chan -> Mr. Bean pipeline.

Usage:
  python3 run_swap.py <input_video> <ref_image> <out_dir> [--hf-token TOKEN|--hf-token-file FILE]
                      [--duration N] [--resolution "Low Res"|"Medium Res"]

Env:
  HF_TOKEN  - alternative to --hf-token
"""
import argparse, os, shutil, sys, time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_video")
    ap.add_argument("ref_image")
    ap.add_argument("out_dir")
    ap.add_argument("--hf-token", default=None)
    ap.add_argument("--hf-token-file", default=None)
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--resolution", default="Low Res")
    ap.add_argument("--space", default="alexnasa/Wan2.2-Animate-ZEROGPU")
    args = ap.parse_args()

    token = args.hf_token or os.environ.get("HF_TOKEN")
    if not token and args.hf_token_file and os.path.exists(args.hf_token_file):
        token = open(args.hf_token_file).read().strip()
    if not token:
        print("ERROR: no HF token given (anonymous ZeroGPU quota is almost always exhausted)", file=sys.stderr)
        sys.exit(2)

    from gradio_client import Client, handle_file

    client = Client(args.space, token=token, verbose=False)
    print(f"[*] connected to {args.space}", flush=True)

    t0 = time.time()
    try:
        result = client.predict(
            input_video=handle_file(args.input_video),
            max_duration_s=args.duration,
            edited_frame=handle_file(args.ref_image),
            rc_str="Character Swap",
            resolution_choice=args.resolution,
            api_name="/animate_scene",
        )
    except Exception as e:
        print(f"[!] PREDICT FAILED ({type(e).__name__}): {str(e)[:2000]}", flush=True)
        raise SystemExit(3)
    print(f"[*] finished in {time.time()-t0:.1f}s", flush=True)

    os.makedirs(args.out_dir, exist_ok=True)
    edited = result[0] if isinstance(result, (list, tuple)) else result
    if isinstance(edited, dict):
        src = edited.get("video") or edited.get("path")
    else:
        src = edited
    out_path = os.path.join(args.out_dir, "bean_swapped.mp4")
    shutil.copy(src, out_path)
    print("[*] saved:", out_path, flush=True)


if __name__ == "__main__":
    main()
