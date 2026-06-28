# NegPy — Improvement Brainstorm

> Status: **ideas only — nothing implemented yet.** This is a working backlog of
> candidate improvements across UX/UI, neg→pos accuracy, and performance, with
> enough technical grounding to turn each into a ticket. Items are tagged with a
> rough **Impact** (H/M/L) and **Effort** (S/M/L) so they can be triaged.

Scope reviewed: pipeline (`negpy/services/rendering`, `negpy/features/*`),
desktop UI (`negpy/desktop/*`), docs (`docs/PIPELINE.md`, `docs/USER_GUIDE.md`).

---

## 1. UX / UI

### 1.1 Persistence & data safety

- **Auto-save / crash safety (Impact H, Effort M).** The user guide states plainly:
  *"If you close the app without saving, your edits/settings will be lost."*
  ([`docs/USER_GUIDE.md`](USER_GUIDE.md) §Additional Info). This is the single
  biggest footgun in the product. Edits already persist to SQLite on
  file-change/export/explicit save — extend that to:
  - Persist the working config on a short idle-debounce (e.g. 2–3 s after the
    last edit) and on `closeEvent`.
  - Or, at minimum, a dirty-state guard: a "You have unsaved edits" prompt on
    quit when `state.is_dirty`.
  - Consider a periodic journaled autosave so a hard crash recovers the session.
- **Undo persistence vs. session loss mismatch (Impact M, Effort S).** History is
  kept (up to 100 steps) but lost if the session isn't saved. Clarify in-UI when
  history will survive vs. not, or just make autosave moot the issue.
- **"Edits are unsaved" indicator (Impact M, Effort S).** A visible dirty marker
  (title-bar asterisk / status pill) so users know their state. Currently
  `state.is_dirty` exists but isn't surfaced prominently.

### 1.2 Onboarding & discoverability

- **First-run guided flow (Impact H, Effort M).** There's a `tutorial_overlay`,
  but the natural workflow (Import → pick mode → crop → batch-normalize →
  grade → export) is non-obvious. A dismissible step strip or a "Getting
  Started" checklist that lights up as the user completes each stage would help
  new film shooters who don't know darkroom vocabulary.
- **Plain-language tooltips / "what does this do" mode (Impact M, Effort M).** The
  pipeline is physically rich (H&D curve, ISO-R grade, CMY in log space) but the
  terms are jargon. Add a beginner/expert toggle, or expandable "?" popovers per
  control sourced from `USER_GUIDE.md` so the docs live next to the slider.
- **Grade scale confusion (Impact M, Effort S).** `PIPELINE.md` says Grade is now
  ISO-R (50–180, default 115) with auto-migration from the old 0–5 scale, but
  `USER_GUIDE.md` §5 still documents **Grade (0.0–5.0)**. The docs contradict the
  code — pick one scale, and make sure the slider label/units match. Display both
  ("R115 ≈ Grade 2") to bridge mental models.

### 1.3 Workflow friction

- **Batch normalization requires the right file open first (Impact M, Effort M).**
  `request_batch_normalization()` pulls Analysis Buffer / D-Range Clip from
  *whatever frame is currently open* and applies them to the whole roll. This is
  surprising and is patched over with a long `QMessageBox` explanation. Better:
  expose buffer/d-range as explicit fields inside the Batch Analysis dialog, with
  a live preview of the sampled region, instead of relying on hidden current-frame
  state.
- **Crop-before-normalize coupling is implicit (Impact M, Effort M).** Accuracy
  depends heavily on cropping out rebate/borders before bounds detection (the
  dialog spends 3 paragraphs explaining this). Consider: auto-detect "this frame
  has a large border" and nudge the user, or default Analysis Buffer based on
  detected border width.
