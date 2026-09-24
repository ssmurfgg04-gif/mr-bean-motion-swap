# Mr. Bean Motion Swap Pipeline v2 🎬🫘

**Mission:** replace Jackie Chan (the second subject) in a TikTok-style reel with
**Mr. Bean**, replicating the exact full-body motions — face-swap is NOT enough,
the entire character (head, clothing, body) must be re-rendered following the
original skeleton.

Source reel: https://github.com/ssmurfgg04-gif/jackie-chan (`53140.mp4`,
576x1024@30fps, 8.77s, with audio). Frames 0-96 = creator intro,
frames 97-262 = Jackie Chan segment (~5.53s).

## What's new in v2

| v1 problem | v2 fix |
|---|---|
| Identity drift: output showed a *generic man*, not the ref character | **Edited-first-frame reference**: build the target character INTO the driving video's first frame (image-edit), pass that as `edited_frame`. This is the single biggest identity lever. |
| VLM critique asked leading questions ("does it look like Mr Bean?") → sycophantic YES | `identity_gate.py`: **neutral forensic comparison** ("is IMAGE 2 the same individual as IMAGE 1?" — identity never named), anchored on a real photo. |
| Anonymous ZeroGPU quota capped clips at ~5s | Formalized the **fresh-IP pattern**: anonymous quota is per-IP; every Actions runner has a fresh IP → **matrix job per chunk, up to 20 parallel runners**. |
| Chunks > cap → blind 5s splits + crossfade seam ghosting | `plan_chunks.py`: **motion-aware chunking** — seams snap to low-motion valleys, budget-sized by resolution (`~40 GPU-s/s Low, ~80 Medium`), overlap context on both sides. |
| Medium Res impossible (2x cost > cap) | Small chunks (~1.2s) fit the cap → **native Medium Res** now feasible, fully parallel. |
| Whole frame re-rendered → background "breathing" | `enhance_swap.py`: **background-plate compositing** — matte the swapped person (diff-matte vs original, temporal-median smoothed), keep ORIGINAL full-res background pixels, LAB color-transfer person to scene, unsharp+lanczos, grain match. |
| Quota failure = dead run | `run_swap.py` v2 error taxonomy (exit 3 fatal / 4 quota / 5 retryable) + **second matrix stage** re-runs missing chunks on fresh runners automatically. |
| Failures undiagnosable | Every chunk uploads QA artifacts: pose / mask / bg / face videos from the space. |

## Pipeline v2 architecture

```
                    ┌──────────────────────────────────────────────┐
 input video ──────►│ plan job (CPU): cut segment, plan_chunks.py  │
 ref (edited 1st ──►│   • activity curve → seam valleys            │
      frame)        │   • budget sizing per resolution             │
                    └──────────────┬───────────────────────────────┘
                                   │ matrix {chunk: [0..N]}
                    ┌──────────────▼───────────────────────────────┐
                    │ N parallel runners (FRESH IP EACH)           │
                    │   cut chunk ±overlap → ZeroGPU Wan2.2-Animate│
                    │   anonymous quota (~140 GPU-s budget/chunk)  │
                    │   upload swapped.mp4 + QA pose/mask/bg/face  │
                    └──────────────┬───────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────────────┐
                    │ assemble (CPU):                              │
                    │   missing chunks? → swap-retry matrix        │
                    │   (new runners = new IPs)                    │
                    │   frame-exact tiling (assemble_chunks.py)    │
                    │   background-plate composite + color match   │
                    │   + unsharp + grain (enhance_swap.py)        │
                    │   mux original audio                         │
                    └──────────────────────────────────────────────┘
```

## The "infinite free GPU" pattern (documented for reuse)

1. Anonymous HuggingFace ZeroGPU quota is **per-IP**, roughly ~160 GPU-seconds.
2. GitHub Actions gives every job a **fresh runner IP** (free for public repos,
   20 parallel jobs on free plans).
3. Therefore: slice work into ≤140-GPU-s chunks, run one **matrix job per
   chunk** — each job consumes its own IP's quota. 20 runners ≈ 2,800 GPU-s
   of H200 time per run, free.
4. Quota failures exit with code 4 → the assemble stage re-runs missing chunks
   as a second matrix → new runners → new IPs.
5. Ethics note: this uses quota as intended (anonymous demos); don't hammer a
   single space — the client rotates fallback spaces and backs off.

