"""
Nightly student training on the NAS — the `trainer` service (image pokecollect-trainer).

Once a night (TRAIN_AT, in the container's time zone), when enough new scan
photos came in since the last run, it continues the student from its last
checkpoint on the CPU: a short epoch over all training scans plus a different
slice of the catalog each night (so nothing is forgotten). Then it measures the
new student against the one the matcher uses now and against DINOv2, on the
held-out scan photos (the same split as `distill_student.py check`), and only
when the new one is at least as good it writes the matcher's
/data/student.onnx — the matcher swaps it in by itself.

Folders (compose volumes):
  /data      the trainer's own: catalog images, scan photos, checkpoints,
             runs/<date>/ (log + check.json of each night), state.json
  /matcher   the matcher's /data: reads emb/catalog_emb.npz and
             dinov2_vits14_cls.onnx, writes student.onnx
  /app/logs  trainer.log, trainer-errors.log

To start, copy run/student.pt from the first training on the PC into /data
(the trainer's folder). `touch /data/train-now` runs a night right away.
Scan photos come straight from the database and the api (/images/…), like
the matcher's learner — no ZIP export needed; the held-out split is the
export's (by the original photo's content), so the PC's check and this one
measure on the same photos.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # matcher/ → app.*
sys.path.insert(0, str(Path(__file__).resolve().parent))         # tools/ → distill_student
import distill_student as ds

from app import logsetup

log = logging.getLogger("trainer")

DATABASE_URL = os.getenv("DATABASE_URL", "")
API_URL = os.getenv("API_URL", "http://api:8000").rstrip("/")
HOME = Path(os.getenv("TRAINER_DIR", "/data"))
MATCHER = Path(os.getenv("MATCHER_DIR", "/matcher"))
TRAIN_AT = os.getenv("TRAIN_AT", "03:30")
MIN_NEW = int(os.getenv("TRAIN_MIN_NEW", "10"))           # new training scan photos since the last run
EPOCHS = int(os.getenv("TRAIN_EPOCHS", "1"))
CARDS = int(os.getenv("TRAIN_CARDS", "4000"))             # catalog cards per night (a different slice each night)
LR = float(os.getenv("TRAIN_LR", "2e-4"))
BATCH = int(os.getenv("TRAIN_BATCH", "32"))
THREADS = int(os.getenv("TRAIN_THREADS", "4"))
WORKERS = int(os.getenv("TRAIN_WORKERS", "2"))
SCAN_SHARE = float(os.getenv("TRAIN_SCAN_SHARE", "0.5"))
KEEP_RUNS = int(os.getenv("TRAIN_KEEP_RUNS", "7"))
NICE = int(os.getenv("TRAIN_NICE", "10"))
MAX_DROP_TOP30 = float(os.getenv("TRAIN_MAX_DROP_TOP30", "0.3"))   # vs the student in use, points
MAX_DROP_TOP1 = float(os.getenv("TRAIN_MAX_DROP_TOP1", "1.0"))
MAX_DROP_NEW30 = float(os.getenv("TRAIN_MAX_DROP_NEW_TOP30", "1.0"))  # cards never trained on
CHECK_DOMAIN = int(os.getenv("TRAIN_CHECK_DOMAIN", "800"))    # photos for DINOv2's domain correction
ON_START = os.getenv("TRAIN_ON_START", "0") == "1"

_SQL = """
SELECT c.id, c.tcgdex_card_id, COALESCE(c.bild_scan_pfad, c.bild_karte_pfad), c.bild_original_pfad, s.status
FROM pokemon_cards c LEFT JOIN scan_checks s ON s.card_id = c.id
WHERE c.tcgdex_card_id IS NOT NULL AND c.bild_original_pfad IS NOT NULL
  AND COALESCE(c.bild_scan_pfad, c.bild_karte_pfad) IS NOT NULL
