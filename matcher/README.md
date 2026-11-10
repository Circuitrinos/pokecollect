# PokéCollect matcher

Identifies cards by their **artwork** (no cloud, no API key). Used by
PokéCollect's scan reader **"Image match (local)"**.

Pipeline per photo:

1. **Find the card outlines**: several edge strategies (gradient contours,
   blurred Canny, grouped convex hulls, straight-line quad fitting).
2. **Straighten** each candidate outline to 630×880.
3. **Shortlist** the ~25k templates. With the DINOv2 model present: the 30 cards
   whose embedding is closest to the photo (0° and 180°, after auto-levels), plus the
   10 best pHash templates. Without it: the 300 best by 63-bit pHash and HSV histogram.
   On real binder photos the DINOv2 top 5 held the right card ~96 % of the time; the
   pHash top 300 ~64 % (`embed_experiment`).
4. **Match**: ORB + ratio test on the shortlist (threaded), RANSAC homography on the top 20.
   The scores that matter are the *art inliers*, meaning inliers inside the artwork
   window. Inliers on the frame and text box are shared by every card of an era.
   A homography that isn't close to a plain scaling (or 180° turn) is treated as a fluke.
5. **Refine**: map the matched template's corners back into the photo and match
   again. This repairs partial or sloppy outlines and gives an accurate crop.
6. **Decide**:
   - *auto*: art inliers ≥ 10 and ≥ 2× any other candidate.
   - *number*: OCR of the collector number picks exactly one of the candidates.
   - otherwise *ask*: PokéCollect shows the candidates and the user picks.

## Data (`/data`)

| Path | What |
|---|---|
| `card_index1.pkl` | PokemonScanner v2 index (input, used once) |
| `segments/base.npz/.json` | converted index (created on first start) |
| `segments/ext_*.npz/.json` | cards added from catalog images |
| `segments/failed.json` | image downloads that failed (retried after 7 days) |
| `dinov2_vits14_cls.onnx` | DINOv2 shortlist model (optional; `export_matcher.py`) |
| `dinov2_catalog.npz` | catalog embeddings computed on a PC (optional bootstrap, read once) |
| `emb/catalog_emb.npz` | the matcher's own embeddings, extended in the background |

Everything under `segments/` and `emb/` can be deleted and rebuilt.

### DINOv2 shortlist

Optional. Without `dinov2_vits14_cls.onnx` the matcher works exactly as before.

1. On a PC with PyTorch (and a GPU, ideally) run `embed_experiment/export_matcher.py`.
   It exports the model to ONNX (fixed 224×308 input, L2-normalised CLS token,
   checked against PyTorch with onnxruntime) and embeds every catalog image.
2. Copy `dinov2_vits14_cls.onnx` and `dinov2_catalog.npz` into `/data` and restart.
3. `/health` → `embedding` shows `active`, the number of embedded cards and the
   coverage of the index. Cards without an embedding (new catalog cards, failed
   bootstrap) are embedded in the background on the CPU. Until coverage reaches
   98 % the full pHash preselection stays on as well, so nothing gets unreachable.

CPU cost: two inferences (0° and 180°) per card outline, roughly 30–80 ms each on a
desktop-class CPU; the ORB stage gets much cheaper in return (≈40 candidates instead of 300).

## Environment

| Var | Default | |
|---|---|---|
| `DATABASE_URL` | – | PokéCollect's Postgres (read-only use) |
| `DATA_DIR` | `/data` | |
| `INDEX_PKL` | `/data/card_index1.pkl` | |
| `EXTEND` | `1` | add catalog cards that no template covers |
| `EXTEND_REGIONS` | `west` | e.g. `west,ja` to also index Japanese cards |
| `REFRESH_HOURS` | `24` | how often to look for new catalog cards |
| `EMBED_TOP` | `30` | cards taken from the DINOv2 shortlist |
| `PHASH_TOP` | `10` | pHash templates added to it |
| `EMBED_MIN_COVERAGE` | `0.98` | below this, the full pHash preselection stays on too |
| `EMBED_THREADS` | `0` | onnxruntime threads (0 = its default) |
| `BACKGROUND_DELAY_MIN` | `10` | wait before the extension pass + embedding backfill after a start (they then run at low CPU priority, so first scans after a reboot don't compete) |
| `WARMUP` | `1` | run every stage once on a synthetic photo before reporting ready |

## Endpoints

- `GET /health`: loading state (`status`, `phase` while starting, `ready_after_s`,
  `warmup_ms`), template counts, pokemontcg→TCGdex mapping report (incl. unmapped
  sets), extension progress, embedding shortlist state.
- `POST /match` (multipart): `file`, `mode` = `single|multi|binder`, `rows`, `cols`, `top_k`.
  The response's `shortlist` says which shortlist was used (`dinov2` or `phash`);
  `timing` breaks `ms` down (`detect`, `embed`, `embed_wait`, `match`, `ocr`, `queued`).
  Each match is also logged with that breakdown.

Thresholds live at the top of `app/matcher.py` (`MIN_ART`, `LEAD`, …).
