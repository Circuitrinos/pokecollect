"""
Hüllen-Aufkleber (Brian 23.11.: OfficeSmartLabels LWS144-1-S): blanko Bögen
drucken, Aufkleber scannen, mit einer Karte verknüpfen.
"""
import pytest
import segno
from sqlalchemy import delete

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.sleeve_tag import SleeveTag, TagSheet
from app.services import sleeve_tags as st
from app.services import tag_pdf
from test_multiuser import _client_for, _drop_user, _make_user

API = "/api/v1"
BASE = "https://tcg.circuitrinos.com"


@pytest.fixture()
def clean(client):
    def wipe():
        db = SessionLocal()
        db.execute(delete(SleeveTag))
        db.execute(delete(TagSheet))
        db.query(PokemonCard).filter(PokemonCard.kartenname.like("TestTag%")).delete(synchronize_session=False)
        db.commit()
        db.close()
    wipe()
    client.put(f"{API}/tags/settings", json={"base_url": BASE, "offset_x_mm": 0, "offset_y_mm": 0})
    yield
    wipe()


def _card(client, name="TestTag Pikachu", owned=True) -> int:
    r = client.post(f"{API}/cards", json={"kartenname": name, "besessen": owned, "wunschliste": not owned})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Codes ────────────────────────────────────────────────────────────────────

def test_codes_stay_four_characters_and_map_back():
    """Brian 23.11.: „with thousands of cards, the number will eventually get too long"."""
    for serial in (1, 2, 144, 4217, 99_999, st.MAX_SERIAL):
        code = st.code_for(serial)
        assert len(code) == 4 and set(code) <= set(st.ALPHABET) and st.parse(code) == serial
    assert not set("ILOU") & set(st.ALPHABET)                  # nichts zum Verwechseln
    assert all(st._unmix(st._mix(i)) == i for i in range(0, st.SPACE, 997))


def test_neighbours_look_different_and_typos_rarely_hit_a_sheet():
    codes = [st.code_for(i) for i in range(1, 1001)]
    assert len(set(codes)) == 1000
    diffs = [sum(a != b for a, b in zip(x, y)) for x, y in zip(codes, codes[1:])]
    assert sum(diffs) / len(diffs) > 3.5                        # Nachbarn: fast alle Stellen anders
    sheet = set(range(1, 145))
    hits = total = 0
    for serial in sheet:                                        # jede vertippte Stelle eines Bogens
        code = st.code_for(serial)
        for i in range(4):
            for ch in st.ALPHABET:
                if ch != code[i]:
                    total += 1
                    hits += st.parse(code[:i] + ch + code[i + 1:]) in sheet
    assert hits / total < 0.01                                  # landet fast nie auf einem anderen


def test_parse_accepts_what_people_type_or_scan():
    code = st.code_for(123)
    assert st.parse(code) == st.parse(code.lower()) == st.parse(f"{code[:2]}-{code[2:]}") == 123
    assert st.parse(f" #{code[:2]} {code[2:]} ") == 123
    assert st.parse(f"HTTPS://TCG.CIRCUITRINOS.COM/T/{code}") == 123
    assert st.parse(f"https://tcg.circuitrinos.com/t/{code.lower()}/") == 123
    tricky = next(st.code_for(i) for i in range(1, 5000) if "0" in st.code_for(i) and "1" in st.code_for(i))
    assert st.parse(tricky.replace("0", "O").replace("1", "l")) == st.parse(tricky)   # O→0, l→1
    for bad in ("", "abc", "ABCDE", "#", "AB-U1", "12.5", st.code_for(0)):
        with pytest.raises(st.CodeError):
            st.parse(bad)


def test_link_is_upper_case_so_the_qr_stays_small():
    content = st.qr_content(BASE, 123)
    assert content == f"HTTPS://TCG.CIRCUITRINOS.COM/T/{st.code_for(123)}"
    assert tag_pdf.qr_version(content) == 2                     # 25 × 25 Module
    assert segno.make(content, error="m").mode == "alphanumeric"
    # Web-App unter einem Pfad: der Pfad bleibt, wie er ist
    assert st.qr_content("https://example.com/Poke", 5) == f"https://example.com/Poke/T/{st.code_for(5)}"


def test_qr_modules_are_whole_printer_dots_with_a_quiet_zone():
    n = len(tag_pdf.qr_matrix(st.qr_content(BASE, st.MAX_SERIAL)))
    mod = tag_pdf.QR_BOX // n
    assert n == 25 and mod == 9                                  # 9/600" = 0,38 mm je Modul
    rand = tag_pdf.QR_INSET + (tag_pdf.QR_BOX - n * mod) // 2
    assert rand / mod >= 4                                        # Ruhezone ≥ 4 Module bei exaktem Druck


