# PokéCollect matcher

Identifies cards by their **artwork** (no cloud, no API key). Used by
PokéCollect's scan reader **"Image match (local)"**.

Pipeline per photo:

1. **Find the card outlines**: several edge strategies (gradient contours,
   blurred Canny, grouped convex hulls, straight-line quad fitting).
2. **Straighten** each candidate outline to 630×880.
3. **Prefilter** ~25k templates to 300 by 63-bit pHash (0° and 180°) and HSV histogram.
4. **Match**: ORB + ratio test on the 300 (threaded), RANSAC homography on the top 20.
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

Everything under `segments/` can be deleted and rebuilt.

## Environment

| Var | Default | |
|---|---|---|
| `DATABASE_URL` | – | PokéCollect's Postgres (read-only use) |
| `DATA_DIR` | `/data` | |
| `INDEX_PKL` | `/data/card_index1.pkl` | |
| `EXTEND` | `1` | add catalog cards that no template covers |
| `EXTEND_REGIONS` | `west` | e.g. `west,ja` to also index Japanese cards |
| `REFRESH_HOURS` | `24` | how often to look for new catalog cards |

## Endpoints

- `GET /health`: loading state, template counts, pokemontcg→TCGdex mapping
  report (incl. unmapped sets), extension progress.
- `POST /match` (multipart): `file`, `mode` = `single|multi|binder`, `rows`, `cols`, `top_k`.

Thresholds live at the top of `app/matcher.py` (`MIN_ART`, `LEAD`, …).