ORDER BY c.id
"""


# ── data ─────────────────────────────────────────────────────────────────────

def _rel(path: str) -> str | None:
    rel = (path or "").lstrip("/")
    return None if not rel or ".." in rel.split("/") else rel


def _db_rows() -> list[tuple]:
    import psycopg2
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(_SQL)
        return cur.fetchall()


def sync_scans(folder: Path) -> int:
    """
    The scan photos as the dataset export has them (labels.jsonl + crops/), kept
    up to date: crops by ETag, originals only hashed once (their content names
    them — "originals/<sha1>…", which decides the held-out split).
    """
    import httpx
    (folder / "crops").mkdir(parents=True, exist_ok=True)
    seen_path = folder / "originals.json"
    seen: dict[str, str] = json.loads(seen_path.read_text("utf-8")) if seen_path.exists() else {}
    etags_path = folder / "crops.json"
    etags: dict[str, list] = json.loads(etags_path.read_text("utf-8")) if etags_path.exists() else {}
    labels, fresh, failed = [], 0, 0
    with httpx.Client(timeout=60) as http:
        for row_id, card_id, crop, orig, status in _db_rows():
            crel, orel = _rel(crop), _rel(orig)
            if not crel or not orel:
                continue
            name = f"crops/{row_id}{Path(crel).suffix.lower()}"
            key = str(row_id)
            try:
                if orel not in seen:
                    r = http.get(f"{API_URL}/{orel}")
                    if r.status_code != 200:
                        failed += 1
                        continue
                    seen[orel] = f"originals/{hashlib.sha1(r.content).hexdigest()[:16]}{Path(orel).suffix.lower()}"
                old = etags.get(key)
                headers = {"If-None-Match": old[1]} if old and old[0] == crel and old[1] and (folder / name).exists() else {}
                r = http.get(f"{API_URL}/{crel}", headers=headers)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    tmp = folder / (name + ".tmp")
                    tmp.write_bytes(r.content)
                    tmp.replace(folder / name)
                    etags[key] = [crel, r.headers.get("etag")]
                    fresh += 1
                elif r.status_code != 304:
                    failed += 1
                    continue
            except httpx.HTTPError as exc:
                log.warning("Scan photo %s: %s", crel, exc)
                failed += 1
                continue
            labels.append(json.dumps({"id": row_id, "card_id": card_id, "crop": name, "original": seen[orel],
                                      "check": {"status": status} if status else None}))
    keep = {json.loads(x)["crop"] for x in labels}
    for p in (folder / "crops").iterdir():                  # cards deleted or changed since
        if f"crops/{p.name}" not in keep:
            p.unlink(missing_ok=True)
    (folder / "labels.jsonl").write_text("\n".join(labels) + ("\n" if labels else ""), "utf-8")
    seen_path.write_text(json.dumps(seen), "utf-8")
    etags_path.write_text(json.dumps(etags), "utf-8")
    log.info("Scan photos: %d (%d downloaded, %d not loadable)", len(labels), fresh, failed)
    return len(labels)


def write_catalog_csv(path: Path) -> None:
    import csv

    from app.catalog import load_catalog
    cat = load_catalog(DATABASE_URL)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["card_id", "image_url"])
        for cid, c in cat.items():
            if c.image_url:
                w.writerow([cid, c.image_url])


# ── one night ────────────────────────────────────────────────────────────────

class _ToLog(io.TextIOBase):
    """The tool's print() lines into the log."""

    def write(self, s: str) -> int:
        for line in s.rstrip().splitlines():
            if line.strip():
                log.info("  %s", line)
        return len(s)


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def _load_state() -> dict:
    p = HOME / "state.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {"trained": [], "history": []}


def _save_state(state: dict) -> None:
    tmp = HOME / "state.tmp.json"
    tmp.write_text(json.dumps(state, indent=1), "utf-8")
    tmp.replace(HOME / "state.json")