## Repository layout

```
.github/workflows/swap-character-v2.yml  # v2 pipeline: plan → matrix swap → assemble
.github/workflows/swap-character.yml     # v1 single-shot pipeline (kept for reference)
scripts/plan_chunks.py                   # motion-aware chunk planner (valleys + GPU budget)
scripts/ci_chunk.sh                      # per-chunk CI step: cut + swap
scripts/ci_assemble.sh                   # completeness check + assemble + enhance + audio
scripts/run_swap.py                      # ZeroGPU client v2 (anonymous-first, QA artifacts, taxonomy)
scripts/assemble_chunks.py               # frame-exact timeline tiling of chunk outputs
scripts/enhance_swap.py                  # post-FX: plate compositing, color match, unsharp, grain
scripts/identity_gate.py                 # neutral VLM identity verification (anti-sycophancy)
scripts/reassemble.py                    # v1 helper: intro + segment + audio
swap_spec.json                           # parameters of the shipped v1 test
```

## Usage

### One-click (GitHub Actions)
Actions → **swap-character-v2** → Run workflow:
- `input_video_url`: direct URL of the driving video
- `ref_image_url`: direct URL of the reference image — **strongly recommended:
  an edited first frame** (target character in the video's first-frame scene,
  pose and lighting). See "Identity recipe" below.
- `cut_start` / `cut_end`: segment bounds in seconds
- `resolution`: `Medium Res` recommended (chunks are small enough now)
- `max_gpu_s`: 140 (stay under the ~160 anonymous cap)

Artifacts: `assembled-result/` (final video + enhancement debug grids) and
`chunk-N/` per chunk (swapped + QA pose/mask/bg/face videos).

### Local

```bash
pip install numpy opencv-python-headless gradio_client
sudo apt install ffmpeg

# 1) plan chunks
python3 scripts/plan_chunks.py segment.mp4 --out plan.json --resolution "Medium Res"

# 2) swap one chunk (anonymous works from any fresh IP)
python3 scripts/run_swap.py chunk.mp4 ref.png out/ --resolution "Medium Res"

# 3) assemble + enhance
python3 scripts/assemble_chunks.py --plan plan.json --chunks-dir chunks --out stitched.mp4
python3 scripts/enhance_swap.py --swapped stitched.mp4 --original segment.mp4 --out final.mp4

# 4) verify identity neutrally (never leading!)
python3 scripts/identity_gate.py real_person.jpg out/first_frame.png --out gate.json
```

## Identity recipe (the lesson from v1)

Do NOT pass a white-background studio portrait and hope the video model
invents the character in-scene. It will drift to a generic person.

Instead, **edit the driving video's first frame first** (any image-edit model):
> "Replace the man's facial identity with [CHARACTER + 3-5 anchor features].
> Dress him in [signature wardrobe]. Do not preserve the man's original facial
> features. Keep his exact body pose, hand positions, camera angle, framing,
> background and lighting unchanged. Photorealistic."

Then pass that edited frame as the reference. The video model's job becomes
*animate this exact character* instead of *solve identity + scene + pose
simultaneously*. Verify with `identity_gate.py` (neutral prompts — the same
person question, never naming the target identity).

## Results

- `results/final_reel_v2.mp4` — v1 pipeline result. **Known issue (found in
  v2 audit): the swapped subject is a generic identity, not Mr. Bean — the v1
  critique's "identity confirmed" verdict was sycophantic.** Kept for
  transparency; superseded by v2 runs.
- v2 run results land in workflow artifacts (`assembled-result/`).
- `results/critique/` — v1 4-round critique reports (methodology deprecated:
  leading prompts, see identity_gate.py for the fixed approach).

## Notes & limits

- Anonymous quota behaves like ~160 GPU-s/IP (may vary); authenticated HF
  tokens raise it — `run_swap.py --hf-token` still supported.
- Chunk outputs may be shorter than requested (model's latent window rounds
  frames); `assemble_chunks.py` trims by actual frame counts and reports gaps.
- ZeroGPU space availability varies; `run_swap.py` rotates fallback spaces and
  retries transient errors with backoff.
- Responsible use: for personal/creative parody testing only. Do not use
  outputs to deceive; respect likeness rights of real people (Rowan Atkinson /
  Mr. Bean is a protected persona).
