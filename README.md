# Mr. Bean Motion Swap Pipeline 🎬🫘

**Mission:** replace Jackie Chan (the second subject) in a TikTok-style reel with
**Mr. Bean**, replicating the exact full-body motions — face-swap is NOT enough,
the entire character (head, clothing, body) must be re-rendered following the
original skeleton.

Source reel: https://github.com/ssmurfgg04-gif/jackie-chan (`53140.mp4`,
576x1024@30fps, 8.77s, with audio). Cut analysis: frames 0-96 = creator intro,
frames 97-262 = Jackie Chan segment (~5.53s).

## Route chosen (no local GPU needed)

**Wan2.2-Animate 14B "Character Swap" mode** — Alibaba's open-source full-body
character replacement model — executed on **HuggingFace ZeroGPU**
(space `alexnasa/Wan2.2-Animate-ZEROGPU`) via `gradio_client`.

Why this route:
- Local machine: 2-core CPU / 4GB RAM / no GPU → local diffusion impossible.
- GitHub-hosted Actions runners are CPU-only (GPU runners need paid plans).
- ZeroGPU gives free daily H200 quota to any HF account; ~150-210s of GPU covers
  a 5-6s swap at 360x640 ("Low Res").
- GitHub Actions (`ubuntu-latest`, free) handles all CPU orchestration: ffmpeg
  cutting, ref prep, API calls, audio re-attach.

## Repository layout

```
.github/workflows/swap-character.yml   # one-click swap job on GH Actions (CPU only)
scripts/run_swap.py                    # ZeroGPU Wan2.2-Animate API client
scripts/reassemble.py                  # intro + swapped segment + audio reassembly
swap_spec.json                         # parameters used for the shipped test
```

## Setup (one-time)

1. Create a HuggingFace account and a **read access token** (free).
2. Add it as repo secret `HF_TOKEN` (Settings → Secrets and variables → Actions).
3. Trigger **swap-character** from the Actions tab with:
   - `input_video_url`: raw URL of the reel
   - `ref_image_url`: raw URL of the Mr. Bean reference image
   - `cut_start=3.2333`, `cut_end=8.766667` (the Jackie segment)
   - `duration=5`, `resolution=Low Res`

## Local usage

```bash
pip install gradio_client huggingface_hub
export HF_TOKEN=hf_xxx

# 1) cut the swap segment (frame 97 @30fps => 3.2333s)
ffmpeg -ss 3.2333 -to 8.766667 -i 53140.mp4 jackie_segment.mp4

# 2) run the swap on ZeroGPU
python3 scripts/run_swap.py jackie_segment.mp4 bean_ref.png output --duration 5

# 3) reassemble full reel (intro + swapped + original audio)
python3 scripts/reassemble.py --source 53140.mp4 --swapped output/bean_swapped.mp4 \
    --cut-frame 97 --out final_reel.mp4
```

## Notes & limits

- ZeroGPU free quota is a few minutes of H200/day per account; "Medium Res"
  (480x832) costs 2x and may exceed it.
- "Character Swap" mode keeps the original background and lighting; "Pose
  Retarget" mode would re-render Mr. Bean on a new background instead.
- Ref image quality drives identity fidelity: use a clean, front-facing,
  waist-up photo (classic tweed + red tie).
- Responsible use: for personal/creative parody testing. Do not use outputs to
  deceive; respect likeness rights of real people.

## Results (shipped test)

| Artifact | Description |
|---|---|
| `results/final_reel_v2.mp4` | **Final reel**: original intro (frames 0-96) + full Mr. Bean segment (5.5s) + original audio |
| `results/final_reel_v1.mp4` | First delivery (segment capped at 4.97s by anonymous ZeroGPU quota) |
| `results/bean_segment_full.mp4` | Complete swapped segment (v1 + chunk2 stitched w/ 0.25s crossfade) |
| `results/bean_swapped.mp4` | Raw Wan2.2-Animate output, chunk 1 (352x640, 149 frames) |
| `results/bean_swapped_chunk2.mp4` | Raw output, chunk 2 (segment tail, 90 frames) |
| `results/critique/` | 4-round VLM critique reports (JSON + motion-comparison grids) |

### VLM critique summary (4 rounds)

1. **Overall**: 7/10 — clean reel structure, intentional hard cut preserved; some ghosting at jawline typical of the model at 352x640.
2. **Identity**: consistent single identity across the segment, no Jackie Chan leakage, features stable frame-to-frame. Model renders a *younger* Mr. Bean (ref image is classic-era Bean).
3. **Motion fidelity** (5 matched-frame pairs vs original): same gestures, head positions and framing in 5/5; only fast-motion hand shows extra blur.
4. **Technical**: background (curtains/painting/wall) preserved from source; hands render correctly; upscale from 360x640 visible but acceptable.

### How the no-GPU route actually ran

- GitHub Actions `ubuntu-latest` (free, CPU-only) did all orchestration.
- The swap itself ran on HuggingFace ZeroGPU via `gradio_client`, **anonymously** — anonymous ZeroGPU quota is per-IP and the fresh Actions runner IPs had enough for 150s GPU jobs (duration<=5s, Low Res). `duration=6` needs 210s > anonymous cap, so the segment was split into two chunks and crossfaded.
- For unattended future runs, add an HF token as the `HF_TOKEN` repo secret (higher quota, Medium Res possible).