def test_grid_matches_the_vendor_template():
    """Lage der Etiketten laut LWS144-1-S.docx (Formen in EMU, 914400 je Zoll)."""
    b = tag_pdf.LWS144
    assert b.per_sheet == 144
    vorlage = {0: (0.4056, 0.5938), 8: (7.3056, 0.5938), 135: (0.4056, 9.8986), 143: (7.3056, 9.8986)}
    for slot, (x_in, y_in) in vorlage.items():
        x, y = b.origin(slot)
        assert abs(x - x_in * 600) <= 4 and abs(y - y_in * 600) <= 4      # ≤ 0,17 mm
    right = tag_pdf.PAGE_W - (b.origin(8)[0] + b.label_w)
    bottom = tag_pdf.PAGE_H - (b.origin(143)[1] + b.label_h)
    assert right > 0.4 * 600 and bottom > 0.55 * 600


# ── Bögen ────────────────────────────────────────────────────────────────────

def test_sheets_number_on_and_a_used_sheet_starts_later(client, clean):
    r = client.post(f"{API}/tags/sheets", json={"count": 2, "start_slot": 10})
    assert r.status_code == 201, r.text
    first, second = sorted(r.json(), key=lambda s: s["nr"])
    assert (first["nr"], first["first"], first["last"], first["count"]) == (1, 1, 134, 134)
    assert (second["nr"], second["first"], second["last"], second["count"]) == (2, 135, 278, 144)
    r = client.post(f"{API}/tags/sheets", json={"count": 1})
    assert r.json()[0]["first"] == 279 and r.json()[0]["nr"] == 3
    assert client.post(f"{API}/tags/sheets", json={"count": 1, "start_slot": 144}).status_code == 422
    assert client.post(f"{API}/tags/sheets", json={"count": 11}).status_code == 422


