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
| `student.onnx` | optional photo model (`tools/distill_student.py`); swapped in while running |
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

Live scan: a card a frame found is remembered on the page map (its outline and
best candidates); the next frames of the same page check it again on its own
outline against just those candidates — no image model, no outline search. Only
squares where that fails (a card swapped, an empty pocket) go the full way.
Frames placed closer on the page are matched without reading numbers too (that
took seconds per card); the app sends a number-corner close-up (`mode=closeup`)
for that. A placement through which the cards would come out more than 1.35×
bigger or smaller than the ones already on the map is a wrong fit (repeating
sleeves, two copies of a card): the frame's cards go back without places
(`map.misplaced`) and aren't remembered — the app places them with its motion
tracking, which it also uses to check every placement before drawing with it.

Close-ups (`mode=closeup`): the number is read where it's printed, also one
without a total. Basic energies ("SVE 016"): the small bold italic digits read far
better from a slightly taller strip with adaptive thresholds and sparse-text OCR; a
bare number only counts as printed ("016") read twice, or right after a candidate's
full set code ("SVE 16"). When one number fits two sets ("SVE 016" / "MEE 016"),
that code decides, or the set code box read on its own (inverted and as is — light
letters in a dark frame), twice and without the other code. A wrong print is worse
than none: anything less leaves the card yellow for the next close-up.
The outline is first snapped to the card (ORB against the candidates). Plain
energy art often has too little for that, and the app's corners then come from
its motion tracking — 5–10 % of the card off after moving in close (a MEE 006
close-up: "006" sat just below the outline). If the number isn't where the
corners say, the strip is read once more a bit lower and once a bit higher, past
the card's edges (`detect.warp_wide`, bottom left, two preparations).

### Student model (`tools/distill_student.py`)

A small network (MobileNetV3) taught by DINOv2 to turn a card photo into DINOv2's
vector of that card's catalog image — roughly 10× less CPU per photo, and the
catalog index stays as it is. Trained on a PC with a GPU from data on the NAS:

