#!/usr/bin/env python3
"""
Reassemble the final reel: intro (original creator) + swapped segment (Mr. Bean)
with the original audio laid back on top of the swapped segment.

Usage:
  python3 reassemble.py --source 53140.mp4 --swapped bean_swapped.mp4 \
      --cut-frame 97 --out final_reel.mp4
"""
import argparse, subprocess, tempfile, os, sys


def probe_frames(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="original reel")
    ap.add_argument("--swapped", required=True, help="swapped segment (with or without audio)")
    ap.add_argument("--cut-frame", type=int, default=97, help="first frame of the swap segment")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--out", default="final_reel.mp4")
    args = ap.parse_args()

    sw_frames = probe_frames(args.swapped)
    print(f"[*] swapped segment frames: {sw_frames}")
    if not sw_frames:
        sys.exit("could not count frames of swapped video")

    intro_end = args.cut_frame / args.fps
    swap_dur = sw_frames / args.fps

    tmp = tempfile.mkdtemp()
    intro_path = os.path.join(tmp, "intro.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.source,
                    "-t", f"{intro_end:.6f}", "-an",
                    "-c:v", "libx264", "-crf", "16", "-preset", "ultrafast",
                    intro_path], check=True)

    seg_audio = os.path.join(tmp, "seg_audio.m4a")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{intro_end:.6f}",
                    "-t", f"{swap_dur:.6f}", "-i", args.source,
                    "-vn", "-c:a", "aac", "-b:a", "128k", seg_audio], check=True)

    swapped_norm = os.path.join(tmp, "swapped_norm.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.swapped,
                    "-i", seg_audio,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "libx264", "-crf", "16", "-preset", "ultrafast",
                    "-vf", "scale=576:1024:force_original_aspect_ratio=decrease,pad=576:1024:-1:-1",
                    "-c:a", "copy", "-shortest", swapped_norm], check=True)

    concat_list = os.path.join(tmp, "list.txt")
    with open(concat_list, "w") as f:
        f.write(f"file '{intro_path}'\nfile '{swapped_norm}'\n")

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", concat_list, "-c:v", "libx264", "-crf", "17",
                    "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", args.out], check=True)
    print("[*] wrote", args.out)


if __name__ == "__main__":
    main()