def _replace(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _fmt(d: dict | None) -> str:
    return "-" if not d else "/".join("-" if v is None else f"{v:.1f}" for v in d.values())


def decide(report: dict) -> tuple[bool, list[str]]:
    """
    New student good enough? With a student in use: not worse than it (all held-out
    photos, and cards never trained on). Without one: `check`'s PASS rule against DINOv2.
    """
    why = []
    new = report["new_plain"]
    cur = report.get("current_plain")
    if cur is None:
        t = ds.best_of(report, "teacher")
        if new["all"]["top30"] < t["top30"] - 0.5 or new["all"]["top1"] < t["top1"] - 3.0:
            why.append(f"loses to DINOv2 ({_fmt(new['all'])} vs {_fmt(t)})")
        return not why, why
    a, b = new["all"], cur["all"]
    if a["top30"] < b["top30"] - MAX_DROP_TOP30 or a["top1"] < b["top1"] - MAX_DROP_TOP1:
        why.append(f"worse than the student in use ({_fmt(a)} vs {_fmt(b)})")
    na, nb = new["new_cards"]["top30"], cur["new_cards"]["top30"]
    if na is not None and nb is not None and na < nb - MAX_DROP_NEW30:
        why.append(f"worse on cards never trained on (top 30 {na}% vs {nb}%)")
    return not why, why


def run_once(force: bool = False) -> str:
    t0 = time.time()
    HOME.mkdir(parents=True, exist_ok=True)
    cur_pt = HOME / "current" / "student.pt"
    if not cur_pt.exists():
        seed = HOME / "student.pt"
        if not seed.exists():
            log.warning("No student to continue: copy run/student.pt from the PC's training into the "
                        "trainer's folder (%s/student.pt)", HOME)
            return "no-seed"
        cur_pt.parent.mkdir(exist_ok=True)
        shutil.move(seed, cur_pt)
        log.info("Starting from the PC's student.pt")
    teacher = MATCHER / "dinov2_vits14_cls.onnx"
    src_targets = MATCHER / "emb" / "catalog_emb.npz"
    if not teacher.exists() or not src_targets.exists():
        log.warning("Matcher folder %s has no %s or emb/catalog_emb.npz — is it mounted?", MATCHER, teacher.name)
        return "no-matcher"
    targets = HOME / "targets.npz"
    _replace(src_targets, targets)                       # a fixed copy for tonight
    scans_dir = HOME / "scans"
    if not sync_scans(scans_dir):
        return "no-scans"
    ids, T = ds.load_targets(targets)
    scans = ds.Scans(scans_dir)
    test, train = ds.check_split(ids, scans)
    state = _load_state()
    done = set(state.get("trained", []))
    new = [r for r in train if str(r["id"]) not in done]
    if len(new) < MIN_NEW and not force:
        log.info("%d new training scan photo(s) since the last run (waiting for %d) — nothing to do",
                 len(new), MIN_NEW)
        return "waiting"
    log.info("Training on %d scan photos (%d new) and %d catalog cards; %d photos held out",
             len(train), len(new), CARDS, len(test))
    images = HOME / "images"
    write_catalog_csv(HOME / "catalog.csv")
    run = HOME / "runs" / _now().strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(_ToLog()):
        ds.cmd_fetch(_ns(catalog=HOME / "catalog.csv", targets=targets, images=images, max_cards=0))
        ds.cmd_train(_ns(images=images, targets=targets, scans=scans_dir, out=run, arch="mobilenet_v3_large",
                         size="224x308", epochs=EPOCHS, batch=BATCH, lr=LR, tau=0.05, cos_weight=1.0,
                         scan_share=SCAN_SHARE, workers=WORKERS, device="cpu", max_cards=CARDS,
                         subset_seed=int(time.time() // 86400), threads=THREADS, resume=cur_pt,
                         fresh_optimizer=True, no_pretrained=True))
    if not test:
        log.warning("No held-out scan photos to measure on — the new student is not used")
        return "no-check"
    models = {"teacher": teacher, "new": run / "student.onnx"}
    in_use = MATCHER / "student.onnx"
    if in_use.exists():
        models["current"] = in_use
    with contextlib.redirect_stdout(_ToLog()):
        res = ds.measure(models, ids, T, scans, test, train, THREADS, CHECK_DOMAIN, 5,
                         domain_for=lambda name: name == "teacher")
        report = ds.report_of(res, np.array([ds.held_out_card(r["card_id"]) for r in test]))
        ds.print_report(report)
    ok, why = decide(report)
    summary = {"run": run.name, "published": ok, "why": why, "photos": len(train), "new_photos": len(new),
               "held_out": len(test), "minutes": round((time.time() - t0) / 60, 1),
               "new": report["new_plain"]["all"], "current": (report.get("current_plain") or {}).get("all"),
               "teacher": ds.best_of(report, "teacher")}
    (run / "check.json").write_text(json.dumps({"report": report, **summary}, indent=1), "utf-8")
    if ok:
        _replace(run / "student.onnx", MATCHER / "student.onnx")
        _replace(run / "student.pt", cur_pt)
        state["trained"] = sorted({str(r["id"]) for r in train} | done)
        log.info("New student in use: %s (1st/top 5/top 30) vs %s before, DINOv2 %s — %.0f min",
                 _fmt(summary["new"]), _fmt(summary["current"]), _fmt(summary["teacher"]), summary["minutes"])
    else:
        log.warning("New student not used: %s — the matcher keeps the one it has (%.0f min)",
                    "; ".join(why), summary["minutes"])
    state["history"] = (state.get("history", []) + [summary])[-60:]
    _save_state(state)
    for old in sorted((HOME / "runs").iterdir())[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)
    return "published" if ok else "kept"


# ── schedule ─────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now().astimezone()      # TRAIN_AT is the container's local time (TZ)


def next_run(now: datetime) -> datetime:
    hh, mm = (int(v) for v in TRAIN_AT.split(":"))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def _safe(force: bool) -> None:
    try:
        run_once(force)
    except Exception:
        log.exception("Nightly training failed")


def main() -> None:
    logsetup.setup("trainer", access=False)
    with contextlib.suppress(OSError):
        os.nice(NICE)                                    # the matcher's scans win the CPU
    log.info("Trainer: nightly at %s (%s), %d catalog cards + new scans, %d threads; folders %s and %s",
             TRAIN_AT, time.strftime("%Z"), CARDS, THREADS, HOME, MATCHER)
    if ON_START:
        _safe(force=True)
    trigger = HOME / "train-now"
    while True:
        at = next_run(_now())
        log.info("Next run %s", at.strftime("%Y-%m-%d %H:%M"))
        while _now() < at:
            if trigger.exists():
                trigger.unlink(missing_ok=True)
                log.info("train-now: running a night now")
                _safe(force=True)
                at = next_run(_now())
            time.sleep(min(60.0, max(1.0, (at - _now()).total_seconds())))
        _safe(force=False)


if __name__ == "__main__":
    main()