def test_pdf_has_one_page_per_sheet(client, clean):
    ids = [s["id"] for s in client.post(f"{API}/tags/sheets", json={"count": 2}).json()]
    r = client.get(f"{API}/tags/sheets/pdf", params={"ids": ",".join(map(str, ids)), "lang": "DE"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-1.4") and b"/Count 2" in r.content
    r = client.get(f"{API}/tags/calibration.pdf")
    assert r.status_code == 200 and b"/Count 1" in r.content
    assert client.get(f"{API}/tags/sheets/pdf", params={"ids": "999999"}).status_code == 404


def test_pdf_needs_the_link_address(client, clean):
    sid = client.post(f"{API}/tags/sheets", json={"count": 1}).json()[0]["id"]
    client.put(f"{API}/tags/settings", json={"base_url": ""})
    r = client.get(f"{API}/tags/sheets/pdf", params={"ids": str(sid)})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "base_url"
    assert client.put(f"{API}/tags/settings", json={"base_url": "ftp://x"}).status_code == 422
    r = client.put(f"{API}/tags/settings", json={"base_url": BASE + "/", "offset_x_mm": 0.6, "offset_y_mm": -1.25})
    assert r.json()["base_url"] == BASE and r.json()["offset_y_mm"] == -1.25


# ── Verknüpfen ───────────────────────────────────────────────────────────────

def test_scan_link_and_move(client, clean):
    client.post(f"{API}/tags/sheets", json={"count": 1})
    a, b = _card(client, "TestTag A"), _card(client, "TestTag B")
    code1, code2 = st.code_for(1), st.code_for(2)

    r = client.get(f"{API}/tags/{code1}")
    assert r.status_code == 200 and r.json()["card_id"] is None and r.json()["sheet_nr"] == 1
    cand = [c["id"] for c in client.get(f"{API}/tags/candidates").json()]
    assert cand[:2] == [b, a]                                     # zuletzt erfasst zuerst

    r = client.put(f"{API}/tags/{code1}", json={"card_id": a})
    assert r.status_code == 200 and r.json()["card"]["id"] == a
    assert client.get(f"{API}/tags/by-card/{a}").json()["code"] == code1
    assert a not in [c["id"] for c in client.get(f"{API}/tags/candidates").json()]

    # Karte hat schon einen Aufkleber → Rückfrage statt still umhängen
    r = client.put(f"{API}/tags/{code2}", json={"card_id": a})
    assert r.status_code == 409 and r.json()["detail"] == {"reason": "card_tagged", "other": code1}
    # Aufkleber klebt schon an A → für B nur mit replace
    r = client.put(f"{API}/tags/{code1}", json={"card_id": b})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "tag_linked"

    r = client.put(f"{API}/tags/{code2}", json={"card_id": a, "replace": True})
    assert r.status_code == 200
    assert client.get(f"{API}/tags/{code1}").json()["card_id"] is None
    assert client.get(f"{API}/tags/by-card/{a}").json()["code"] == code2

    assert client.delete(f"{API}/tags/{code2}/card").status_code == 204
    assert client.get(f"{API}/tags/by-card/{a}").json() is None


def test_selling_a_card_frees_its_tag(client, clean):
    """Brian 23.11.: verkauft/getauscht und aus der Hülle → Aufkleber wieder frei."""
    client.post(f"{API}/tags/sheets", json={"count": 1})
    a, b, c = _card(client, "TestTag Weg"), _card(client, "TestTag Bulk"), _card(client, "TestTag Wunsch")
    ca, cb, cc = st.code_for(5), st.code_for(6), st.code_for(7)
    for code, card in ((ca, a), (cb, b), (cc, c)):
        assert client.put(f"{API}/tags/{code}", json={"card_id": card}).status_code == 200
    # gelöscht — einzeln und per Auswahl
    assert client.delete(f"{API}/cards/{a}").status_code == 204
    assert client.post(f"{API}/cards/meta/bulk", json={"action": "delete", "ids": [b]}).status_code == 200
    # behalten, aber nicht mehr besessen (zurück auf die Wunschliste)
    r = client.put(f"{API}/cards/{c}", json={"besessen": False, "wunschliste": True})
    assert r.status_code == 200
    for code in (ca, cb, cc):
        assert client.get(f"{API}/tags/{code}").json()["card_id"] is None
    assert client.get(f"{API}/tags/by-card/{c}").json() is None
    # und lässt sich gleich für die nächste Karte verwenden
    d = _card(client, "TestTag Neu")
    assert client.put(f"{API}/tags/{ca}", json={"card_id": d}).json()["card_id"] == d


def test_editing_an_owned_card_keeps_its_tag(client, clean):
    client.post(f"{API}/tags/sheets", json={"count": 1})
    a = _card(client, "TestTag Bleibt")
    client.put(f"{API}/tags/{st.code_for(3)}", json={"card_id": a})
    assert client.put(f"{API}/cards/{a}", json={"zustand": "Near Mint", "besessen": True}).status_code == 200
    assert client.get(f"{API}/tags/by-card/{a}").json()["code"] == st.code_for(3)


def test_only_owned_cards_and_real_codes(client, clean):
    client.post(f"{API}/tags/sheets", json={"count": 1})
    wish = _card(client, "TestTag Wunsch", owned=False)
    r = client.put(f"{API}/tags/{st.code_for(1)}", json={"card_id": wish})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "not_owned"
    assert wish not in [c["id"] for c in client.get(f"{API}/tags/candidates").json()]
    r = client.get(f"{API}/tags/xyz")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "format"
    r = client.get(f"{API}/tags/{st.code_for(9999)}")           # nie gedruckt
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "unknown"


def test_misprinted_sheet_can_go_until_a_tag_is_used(client, clean):
    s1, s2 = (s["id"] for s in sorted(client.post(f"{API}/tags/sheets", json={"count": 2}).json(),
                                       key=lambda s: s["nr"]))
    client.put(f"{API}/tags/{st.code_for(1)}", json={"card_id": _card(client)})
    assert client.delete(f"{API}/tags/sheets/{s1}").status_code == 409
    assert client.delete(f"{API}/tags/sheets/{s2}").status_code == 204
    assert [s["nr"] for s in client.get(f"{API}/tags/sheets").json()] == [1]


def test_tags_belong_to_their_printer(client, clean):
    client.post(f"{API}/tags/sheets", json={"count": 1})
    uid = _make_user("tagfremd")
    try:
        other = _client_for(uid)
        one = st.code_for(1)
        assert other.get(f"{API}/tags/{one}").status_code == 404
        assert other.get(f"{API}/tags/sheets").json() == []
        theirs = _card(other, "TestTag Fremd")
        assert other.put(f"{API}/tags/{one}", json={"card_id": theirs}).status_code == 404
        # eigener Bogen des anderen: Nummern laufen global weiter, Bogen-Nr. je Benutzer
        r = other.post(f"{API}/tags/sheets", json={"count": 1})
        assert r.json()[0]["first"] == 145 and r.json()[0]["nr"] == 1
        # fremde Karte lässt sich nicht an den eigenen Aufkleber hängen
        assert client.put(f"{API}/tags/{one}", json={"card_id": theirs}).status_code == 404
    finally:
        _drop_user(uid)


def test_every_code_on_a_sheet_is_distinct():
    contents = [st.qr_content(BASE, s) for s in range(1, 1000)]
    assert len(set(contents)) == len(contents)
    assert all(tag_pdf.qr_version(c) == 2 for c in contents[::97])
