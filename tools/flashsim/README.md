# flashsim — the flash test, simulated

A renderer for Brian's six test cards (Ethan's Adventure: Normal, Holo, Reverse Holo, each bare and
sleeved), fitted to his manual-flash video v66. It drives the app's own flash code
(`web/src/lib/flashCapture.ts` → `FlashLoop`, `flashCheck.ts`) on simulated frames. The expected-reading
tables in `web/src/lib/flashTable.ts` come from it.

## What is modelled

- **Cards**: paper + varnish (GGX), sleeve, Holo foil (flakes), Reverse Holo foil (pokeball emboss). The
  light is the flash at the camera (with an offset) plus room light.
- **Camera** (`session.py`, Fairphone 6 in the /e/OS browser, from the debug logs):
  - the light switches 0.4–0.6 s after the request, mostly on a frame boundary;
  - auto exposure is one frame late, then ~80 % of the way per frame. Overshoot is 120→225 and 150→55,
    settling at about 150 lit / 119 dark;
  - rolling shutter, 3 % dropped frames, noise, and tracker error in the outline and the patch.

## Data (not in the repo)

`FLASHSIM_DATA` (default `data/`) holds:

- `catalog.png`, the card scan (600×825);
- `v66/NNNN.jpg`, the frames of the v66 video;
- `v66_H.pkl`, per-frame card homographies (catalog → frame).

Results go to `FLASHSIM_WORK` (default `work/`).

## Steps

```sh
python3 real.py                  # real readings from v66 → work/real_pairs.json
python3 fit.py                   # fit the renderer to them → work/fit.json (3 stages, ~5 min)
python3 gen_table.py 0 60        # simulated readings over camera positions (two seeds)
python3 gen_table.py 1 30
python3 make_table.py            # → web/src/lib/flashTable.ts
npm i --prefix work esbuild
./build.sh work/harness_new.cjs  # the app's flash code for node
python3 sweep.py new 0 1         # whole scans: 3 rooms × 2 heights × 2 hand paths × 2 seeds
```

## Findings (16.11.)

- **The fit holds up reading by reading.** On the 109 real readings, simulated and real agree at the
  same camera position. Medians: Holo +0.43 / +0.50, Holo sleeve +0.18 / +0.16, RH −0.08 / −0.08,
  RH sleeve −0.34 / −0.30, Normal ±0.00 / +0.07.
- **Where the camera is matters more than the angle.**
  - Holo sleeve reads +0.7 with the camera over the card's middle, but ±0.0 15° below it.
  - Reverse Holo reads −0.5 straight above, but ±0.0 with the camera above the card.
  - A Normal card reads +0.6 when the flash's glare spot lies on the art.
  - The real v66 readings show the same pattern.
  - So a fixed angle band with fixed thresholds can't separate the kinds. The verdict now compares every
    reading with what each kind would read from that camera position.
- **Dark rooms make it stronger.** There the flash dominates and the glare gradient across the card
  counts as lift.

## Results

| | Right | Wrong | Still open |
|---|---|---|---|
| Whole scans (144 cards), old rule | 27 | 19 | 98 |
| Whole scans (144 cards), new rule | 97 | 4 | 43 |
| Real v66 (81 sequences, 12 readings each), old rule | 28 | 0 | 53 |
| Real v66 (81 sequences, 12 readings each), new rule | 61 | 1 | 19 |

- Whole scans ran up to 30 s each, with the app's code and the Fairphone camera model.
- The real v66 rows start a sequence at every reading, up to 12 readings each.

## Test mat (17.11.)

`python3 make_mat.py` → `mat.pdf` (US Letter landscape, print at 100 %) and `mat.json` (marker corners and
card centres in mm). The 3×2 card slots sit between 20 ArUco markers (DICT_4X4_50). From the markers in a
video, the camera pose is known in every frame. That gives true camera positions for each reading, so the
renderer can be refitted to footage from any room, and the app's own position estimate can be checked.
