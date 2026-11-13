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
   pHash top 300 ~64 % (`embed_experiment`). With 50+ learned scan photos the
   shortlist is domain-corrected: the photo is centred on the mean scan photo, the
   catalog on its own mean, and each catalog image loses half its similarity to scan
   photos of other cards (catalog scans with foil sheen otherwise attract every shiny
   photo). On 914 scan crops top-1 went from 72 % to 92 % and the true card reached
   the ORB check for 99.7 % instead of 98.1 %; `/health` → `embedding.domain`.
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
| `learned/` | features + embeddings of users' confirmed scan photos (rebuilt from the app on demand) |

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
| `EMBED_DOMAIN` | `1` | domain correction of the DINOv2 shortlist (`0` = plain cosine) |
| `EMBED_DOMAIN_MIN_PHOTOS` | `50` | learned scan photos needed before the correction switches on |
| `EMBED_HUB_K` | `10` | nearest scan photos (of other cards) that measure a catalog image's "photo hubness" |
| `EMBED_THREADS` | `0` | onnxruntime threads (0 = its default) |
| `BACKGROUND_DELAY_MIN` | `10` | wait before the extension pass + embedding backfill after a start (they then run at low CPU priority, so first scans after a reboot don't compete) |
| `LEARN` | `1` | use confirmed scan photos as extra references (see below) |
| `LEARN_MINUTES` | `15` | how often to look for new scan photos (the api also pokes `/learn` after each one) |
| `API_URL` | `http://api:8000` | where the scan photos are fetched from (`/images/…`) |
| `LEARNED_TOP` / `LEARNED_MIN_SIM` | `5` / `0.5` | cards added to the shortlist from learned photos |
| `WARMUP` | `1` | run every stage once on a synthetic photo before reporting ready |

## Endpoints

- `GET /health`: loading state (`status`, `phase` while starting, `ready_after_s`,
  `warmup_ms`), template counts, pokemontcg→TCGdex mapping report (incl. unmapped
  sets), extension progress, embedding shortlist state.
- `POST /learn`: index new scan photos soon (the api calls it after each scan photo upload).
- `POST /match` (multipart): `file`, `mode` = `single|multi|binder|card`, `rows`, `cols`, `top_k`
  (`card`: the image is already a straightened card, e.g. after the user moved its corners).
  The response's `shortlist` says which shortlist was used (`dinov2` or `phash`);
  `timing` breaks `ms` down (`detect`, `embed`, `embed_wait`, `match`, `ocr`, `queued`).
  Each match is also logged with that breakdown.
- `POST /outline` (multipart): `file` (any picture), optionally `quad` (rough corners,
  fractions) and `refs` (comma-separated catalog ids, e.g. other prints of the same name).
  With refs the card is found by its artwork (ORB on the whole picture or around the rough
  corners against those templates) and then settled like `/verify`. Otherwise, or when no
  reference shares the artwork, it looks for *a* Pokémon card rather than this one (see
  "Card frame" below): the card-shaped outlines the detector finds near the rough corners
  are scored against the typical card frame, and the one that looks like a card wins (not
  its sleeve, binder pocket or art box); an exactly card-shaped outline counts too
  (screenshots, store images, full-art cards). When none does, the frame itself is fitted
  from the rough corners and its sides snapped onto their edges. Last resort: the outline
  nearest the rough corners (without corners: the most card-like outline; a picture that is
  just the card comes back whole). Returns `quad` (card's TL first) or null, `method`
  (`reference` | `frame` | `edges`), `frame_ok` (the frame confirmed the edges) and
  `reference`. Used by the app's picture finder to crop pictures pasted from an image search.
- `POST /stamp` (multipart): `file` (a straightened card photo), optionally `quad` (rough
  corners, card fractions). Finds the Play! Pokémon stamp of Prize Pack cards with the
  learned template (see "Play! stamp" below): without `quad` at both places it sits (bottom
  corners of the artwork), with `quad` around there. Returns `quad`, `score`, `ok` (score
  ≥ 0.75) and `template` (false: nothing learned yet).
- `POST /stamp/learn` (multipart): `photos`, `refs` (files) and `samples` (JSON list of
  `{"photo": i, "box": [x0, y0, x1, y1] | null, "ref": j | null}`) — learns the template;
  `GET /stamp/template` says whether one exists and what it was learned from.
- `POST /verify` (multipart): `file` (a saved, straightened scan photo), `card_id`, optionally
  `original` + `quad` (the corners it was cut along). Compares the photo with that card's
  catalog images only (learned photos are left out, they would match themselves) and returns
  `inliers`, `art_inliers`, `corner_err` (share of the card diagonal), `geo_ok`, the card's
  `outline` inside the photo and — with the original — `suggested_quad`: better corners in
  the original. A crop that cuts off part of the card gives a rough first outline, so the
  matcher crops again along its suggestion and looks again, up to 3 times, until the corners
  settle (`refine_passes` says how many crops it checked). Used by the app's admin tool
  "Weak scans".

### Card frame (`app/frame.py`)

What every card shares — border, name bar, HP, attack box, weakness line, bottom
text — as one mean gradient map (`app/frame_template.png`): the desaturated
gradients of many catalog images averaged, so the artworks wash out. Fitting
it ignores the artwork window and everything outside the card, otherwise the
fit is pulled onto the art box, a sleeve or the binder pocket. Fitting =
a coarse search (scale × rotation × shift by normalised correlation around the
start corners), then an ECC homography on two pyramid levels; the correlation
is the score (≥ 0.3: a card frame). `frame.score(picture, corners)` alone tells
how much an outline looks like a card.

On 120 binder-page phone photos (cards in sleeves, rough corners ~3% off) the
outline came back with a median corner error of 1.5% instead of 3.2%, 76%
within 3% instead of 49%; on clean catalog/store pages it is as exact as the
plain edges and picks the right outline more often.

Rebuild the template from more cards (other eras, other languages):

    python tools/frame_template.py --dataset scans.zip      # references/ of a scan-dataset export
    python tools/frame_template.py --images /path/to/cards   # upright, tightly cropped card images

### Play! stamp (`app/stamp.py`)

The stamp template is learned from the collection, not shipped: the app's admin
section "Play! stamps" → "Learn template" sends the hand-marked stamps and, for
Prize Pack photos nobody marked yet, the unstamped catalog image (the stamp is
where the photo differs most from it, at the bottom corners of the artwork).
A few self-training rounds re-find every sample with the current mean and drop
the ones that don't fit; the box is then moved onto the stamp's outline. The
template lives in the data volume (`stamp_template.png` + `.json`); learn again
after marking more. Matching is normalised correlation on desaturated,
contrast-evened greys at a few scales. On 27 Prize Pack photos (each found with
a template learned from the other 26) stamps scored 0.87–0.96 apart from one
heavy glare (0.52); 150 cards without a stamp stayed at or below 0.60.

While scanning, every recognised card is checked for the stamp (`/match` →
`stamp: {score, quad, ok, sure}`): `STAMP_SURE_SCORE` (default 0.75) ticks
Prize Pack in the review, `STAMP_MIN_SCORE` (default 0.65) shows a "stamp?"
hint. On 57 marked stamps / 899 cards without one, found stamps scored ≥ 0.80
and cards without one ≤ 0.63. Basic energy cards carry the stamp outside the
two usual places: the template remembers where at least two of its stamps sat
(`zones` in the .json) and searches there too — learn it again after updating. `tools/stamp_eval.py <scan dataset zip>` measures both on your own cards
(each marked stamp held out once, every card without Prize Pack as a false
alarm check) and suggests values for the two variables.

### Foil (`tools/holo_diff.py`, `tools/foil_eval.py`)

`app/foil.py` aligns a scanned card to its plain catalog image and keeps the
difference — mostly foil: a reverse holo lights up the frame, a holo the art.
Newer sets (Mega Evolution era, e.g. Chaos Rising) also print the set code box
inverted on foil cards; `code_corr` compares that corner with the catalog image
(≈ +1 same print, negative = inverted), independent of the light.
`holo_diff.py` runs it over the scan dataset; `foil_eval.py <holo_diff output>`
tests how well those numbers tell Normal / Holo / Reverse Holo apart on cards
they were not fitted on (folds by card) and how many "reverse holo?" hints would
be right, and lists the labels to check — cards whose foiling label disagrees
with the photo (set code inverted on a "Normal", plain on a "Reverse Holo"), in
`labels_to_check.csv` and, with `--sheet`, as pictures; `--save foil_model.json` writes the model (asking only where 80 % of
the asks were right, `--precision`). Uploaded in the app (Admin → Reverse holo
detection, → `PUT /foil/model`), the scan review checks each plain card whose
print exists as reverse holo (`POST /foil`: crop + catalog image → score, hint)
and offers "Reverse holo? — tap to set". On 382 Normal / 85 Reverse Holo scans:
85 % balanced accuracy on unseen cards; at 4 in 5 asks right it asks about half
of the reverse holos.

### Learning from scans

Every card saved from a scan with "use photo" keeps the straightened photo, the
original and the confirmed TCGdex id. `app/learn.py` indexes those photos next
to the catalog images (ORB template in the `learned` segment + DINOv2
embedding), so a card you scan again in the same sleeve and lighting is matched
against your own earlier photo. Nothing is trained. Deleting a card or changing
its id drops its photo on the next sync.

Reprint guard: the user's photo of e.g. Base Set Charizard matches the Base
Set 2 reprint just as well. When the winning evidence comes from a learned
photo and another candidate matches the same art in the catalog, the art alone
doesn't decide; the printed number (OCR) or the user does.

For offline work (fine-tuning, detector training, evaluation) the app exports
all scans: Settings → *Scan dataset (ZIP)*.

Thresholds live at the top of `app/matcher.py` (`MIN_ART`, `LEAD`, …).

### Foil patterns (reverse holo, Poké Ball / Master Ball, …)

The catalog image of a card is the print without its foil. `tools/holo_diff.py`
aligns each scanned crop from the scan dataset to that reference (ORB
homography, then ECC for sub-pixel fit), evens out the lighting and subtracts:
what is left is mostly foil. A reverse holo lights up in the frame around the
art box, a pattern shows its repeated motif, a holo only the art. Differences on
printed edges (text, outlines) are damped, so small alignment errors don't
drown the foil.

    pip install opencv-python-headless numpy      # or run it in the matcher image
    python tools/holo_diff.py pokecollect-scans-<date>.zip --out holo_diff --pairs

It writes one sheet per photo (aligned photo · reference · difference) into
`holo_diff/<kind>/<foiling>__<pattern>/` and `holo_diff/index.csv` with the
labels, the alignment inliers and the mean difference in the art box and the
frame. `--pairs` also compares photos of the same card with different foiling
(your reverse holo against your plain copy). `--download` fetches references
that aren't cached on the server. The frame/art ratio alone already tells
reverse holo from plain and holo apart; the sheets are training material for a
real pattern classifier. Label the foiling and pattern on each card (card page
→ Foiling / Pattern) before exporting.

`tools/holo_cluster.py` does the same for every scanned card and sorts the
differences into buckets of similar foil (where the foil is, its texture
spectrum and line directions; k-means, the number of buckets picked by
silhouette unless `--k` is given). It can fetch the dataset from the app itself:

    docker exec -it <matcher> python /app/tools/holo_cluster.py \
        --server http://api:8000 --user <admin> --out /tmp/holo_clusters --download

By default it only compares cards that also exist as a plain print (commons,
uncommons, rares, trainers): ex, full-art and other all-foil prints have no plain
catalog image to subtract (`--all` includes them anyway).
It writes a contact sheet per bucket (`cluster_NN.jpg`, photo + difference,
most typical cards first), `clusters.csv` and `summary.txt` with the labels
each bucket contains. Differences are cached; `--recluster --k N` re-sorts
without aligning everything again. Use the buckets to name the pattern types
you own and set Foiling / Pattern on those cards.