| File | From |
|---|---|
| `catalog_emb.npz` | `/data/emb/catalog_emb.npz` (the teacher's catalog vectors) |
| `dinov2_vits14_cls.onnx` | `/data` (the teacher, for the check) |
| `catalog.csv` | `card_id,image_url` from the `tcgdex_catalog` table |
| scans ZIP | Settings → *Scan dataset (ZIP)* |

```
pip install numpy opencv-python-headless onnxruntime httpx onnx   # + PyTorch/torchvision with CUDA (pytorch.org)
python matcher/tools/distill_student.py fetch --catalog catalog.csv --targets catalog_emb.npz --images images
python matcher/tools/distill_student.py train --images images --targets catalog_emb.npz --scans scans.zip --out run
python matcher/tools/distill_student.py check --run run --teacher dinov2_vits14_cls.onnx --targets catalog_emb.npz --scans scans.zip
```

`train` keeps one scan photo in five and one card in ten out of training, always
the same ones; `check` measures teacher and student on them (right card 1st, in
the top 5, in the top 30 = the shortlist, with and without the domain correction;
separately for cards the student never saw) and the CPU time per photo, and says
PASS when the student keeps the shortlist. `train --resume run/student.pt
--epochs 3` continues a run with newer scans. `--max-cards 3000` makes a quick
first try.

**Using it:** copy `run/student.onnx` into `/data` (next to `dinov2_vits14_cls.onnx`).
The matcher looks for it every minute and swaps it in without a restart (a copy
still in progress is waited for); from then on scan photos — queries and the learned
photos — go through the student, catalog images through DINOv2 as before. The
learned photos are embedded again in the background for the new model (~1–2 minutes
for a thousand; until then they don't add to the shortlist). `/health` →
`embedding.photos` shows the model in use (`teacher` or `student-<hash>`), its input
size and CPU time per photo, or why a file was refused (wrong output size, unreadable).
Delete the file to go back to DINOv2. The domain correction stays off for the student
(`EMBED_QUERY_DOMAIN=1` turns it on): it learned the photo look itself, and on
`check` the correction made it slightly worse. Synthetic 3×3 live frame on 2 cores:
image model 3.4 s → 0.37 s, whole frame 5.5 s → 2.5 s, same 9 cards found.

### Nightly training on the NAS (`tools/nightly_student.py`, image `pokecollect-trainer`)

The optional `trainer` service (see `deploy/docker-compose.ghcr.yml`) keeps the student
up to date with new scan photos, on the CPU, once a night (`TRAIN_AT`, local time via `TZ`):

1. Only when at least `TRAIN_MIN_NEW` new scan photos came in since its last training.
2. Scan photos come straight from the database and the api (`/images/…`, ETags, so
   unchanged photos aren't downloaded again); the held-out split is the dataset
   export's, so the PC's `check` and this one measure on the same photos. Catalog
   images are downloaded once (all of them on the first night; copying the PC's
   `images/` folder into the trainer folder saves that).
3. It continues the student from its last checkpoint: one epoch over all training
   scans plus `TRAIN_CARDS` catalog cards — a different slice each night — at a small
   learning rate.
4. It measures the new student, the one in use (`/matcher/student.onnx`) and DINOv2 on
   the held-out photos. The new one replaces the one in use only if it's not worse
   (`TRAIN_MAX_DROP_TOP30` / `_TOP1` / `_NEW_TOP30` percentage points; with no student in
   use, `check`'s PASS rule against DINOv2). The matcher picks the file up within a minute.
5. Each night leaves `runs/<date>/` (log lines are in `trainer.log`, numbers in
   `check.json`) and a line in `state.json` → `history`; the last `TRAIN_KEEP_RUNS` runs stay.

To start it, put `run/student.pt` from the PC into the trainer folder. `touch train-now`
in that folder runs a night right away (also without new photos). Expect roughly
5–10 minutes a night with 4 threads on a desktop-class CPU (measured: ~13 training
pictures/s on 2 slow server cores), plus the catalog download on the first night.

| Var | Default | |
|---|---|---|
| `TRAIN_AT` | `03:30` | local time (set `TZ`) |
| `TRAIN_MIN_NEW` | `10` | new training scan photos needed for a run |
| `TRAIN_CARDS` / `TRAIN_EPOCHS` | `4000` / `1` | catalog cards per night, epochs |
| `TRAIN_LR` / `TRAIN_BATCH` | `2e-4` / `32` | |
| `TRAIN_THREADS` / `TRAIN_WORKERS` | `4` / `2` | PyTorch threads, data loader processes (needs `shm_size`) |
| `TRAIN_SCAN_SHARE` | `0.5` | real photos' share of a night's samples |
| `TRAIN_CHECK_DOMAIN` | `800` | photos for DINOv2's domain correction in the check |
| `TRAIN_NICE` | `10` | CPU priority below the matcher |
| `TRAINER_DIR` / `MATCHER_DIR` | `/data` / `/matcher` | |

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
| `EMBED_QUERY_MODEL` | `/data/student.onnx` | photo model (student); missing = photos through DINOv2 |
| `EMBED_QUERY_DOMAIN` | `0` | domain correction for the student too |
| `EMBED_QUERY_RELOAD_S` | `60` | how often to look for a new or removed student file (`0` = only at start) |
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
  `page` (binder and multi, optional): the grid's four corners as fractions, JSON
  `[[x, y], …]` from the top left, clockwise — the app's tilted grid when the cards were
  shot at an angle against glare (`rows`/`cols` give the grid's shape). The photo is
  warped upright first (`"page": "straightened"` in the response), corners are mapped
  back into the photo. Binder: the page (grid shape `cols × 63` by `rows × 88`) is
  searched for cards anywhere, each put in the pocket under its centre; pockets still
  empty are searched one by one with 12 % slack, corners snapped to the matched card.
  Multi: the whole photo is warped (up to 60 % of the grid's size past it) and loose
  cards are found as usual. The grid only removes the tilt — the cards needn't sit in
  its cells, and the phone stepping back (the app shrinks the grid to keep it on
  screen) is fine. Synthetic 3×3 layouts, app's focal-length guess 15 % off, the
  photographer stepping back to keep the grid in view:

  | tilt | binder: plain split → straightened | loose cards: plain → straightened |
  |---|---|---|
  | 15° | 9 → 9 of 9 (corners 5 % → 1 % of a card off) | 9 → 9 of 9 |
  | 25° | 4 → 8 of 9 | 5 → 9 of 9 |
  | 30° sideways | 4 → 9 of 9 | 5 → 9 of 9 |
  | 35°, guess 25 % off, off-centre | 3 → 7 of 9 | 4 → 6 of 9 |

  The response's `shortlist` says which shortlist was used (`student`, `dinov2` or `phash`);
  `timing` breaks `ms` down (`detect`, `embed`, `embed_wait`, `match`, `ocr`, `page`, `queued`).
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
Scarlet & Violet and Mega Evolution sets also print the set code box
("SCR EN") inverted on reverse holos; holos keep it like the catalog image
(860 scans: reverse holos −0.69…−0.94 in 11 of 12 sets, Normal and Holo ≈ +0.9;
me05 is the exception so far). `box_corr` finds that box in the catalog image (a
box-sized patch on the set code row, mostly one tone with letters in it) and
compares just its inside with the photo: ≈ +1 same print, ≈ −1 inverted, empty
when the photo is too blurred or washed out there — independent of the light.
`code_corr`, the lowest correlation of any small window in that corner, also
reacts to foil texture there but just as much to sharpening halos and smeared
edges, so it is only a model feature, never the proof for a label.
`holo_diff.py` runs it over the scan dataset; `foil_eval.py <holo_diff output>`
tests how well those numbers tell Normal / Holo / Reverse Holo apart on cards
they were not fitted on (folds by card) and how many "reverse holo?" hints would
be right, and lists the labels to check — cards whose foiling label disagrees
with the photo (set code box inverted on a "Normal" or "Holo", plain on a
"Reverse Holo" — only in sets whose own labels show the pattern; others follow
the majority), in `labels_to_check.csv` and, with
`--sheet`, as pictures with the compared box marked; `--save foil_model.json` writes the model (asking only where 80 % of
the asks were right, `--precision`). Uploaded in the app (Admin → Reverse holo
detection, → `PUT /foil/model`), the scan review checks each plain card whose
print exists as reverse holo (`POST /foil`: crop + catalog image → score, hint)
and offers "Reverse holo? — tap to set". On 382 Normal / 85 Reverse Holo scans
with the set code box: 98 % balanced accuracy on unseen cards; at 9 in 10 asks
right it asks about 98 % of the reverse holos (the box alone: AUC 0.99).

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
