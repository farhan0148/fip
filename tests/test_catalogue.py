"""Prudential catalogue import, contract ingestion, citation viewer and the catalogue Plans pages.

Uses the real files in data/prudential (the JSON and the two sample contracts). _test database only.
"""

import json
import re
import shutil
from pathlib import Path

import pymupdf
import pytest

from app import catalogue, contracts
from app.extensions import db
from app.models import (
    ComparisonFlag, Document, DocumentVersion, Paragraph, PlanDocument, Policy, Scenario, User,
)

DATA = Path(__file__).resolve().parent.parent / "data" / "prudential"
CATALOGUE = json.loads((DATA / "prudential-catalogue.json").read_text(encoding="utf-8"))
SHIELD_DOC = "prushield-premier-plus-standard-apr-2026"
AB_DOC = "prushield-a-b-apr-2026"
SLUG_BY_NAME = {
    "PRUShield Premier": "prushield-premier",
    "PRUShield Plus": "prushield-plus",
    "PRUShield Standard": "prushield-standard-plan",
}


def run(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


@pytest.fixture()
def imported(app, seeded):
    result = run(app, "import-catalogue")
    assert result.exit_code == 0, result.output
    return app


@pytest.fixture()
def ingested(imported):
    result = run(imported, "ingest-contracts")
    assert result.exit_code == 0, result.output
    return imported


@pytest.fixture()
def user(browser, ingested):
    browser.signup()
    browser.onboard()
    return browser


def plan(slug):
    return Policy.query.filter_by(slug=slug).one()


def paragraph_for(example):
    """The stored paragraph at the example's page whose box matches the example's box."""
    x0, y0, x1, y1 = example["paragraph_bbox_points"]
    for row in Paragraph.query.filter_by(page=example["pdf_page"]):
        if all(abs(a - b) < 0.01 for a, b in zip(row.bbox, (x0, y0, x1, y1))):
            return row
    return None


def scope_slugs(text):
    found = {slug for name, slug in SLUG_BY_NAME.items() if name in text}
    if "PRUShield Standard Plan" in text:
        found.add("prushield-standard-plan")
    return found


# ---------- 1. catalogue import ----------

def test_import_loads_every_plan_and_document_with_fields_intact(imported):
    assert Policy.query.filter(Policy.slug.isnot(None)).count() == len(CATALOGUE["plans"]) == 49
    assert Document.query.count() == len(CATALOGUE["documents"]) == 70
    for item in CATALOGUE["plans"]:
        row = plan(item["id"])
        assert (row.name, row.kind, row.categories, row.catalogue_status, row.contract_status) == (
            item["name"], item["kind"], item["categories"], item["catalogue_status"], item["contract_status"])
        assert row.product_url == item["product_url"] and row.coverage_focus == item["coverage_focus"]
        assert row.applicable_base_plans == item["applicable_base_plans"]
        assert row.catalogue_as_of.isoformat() == CATALOGUE["as_of"]
        assert row.monthly_premium_cents is None and not row.is_demo
        linked = [d.doc_id for d in catalogue.plan_documents(row)]
        assert linked == item["document_ids"]
    for item in CATALOGUE["documents"]:
        row = Document.query.filter_by(doc_id=item["id"]).one()
        assert (row.type, row.url, row.language, row.version, row.page_count, row.sha256, row.local_path) == (
            item["type"], item.get("url") or None, item.get("language") or None, item.get("version") or None,
            item.get("page_count"), item.get("sha256") or None, item.get("local_path") or None)


def test_import_is_idempotent(imported):
    def state():
        return (Policy.query.count(), Document.query.count(), PlanDocument.query.count(),
                sorted((p.id, p.slug) for p in Policy.query.filter(Policy.slug.isnot(None))))
    before = state()
    result = run(imported, "import-catalogue")
    assert result.exit_code == 0 and "0 new plans, 0 new documents" in result.output
    assert state() == before


def test_import_refuses_a_broken_catalogue_and_writes_nothing(app, seeded, tmp_path):
    broken = json.loads(json.dumps(CATALOGUE))
    broken["plans"][0]["document_ids"].append("no-such-document")
    (tmp_path / "prudential-catalogue.json").write_text(json.dumps(broken), encoding="utf-8")
    result = run(app, "import-catalogue", "--dir", str(tmp_path))
    assert result.exit_code != 0 and "no-such-document" in result.output
    assert Policy.query.filter(Policy.slug.isnot(None)).count() == 0


def test_demo_plans_are_hidden_not_deleted_and_flags_and_scenarios_survive(app, seeded, browser):
    browser.signup()
    browser.onboard()
    demo = Policy.query.filter_by(is_demo=True, name="PRUActive Term").one()
    user = User.query.filter_by(email="alex@example.com").one()
    db.session.add(ComparisonFlag(user_id=user.id, policy_id=demo.id, row_key="premium_waiver"))
    db.session.add(Scenario(user_id=user.id, inputs={"fields": {}, "plans": [demo.id], "events": []}, result={}))
    db.session.commit()
    assert run(app, "import-catalogue").exit_code == 0
    assert Policy.query.filter_by(is_demo=True).count() == 6
    assert all(p.is_hidden for p in Policy.query.filter_by(is_demo=True))
    assert ComparisonFlag.query.count() == 1 and Scenario.query.count() == 1
    assert [p.name for p in catalogue.catalogue_policies()][:1] == ["PRUShield and PRUExtra"]
    assert not any(p.is_demo for p in catalogue.catalogue_policies())
    assert {p.is_demo for p in catalogue.legacy_demo_policies()} == {True}


def test_compare_simulator_and_dashboard_still_use_only_the_hidden_demo_plans(user):
    for url in ("/compare", "/simulator", "/dashboard"):
        page = user.get(url).get_data(as_text=True)
        assert user.get(url).status_code == 200, url
        assert "prushield-standard-plan" not in page and "PRUActive Term" in page, url
    compare_page = user.get("/compare").get_data(as_text=True)
    assert "S$142/mo" in compare_page  # the fictional premium, not a catalogue plan
    assert "Closed to new members" not in compare_page and "pruextra" not in compare_page


# ---------- premium ----------

def test_premium_is_never_a_number_for_catalogue_plans(user):
    assert Policy.query.filter(Policy.slug.isnot(None), Policy.monthly_premium_cents.isnot(None)).count() == 0
    listing = user.get("/plans").get_data(as_text=True).split("Sample plans with AI summary")[0]
    assert "S$" not in listing   # only the fictional demo section below it shows prices
    for item in CATALOGUE["plans"]:
        page = user.get(f"/plans/{item['id']}").get_data(as_text=True)
        assert "Premium varies. See" in page and "S$" not in page and not re.search(r"/mo\b", page), item["id"]


def test_premium_link_goes_to_the_rate_tables_document_when_the_plan_has_one(user):
    page = user.get("/plans/prushield-premier").get_data(as_text=True)
    rates = next(d for d in CATALOGUE["documents"] if d["id"] == "prushield-pruextra-premium-tables")
    assert f'<a href="{rates["url"]}" target="_blank" rel="noopener noreferrer">Prudential\'s official rate tables</a>' in page
    other = user.get("/plans/pruactive-term").get_data(as_text=True)
    assert rates["url"] not in other


def test_plan_matching_treats_an_unknown_premium_as_not_fitting(app):
    from app import plan_match
    band = plan_match.Budget("Under S$100", 10_000, False)
    assert plan_match.budget_tradeoff(None, band)["known"] is False
    row = plan_match.budget_row(band, None, "under_100")
    assert row["status"] == plan_match.UNKNOWN and "varies" in row["text"]
    ranked = plan_match.rank_plans([Policy(name="x", type="Health", monthly_premium_cents=None)], [], band)
    assert ranked[0]["fits_budget"] is False and ranked[0]["matched"] is False


# ---------- selector rules ----------

def test_selectors_exclude_families_and_closed_plans_and_gate_riders(imported):
    slugs = lambda chosen=(): {p.slug for p in catalogue.selectable_policies(chosen)}
    base = slugs()
    assert "prushield-and-pruextra" not in base                      # product family
    assert not {"prushield-a", "prushield-b"} & base                  # closed to new members
    assert not {"pruextra-premier-care", "pruextra-preferred-care", "pruextra-plus-care"} & base  # riders need a base
    assert {"prushield-premier", "prushield-plus", "prushield-standard-plan", "pruactive-term"} <= base
    premier = slugs(("prushield-premier",))
    assert {"pruextra-premier-care", "pruextra-preferred-care"} <= premier and "pruextra-plus-care" not in premier
    plus = slugs(("prushield-plus",))
    assert "pruextra-plus-care" in plus and not {"pruextra-premier-care", "pruextra-preferred-care"} & plus
    standard = slugs(("prushield-standard-plan",))
    assert not {"pruextra-premier-care", "pruextra-preferred-care", "pruextra-plus-care"} & standard
    for chosen in (("prushield-a",), ("prushield-b",), ("prushield-and-pruextra",)):
        assert not {"pruextra-premier-care", "pruextra-plus-care", "pruextra-preferred-care"} & slugs(chosen)
        assert not {"prushield-a", "prushield-b", "prushield-and-pruextra"} & slugs(chosen)


def test_closed_plans_are_viewable_but_not_listed_under_all(user):
    listing = user.get("/plans").get_data(as_text=True)
    assert "/plans/prushield-a\"" not in listing and "/plans/prushield-b\"" not in listing
    closed = user.get("/plans?kind=closed").get_data(as_text=True)
    assert "/plans/prushield-a\"" in closed and "/plans/prushield-b\"" in closed
    page = user.get("/plans/prushield-a")
    assert page.status_code == 200
    assert "closed to new members" in page.get_data(as_text=True)


def test_rider_pages_name_their_base_plan_and_base_plans_list_their_riders(user):
    rider = user.get("/plans/pruextra-plus-care").get_data(as_text=True)
    assert "Needs one of these base plans" in rider and 'href="/plans/prushield-plus"' in rider
    assert "prushield-premier\"" not in rider
    base = user.get("/plans/prushield-premier").get_data(as_text=True)
    assert 'href="/plans/pruextra-premier-care"' in base and 'href="/plans/pruextra-preferred-care"' in base
    assert "pruextra-plus-care" not in base
    family = user.get("/plans/prushield-and-pruextra").get_data(as_text=True)
    assert "product family" in family and 'href="/plans/prushield-standard-plan"' in family


# ---------- 2. contract ingestion ----------

def test_ingestion_refuses_a_tampered_pdf_and_stores_nothing(imported, tmp_path):
    folder = tmp_path / "prudential"
    shutil.copytree(DATA / "contracts", folder / "contracts")
    target = folder / "contracts" / f"{SHIELD_DOC}.pdf"
    raw = bytearray(target.read_bytes())
    raw[-10] ^= 0xFF
    target.write_bytes(bytes(raw))
    result = run(imported, "ingest-contracts", "--dir", str(folder))
    assert result.exit_code != 0 and "SHA-256 mismatch" in result.output and SHIELD_DOC in result.output
    assert DocumentVersion.query.count() == 0 and Paragraph.query.count() == 0   # even the untouched A/B file is not stored


def test_ingestion_stores_versions_and_is_idempotent_with_stable_ids(ingested):
    versions = {v.document.doc_id: v for v in DocumentVersion.query}
    assert set(versions) == {SHIELD_DOC, AB_DOC}
    for doc_id, version in versions.items():
        item = next(d for d in CATALOGUE["documents"] if d["id"] == doc_id)
        assert (version.sha256, version.version, version.page_count) == (item["sha256"], item["version"], item["page_count"])
    ids = sorted(p.id for p in Paragraph.query)
    assert len(ids) > 1000
    result = run(ingested, "ingest-contracts")
    assert result.exit_code == 0 and "already ingested" in result.output
    assert sorted(p.id for p in Paragraph.query) == ids and DocumentVersion.query.count() == 2


def test_ingestion_needs_the_catalogue_first(app, seeded):
    result = run(app, "ingest-contracts")
    assert result.exit_code != 0 and "import-catalogue" in result.output


def test_paragraphs_carry_hash_box_page_and_clause(ingested):
    for row in Paragraph.query.limit(300):
        assert row.paragraph_sha256 == contracts.paragraph_hash(row.text)
        assert row.text == " ".join(row.text.split())
        assert row.x0 < row.x1 and row.y0 < row.y1 and 1 <= row.page <= 59
    clause = Paragraph.query.filter_by(clause_number="1.10.4", heading="Co-insurance").all()
    assert len(clause) == 2 and clause[0].text == "1.10.4 Co-insurance"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_the_three_citation_examples_resolve_to_the_right_paragraph_and_scope(ingested, index):
    example = CATALOGUE["citation_examples"][index]
    row = paragraph_for(example)
    assert row is not None, "no stored paragraph at the example's page and box"
    assert row.paragraph_sha256 == example["paragraph_sha256"]
    assert row.document_version.document.doc_id == example["document_id"]
    assert set(row.scope) == scope_slugs(example["scope"])


def test_scope_part_one_is_premier_and_plus_part_two_is_standard_and_ab_is_closed(ingested):
    part_one = {tuple(p.scope) for p in Paragraph.query.filter(Paragraph.page <= 36).join(DocumentVersion)
                .join(Document).filter(Document.doc_id == SHIELD_DOC)}
    part_two = {tuple(p.scope) for p in Paragraph.query.filter(Paragraph.page >= 37).join(DocumentVersion)
                .join(Document).filter(Document.doc_id == SHIELD_DOC)}
    ab = {tuple(p.scope) for p in Paragraph.query.join(DocumentVersion).join(Document).filter(Document.doc_id == AB_DOC)}
    assert part_one == {("prushield-premier", "prushield-plus")}
    assert part_two == {("prushield-standard-plan",)}
    assert ab == {("prushield-a", "prushield-b")}
    assert Paragraph.query.filter(Paragraph.page == 37, Paragraph.text == "PART TWO").count() == 1


# ---------- contract status and plan pages ----------

def test_contract_status_before_and_after_ingestion(imported, browser):
    browser.signup()
    browser.onboard()
    before = browser.get("/plans/prushield-premier").get_data(as_text=True)
    assert "Contract wording not verified" in before and "Contract wording verified" not in before
    assert run(imported, "ingest-contracts").exit_code == 0
    after = browser.get("/plans/prushield-premier").get_data(as_text=True)
    assert "Contract wording verified (sample, April 2026)" in after


def test_every_plan_card_and_page_shows_a_contract_status(user):
    listing = user.get("/plans").get_data(as_text=True).split("Sample plans with AI summary")[0]
    cards = re.findall(r'<article class="card plan-card">.*?</article>', listing, re.S)
    assert len(cards) == 49 - 2  # all but the two closed plans
    for card in cards:
        assert re.search(r"Contract wording (not verified|verified \(sample, April 2026\))", card)
        assert "View plan" in card and 'class="chip chip-info"' in card
    verified = [c for c in cards if "Contract wording verified" in c]
    assert len(verified) == 3   # Premier, Plus, Standard
    for item in CATALOGUE["plans"]:
        page = user.get(f"/plans/{item['id']}").get_data(as_text=True)
        assert "Contract wording" in page, item["id"]


def test_plan_without_a_contract_says_so_and_has_no_sources(user):
    page = user.get("/plans/pruactive-term").get_data(as_text=True)
    assert "Contract wording not verified. Explanations from the brochure are not available yet." in page
    assert "Open in the source PDF" not in page
    assert "Brochure" in page


def test_shield_pages_show_product_variant_version_date_documents_and_sources(user):
    page = user.get("/plans/prushield-premier").get_data(as_text=True)
    assert "<h1 class=\"page-title\">PRUShield Premier</h1>" in page
    assert "Plan variant" in page and "Catalogue checked 20 November 2026" in page
    assert "Version: April 2026; PRUShield Version 2 2026" in page
    assert "Sample contract (published by MOH)" in page and "Brochure" in page and "EN" in page
    sources = re.findall(r'href="(/citations/(\d+)\?plan=prushield-premier)"', page)
    assert len(sources) == 3
    standard = user.get("/plans/prushield-standard-plan").get_data(as_text=True)
    premier_ids = {i for _, i in sources}
    standard_ids = {i for _, i in re.findall(r'href="(/citations/(\d+)\?plan=prushield-standard-plan)"', standard)}
    assert len(standard_ids) == 3 and not premier_ids & standard_ids
    for pid in standard_ids:
        assert "prushield-standard-plan" in db.session.get(Paragraph, int(pid)).scope


def test_plans_filters_by_category_and_kind_without_javascript(user):
    def names(url):
        return re.findall(r'<h2 class="plan-name">([^<]+)</h2>', user.get(url).get_data(as_text=True))
    expected = {k: [p["name"] for p in CATALOGUE["plans"] if k in p["categories"] and p["catalogue_status"] != "closed_to_new_members"]
                for k in ("health", "life", "wealth", "legacy")}
    for key, label in (("health", "Health"), ("life", "Life"), ("wealth", "Wealth"), ("legacy", "Legacy")):
        assert names(f"/plans?category={key}") == expected[key], key
    assert len(names("/plans?kind=rider")) == 3
    assert len(names("/plans?kind=family")) == 1 and len(names("/plans?kind=variant")) == 3
    assert len(names("/plans?kind=closed")) == 2
    assert names("/plans?category=legacy&kind=rider") == []
    assert "No plans match these filters" in user.get("/plans?category=legacy&kind=rider").get_data(as_text=True)
    assert len(names("/plans?category=nonsense&kind=nonsense")) == 47
    page = user.get("/plans").get_data(as_text=True)
    links = re.findall(r'<nav class="filters[^"]*"[^>]*>(.*?)</nav>', page, re.S)
    assert len(links) == 2 and all("<button" not in n and "onclick" not in n for n in links)


def test_unknown_plan_slug_is_404_and_pages_need_a_client_account(app, user):
    assert user.get("/plans/not-a-plan").status_code == 404
    anonymous = app.test_client()
    assert anonymous.get("/plans/prushield-premier").location.endswith("/login")
    assert anonymous.get("/citations/1").location.endswith("/login")


def test_the_old_numeric_plan_page_still_works_for_demo_plans(user):
    demo = Policy.query.filter_by(is_demo=True, name="PRUActive Term").one()
    assert user.get(f"/plans/{demo.id}").status_code == 200
    catalogue_row = plan("pruactive-term")
    assert user.get(f"/plans/{catalogue_row.id}").status_code == 404   # the numeric route is for demo plans only


# ---------- 3. citation viewer ----------

def anchor_ids():
    return [paragraph_for(e).id for e in CATALOGUE["citation_examples"]]


def test_viewer_renders_the_pdf_page_with_the_highlight_at_the_paragraph_box(user):
    p4, p30, p55 = anchor_ids()
    page = user.get(f"/citations/{p30}?plan=prushield-premier").get_data(as_text=True)
    example = CATALOGUE["citation_examples"][1]
    x0, y0, x1, y1 = example["paragraph_bbox_points"]
    assert f'src="/citations/{p30}/page/30.png"' in page
    assert 'viewBox="0 0 612.0 792.0"' in page
    rect = re.search(r'<rect class="pdf-highlight"[^>]*x="([\d.]+)" y="([\d.]+)"\s+width="([\d.]+)" height="([\d.]+)"', page)
    got = tuple(float(v) for v in rect.groups())
    assert got == pytest.approx((x0, y0, x1 - x0, y1 - y0), abs=0.002)
    assert "Page 30 of 59" in page and "April 2026; PRUShield Version 2 2026" in page
    assert "Open source PDF" in page and "#page=30" in page and 'rel="noopener noreferrer"' in page
    assert "PRUShield Premier" in page and "PRUShield Plus" in page and "Standard Plan" not in page.split("Applies to")[1].split("</section>")[0]
    assert "Co-insurance is the amount" in page and "clause 1.10.4" in page
    assert "<script" not in page and "style=" not in page


def test_viewer_page_navigation_stays_inside_the_cited_wording(user):
    p4, p30, p55 = anchor_ids()
    at_36 = user.get(f"/citations/{p30}?page=36").get_data(as_text=True)
    assert "Page 36 of 59" in at_36 and "Back to cited paragraph (page 30)" in at_36
    assert "pdf-highlight" not in at_36                 # the highlight is only on the cited page
    assert "Next →</a>" not in at_36                    # page 37 is Standard wording
    assert f"/citations/{p30}?page=35" in at_36
    assert "Page 30 of 59" in user.get(f"/citations/{p30}?page=40").get_data(as_text=True)   # out of scope: falls back
    at_37 = user.get(f"/citations/{p55}?page=37").get_data(as_text=True)
    assert "← Prev</a>" not in at_37 and "Page 37 of 59" in at_37
    assert "Page 55 of 59" in user.get(f"/citations/{p55}?page=36").get_data(as_text=True)
    assert user.get(f"/citations/{p30}/page/36.png").status_code == 200
    assert user.get(f"/citations/{p30}/page/37.png").status_code == 404
    assert user.get(f"/citations/{p55}/page/36.png").status_code == 404
    assert user.get(f"/citations/{p55}/page/59.png").status_code == 200


def test_standard_citation_does_not_open_part_one_wording_and_the_reverse(user):
    p4, p30, p55 = anchor_ids()
    wrong = user.get(f"/citations/{p30}?plan=prushield-standard-plan")
    assert wrong.status_code == 409
    text = wrong.get_data(as_text=True)
    assert "different plan" in text and "PRUShield Premier, PRUShield Plus" in text and "Co-insurance is the amount" not in text
    reverse = user.get(f"/citations/{p55}?plan=prushield-plus")
    assert reverse.status_code == 409 and "PRUShield Standard Plan" in reverse.get_data(as_text=True)
    assert user.get(f"/citations/{p55}?plan=prushield-standard-plan").status_code == 200
    assert user.get(f"/citations/{p4}?plan=prushield-plus").status_code == 200
    assert user.get(f"/citations/{p4}?plan=no-such-plan").status_code == 404
    assert user.get(f"/citations/{p4}?plan=pruactive-term").status_code == 409


def test_page_image_is_a_same_origin_png_of_the_right_size(user):
    p4, p30, p55 = anchor_ids()
    response = user.get(f"/citations/{p30}/page/30.png")
    assert response.mimetype == "image/png" and response.data[:8] == b"\x89PNG\r\n\x1a\n"
    pixmap = pymupdf.Pixmap(response.data)
    assert (pixmap.width, pixmap.height) == (1224, 1584)
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "img-src 'self' data:" in csp and "script-src" not in csp and "http" not in csp


def test_viewer_refuses_when_the_pdf_no_longer_matches_the_stored_hash(app, user, tmp_path):
    p4, p30, p55 = anchor_ids()
    folder = tmp_path / "prudential"
    shutil.copytree(DATA / "contracts", folder / "contracts")
    target = folder / "contracts" / f"{SHIELD_DOC}.pdf"
    raw = bytearray(target.read_bytes())
    raw[-10] ^= 0xFF
    target.write_bytes(bytes(raw))
    app.config["CATALOGUE_DIR"] = str(folder)
    page = user.get(f"/citations/{p30}")
    assert page.status_code == 409
    body = page.get_data(as_text=True)
    assert "SHA-256 no longer matches" in body and "pdf-highlight" not in body and "Co-insurance is the amount" not in body
    assert user.get(f"/citations/{p30}/page/30.png").status_code == 409
    assert user.get(f"/citations/{p55}").status_code == 409
    missing = tmp_path / "empty"
    missing.mkdir()
    app.config["CATALOGUE_DIR"] = str(missing)
    assert user.get(f"/citations/{p30}").status_code == 409


def test_highlight_rect_maps_points_to_page_percentages():
    rect = contracts.highlight_rect((141.121, 572.127, 518.438, 601.199), (612.0, 792.0))
    assert (rect["x"], rect["y"]) == (141.121, 572.127)
    assert rect["w"] == pytest.approx(377.317) and rect["h"] == pytest.approx(29.072)
    assert rect["pct"]["left"] == pytest.approx(141.121 / 612 * 100)
    assert rect["pct"]["top"] == pytest.approx(572.127 / 792 * 100)
    assert rect["pct"]["width"] == pytest.approx(377.317 / 612 * 100)
    assert contracts.pixel_rect((10, 20, 30, 40), 2) == (20, 40, 60, 80)


def test_highlight_box_lands_on_the_rendered_text(ingested):
    """Render page 30 and check the ink inside the box starts and ends where the box does."""
    example = CATALOGUE["citation_examples"][1]
    x0, y0, x1, y1 = example["paragraph_bbox_points"]
    path = DATA / "contracts" / f"{SHIELD_DOC}.pdf"
    scale = contracts.RENDER_SCALE
    pixmap = pymupdf.Pixmap(contracts.render_page_png(path, 30))
    n = pixmap.n

    def ink_rows(left, top, right, bottom):
        rows = []
        for y in range(int(top * scale), int(bottom * scale)):
            for x in range(int(left * scale), int(right * scale)):
                offset = (y * pixmap.width + x) * n
                if pixmap.samples[offset] < 128:
                    rows.append(y)
                    break
        return rows

    inside = ink_rows(x0, y0, x1, y1)
    assert inside, "no text inside the highlight box"
    assert (y1 - 4) * scale <= max(inside) <= (y1 + 0.5) * scale      # last text row is at the bottom of the box
    assert not ink_rows(x0, y1 + 1, x1, y1 + 8)                          # blank gap just below the box
    with pymupdf.open(path) as pdf:
        text = " ".join(pdf[29].get_text("text", clip=pymupdf.Rect(x0, y0, x1, y1)).split())
    assert text == paragraph_for(example).text


def test_old_demo_citations_keep_working_on_their_new_address(app, seeded, browser):
    browser.signup()
    browser.onboard()
    from app.models import PolicyClause
    clause = PolicyClause.query.first()
    assert browser.get(f"/citations/demo/{clause.id}").status_code == 200
    assert browser.get("/citations/999999").status_code == 404


def test_plans_page_still_links_the_demo_plans_to_ai_summary_and_compare(user):
    page = user.get("/plans").get_data(as_text=True)
    head, section = page.split("Sample plans with AI summary and Compare")
    demo = Policy.query.filter_by(is_demo=True).order_by(Policy.id).all()
    for p in demo:
        assert f'href="/plans/{p.id}"' in section
    assert section.count(">Compare<") == 6 and "Demo data" in section
    assert user.get(f"/plans/{demo[0].id}").status_code == 200
    assert len(re.findall(r'<h2 class="plan-name">', head)) == 47   # demo cards use h3, not mixed in