- **Sync Edits semantics (Impact L, Effort S).** "Sync Edits applies current
  settings to selected images (excludes crop and rotation)." Make the exclusions
  visible (a checklist of what will/won't copy) to prevent surprise.
- **Keyboard-driven culling (Impact M, Effort M).** For roll workflows, arrow-key
  next/prev plus a flag/reject/rating system and a filter for it would make NegPy
  usable as a one-stop culler, reducing round-trips to a DAM. (There's already a
  filter + sort; a rating attribute on the asset record would extend it.)

### 1.4 Canvas & comparison

- **Compare is binary before/after only (Impact M, Effort M).** `toggle_compare()`
  shows current vs. the auto-baseline (creative sections reset). Add:
  - A split/curtain wipe view instead of full toggle.
  - Compare against *another frame* or a saved snapshot, not just the baseline.
  - A/B between two preset looks.
- **No histogram/clipping warnings on canvas (Impact M, Effort M).** There's a
  histogram in the Analysis tab, but no on-canvas clipping overlay (blinkies) for
  blown highlights / crushed blacks — very useful given the pipeline deliberately
  leaves headroom unclamped at normalization.
- **Pixel readout is RGB/Lab only (Impact L, Effort S).** Consider adding a
  density readout (log) too, since the whole model is density-based — power users
  setting bounds would benefit.

### 1.5 Platform parity

- **Scanner support is Linux/macOS-only (Impact M, Effort L).** Windows shows a
  placeholder ([`controls_panel`], SANE-based). Document the limitation more
  prominently, and consider a TWAIN/WIA path for Windows or at least a
  clearer "use your scanner's software, then hot-folder into NegPy" guidance.
- **Hot Folder + scanner is the intended tethered flow (Impact L, Effort S).**
  Make that pairing a first-class, documented "tethered scanning" mode rather
  than two separate toggles users must discover.

### 1.6 Polish / small wins

- Typo: `session.py` comment "Full Resoluiton" (cosmetic).
- Surface the GPU-fallback notice better — currently a one-time 5 s status
  message; a persistent badge ("Running on CPU") would reduce "why is it slow"
  confusion.
- `ROADMAP.md` is stale (lists flat-field as TODO though it's implemented).
  Refresh it so contributors trust it.

---

## 2. Neg→Pos pipeline accuracy

### 2.1 Normalization / bounds detection

- **Percentile bounds are per-channel-independent extremes (Impact H, Effort M).**
  `analyze_log_exposure_bounds()` takes per-channel `p_low`/`p_high` percentiles
  independently. For frames with a strong dominant color (sunset, deep blue sky,
  foliage) this couples the orange-mask removal to scene content and can produce
  channel-dependent casts. Worth experimenting with:
  - Estimating film-base density from the **rebate / unexposed leader** when
    available (true D-min) rather than from image percentiles, and only using
    percentiles as a fallback.
  - A constrained model where inter-channel offsets are regularized toward the
    known C-41 mask slope instead of fully free per channel.
- **Block-median grid is fixed-size (`analysis_grid`) (Impact M, Effort S).** Good
  for resolution invariance, but on very high-res scans a single 1024-grid may
  blur small but real low-density regions. Consider an adaptive grid or a second
  pass at native res for the final bounds.
- **Robust batch mean uses IQR (25–75) hard mask (Impact M, Effort S).** In
  `NormalizationWorker.get_robust_mean`, channels with <5 files fall back to plain
  mean (outlier-sensitive). For short rolls (common with 120/large format) a
  trimmed mean or median would be more robust than dropping the outlier rejection
  entirely.
- **Bounds are scene-dependent even with Lock (Impact M, Effort M).** Document /
  surface clearly when bounds re-analyze (crop, slider moves) vs. when they're
  frozen; the controller invalidates local bounds in many places
  (`invalidate_local_bounds`). A small "bounds source: auto / locked / roll" badge
  would make the accuracy model legible and debuggable.

### 2.2 Color / mask neutralization

- **Cast Removal anchors green and is bounded ±0.125 (Impact M, Effort M).**
  Reasonable, but green-as-reference assumes a roughly neutral green layer. For
  tungsten-lit or heavily filtered scenes this can be wrong. Consider a
  user-selectable reference channel or a gray-world/edge-based estimator as an
  alternate Cast Removal model, A/B-testable against the current one.
- **Crosstalk matrices are user-suppliable but un-validated (Impact L, Effort S).**
  `.toml` matrices in `crosstalk/` — add validation (matrix conditioning,
  normalization sanity) and a visual preview swatch so bad matrices don't silently
  wreck color.
- **WB pick damping differs log vs. linear (Impact L, Effort S).** In
  `_handle_wb_pick`, the log path snaps fully while the linear path damps 0.4.
  Inconsistent feel; unify or expose the damping.

### 2.3 Tone / print model

- **Hard-coded constants in the H&D curve (Impact M, Effort M).** `D_min=0.06`,
  `D_asym=2.7`, `D_max=2.3`, `ν=3.0` are baked. These are paper characteristics —
  exposing a small set of **paper profiles** (the Toning panel already has paper
  profiles for tint) that also carry curve params would let users emulate
  different print papers (graded vs. VC, glossy vs. matte D-max) more faithfully.
- **Auto Density / Auto Grade are global per-frame (Impact M, Effort L).** They
  meter median/textural range globally. Spatially-aware metering (center-weighted
  or subject-detected) would handle backlit / spotlit frames better. Even a simple
  center-weight toggle would help portraits.
- **Lab USM has hard-coded magic numbers (Impact L, Effort S).** `PIPELINE.md` §5
  lists `2.5` boost and `2.0` threshold as hardcoded. Make threshold scale-aware
  (it's applied to L in a fixed range) so sharpening behaves consistently between
  preview and full-res export.

### 2.4 CPU/GPU parity (correctness risk)

- **Parity tolerances are intentionally loose (Impact H, Effort L–M).**
  `tests/test_pipeline_parity.py` has multiple `TODO: tighten tolerance` markers
  (1e-3 etc.) "after CPU/GPU implementations converge." This means the preview
  (GPU) and export (could be either) can differ visibly. Prioritize closing the
  parity gap per-stage and ratcheting tolerances down — otherwise "what I saw
  isn't what I exported" bugs are latent. Add a per-stage parity diff harness to
  localize which shader drifts.
- **Toning/Finish aren't cached on the CPU path (Impact L, Effort S).** Engine
  re-runs `ToningProcessor`/`CropProcessor`/`FinishProcessor` every call
  (`engine.py` 145–147). Functionally fine, but means these stages can't be
  checkpointed and any nondeterminism there shows up every render.

### 2.5 Retouch / dust

- **IR-channel dust is opportunistic (Impact M, Effort M).** When an IR channel
  exists it's used; most scanners don't expose it here. The statistical detector's
  cubic variance penalty is tuned to avoid foliage false-positives but can miss
  dust on busy backgrounds. A user-paintable "protect/erase" mask over the auto
  detection would give an escape hatch without manual spot-by-spot work.
- **`manual_dust_size` int cast loses precision (Impact L, Effort S).** Known TODO
  in `retouch.py` line ~90. Store float, round only at apply time.

---

## 3. Performance / optimization

### 3.1 Rendering throughput

- **Single in-flight render + 80 ms debounce (Impact M, Effort M).**
  `request_render` keeps one render in flight and stashes the latest pending task
  (`_pending_render_task`). Combined with the 80 ms debounce this is reasonable,
  but for slider drags it can feel laggy on CPU. Options:
  - Render the preview at a reduced size during active drag, full size on
    release ("interactive vs. quality" tiers).
  - Use the GPU stage-invalidation (`_detect_invalidated_stage`) more
    aggressively so slider drags only re-run the touched stage.
- **Process panel forces CPU (Impact M, Effort L).** Per `USER_GUIDE.md`, the
  Process section (analysis buffer, white/black offsets, normalize) is CPU-only.
  Those are exactly the controls users scrub while dialing in a scan. Moving the
  bounds-stretch and metering to GPU (or caching the expensive analysis and only
  re-stretching on offset changes) would make them feel instant.
- **Bounds re-analysis on every offset change (Impact M, Effort M).** White/Black
  point offsets are described as "without re-running the analysis," but verify the
  offset path truly reuses cached `log_bounds` rather than recomputing percentiles.
  If it recomputes, cache the analysis and apply offsets as a cheap post-step.

### 3.2 Memory

- **Preview cache default ~1.2 GB / 8 entries (Impact M, Effort S).** Good for
  fast machines; punishing on low-RAM laptops (already tunable via
  `override.toml`). Consider auto-sizing the budget from detected system RAM at
  first run instead of a fixed default, and surface the setting in the UI (not
  just a TOML file users won't find).
- **Full-res buffers held per file for prefetch (Impact M, Effort M).** Prefetch
  warms neighbors into the LRU. For large-format 16-bit scans this is heavy.
  Make prefetch depth and HQ-prefetch conditional on available memory.
- **GPU texture pool keyed by (w,h,usage,label) (Impact L, Effort S).** Fine, but
  confirm pool eviction under memory pressure; `max_texture_size` override exists
  for OOM, but an adaptive downscale-on-OOM during export (instead of failing)
  would be friendlier.

### 3.3 Startup & responsiveness

- **Numba JIT warmup on import (Impact M, Effort S).** `preview_manager` warms
  kernels; cold start still pays first-call compilation. Confirm `cache=True`
  (it is, in `normalization.py`) is effective across versions, and consider
  shipping a pre-warmed cache or AOT compilation in the PyInstaller build to cut
  first-render latency.
- **Asset discovery hashes whole files synchronously (Impact M, Effort M).**
  `AssetDiscoveryWorker` + `calculate_file_hash` hash every file on import; for a
  big folder of large RAWs this is slow. Options: hash a partial fingerprint
  (size + head/tail bytes) for identity, or stream-hash with progress already
  shown but parallelized.
- **`os.listdir` non-recursive, single-threaded crawl (Impact L, Effort S).**
  Discovery walks one level and hashes serially within the worker; parallelizing
  the hash step (it's CPU/IO bound) would speed large imports.

### 3.4 Export

- **Tiled export threshold 12 MP / 2048 tiles (Impact L, Effort M).** Sensible;
  validate seam handling for stages with spatial support (CLAHE, glow/halation,
  vignette, USM) — tile-local CLAHE especially can produce seams. Add an overlap
  /halo to spatial stages during tiling if not already present.
- **Batch export is serial per file (Impact M, Effort M).** `run_batch` processes
  tasks in sequence. Decoding the next file while the GPU finishes the current
  export (pipeline the I/O and compute) would speed roll exports significantly.

---

## 4. Testing / maintainability (supporting accuracy & perf)

- **Lock the CPU↔GPU parity ratchet (Impact H, Effort M).** Turn the loose-tolerance
  TODOs into a tracked metric: per-stage max error reported in CI, with a budget
  that can only decrease. This directly protects preview/export fidelity.
- **Perceptual regression fixtures (Impact M, Effort M).** `tests/metrics/`
  downloads real RAWs. Add golden-image perceptual diffs (ΔE / SSIM) for a small
  set of reference negatives so accuracy changes are caught, not just numerical
  kernel behavior.
- **Document contradictions (Impact M, Effort S).** Reconcile `USER_GUIDE.md`
  (Grade 0–5, Density 0–2) with `PIPELINE.md` (ISO-R 50–180) and the actual
  slider configs. A single source-of-truth for control ranges/units (generated
  from the config dataclasses) would prevent drift.

---

## 5. Suggested prioritization (first pass)

| # | Item | Impact | Effort | Why first |
|---|------|--------|--------|-----------|
| 1 | Auto-save / unsaved-edits guard (§1.1) | H | M | Prevents real data loss today |
| 2 | CPU/GPU parity ratchet (§2.4, §4) | H | M | Preview≠export is a trust bug |
| 3 | Grade scale doc/UI reconciliation (§1.2) | M | S | Cheap, removes confusion |
| 4 | Interactive low-res drag + GPU Process stage (§3.1) | M | M | Biggest "feels slow" win |
| 5 | On-canvas clipping/compare improvements (§1.4) | M | M | High-value editing feedback |
| 6 | Paper profiles carrying curve params (§2.3) | M | M | Unlocks real creative range |
| 7 | Batch-normalize dialog with explicit fields (§1.3) | M | M | Removes hidden-state surprise |

---

*Next step: pick the items to pursue, and each can be expanded into a design note
with concrete file/function targets (most are already referenced inline above).*
