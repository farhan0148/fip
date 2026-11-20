"""Import prudential-catalogue.json into the database. Safe to run again (upserts by id)."""

import json
from datetime import date
from pathlib import Path

from .catalogue import CATEGORY_LABELS, KIND_LABELS
from .extensions import db
from .models import Document, PlanDocument, Policy

INSURER = "Prudential Assurance Company Singapore"


class CatalogueError(Exception):
    pass


def default_dir(app):
    return Path(app.root_path).parent / "data" / "prudential"


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CatalogueError(f"Cannot read {path}: {error}") from error
    for key in ("plans", "documents", "as_of"):
        if key not in data:
            raise CatalogueError(f"The catalogue has no '{key}'.")
    return data


def validate(data):
    doc_ids = [d["id"] for d in data["documents"]]
    if len(set(doc_ids)) != len(doc_ids):
        raise CatalogueError("Duplicate document ids in the catalogue.")
    slugs = [p["id"] for p in data["plans"]]
    if len(set(slugs)) != len(slugs):
        raise CatalogueError("Duplicate plan ids in the catalogue.")
    for plan in data["plans"]:
        if plan["kind"] not in KIND_LABELS:
            raise CatalogueError(f"{plan['id']}: unknown kind '{plan['kind']}'.")
        for cat in plan["categories"]:
            if cat not in CATEGORY_LABELS:
                raise CatalogueError(f"{plan['id']}: unknown category '{cat}'.")
        missing = [d for d in plan.get("document_ids", []) if d not in set(doc_ids)]
        if missing:
            raise CatalogueError(f"{plan['id']}: unknown document ids {missing}.")
    names = {p["name"] for p in data["plans"]}
    for plan in data["plans"]:
        for base in plan.get("applicable_base_plans") or []:
            if base not in names:
                raise CatalogueError(f"{plan['id']}: applicable base plan '{base}' is not in the catalogue.")


def import_catalogue(path):
    """Upsert documents, plans and their links. Returns counts. Never touches demo rows, users or answers."""
    data = load(path)
    validate(data)
    as_of = date.fromisoformat(data["as_of"])

    counts = {"documents": 0, "plans": 0}
    documents = {}
    for item in data["documents"]:
        doc = Document.query.filter_by(doc_id=item["id"]).first()
        if doc is None:
            doc = Document(doc_id=item["id"])
            db.session.add(doc)
            counts["documents"] += 1
        doc.type = item["type"]
        doc.url = item.get("url") or None
        doc.language = item.get("language") or None
        doc.version = item.get("version") or None
        doc.page_count = item.get("page_count")
        doc.sha256 = item.get("sha256") or None
        doc.local_path = item.get("local_path") or None
        documents[item["id"]] = doc
    db.session.flush()

    for item in data["plans"]:
        policy = Policy.query.filter_by(slug=item["id"]).first()
        if policy is None:
            policy = Policy(slug=item["id"], is_demo=False, monthly_premium_cents=None)
            db.session.add(policy)
            counts["plans"] += 1
        policy.insurer = INSURER
        policy.name = item["name"]
        policy.kind = item["kind"]
        policy.categories = list(item["categories"])
        policy.type = CATEGORY_LABELS[item["categories"][0]]
        policy.catalogue_status = item["catalogue_status"]
        policy.coverage_focus = item.get("coverage_focus") or ""
        policy.description = (item.get("coverage_focus") or "")[:300]
        policy.contract_status = item["contract_status"]
        policy.product_url = item.get("product_url") or None
        policy.applicable_base_plans = list(item.get("applicable_base_plans") or [])
        policy.catalogue_as_of = as_of
        policy.is_hidden = False
        db.session.flush()
        PlanDocument.query.filter_by(policy_id=policy.id).delete(synchronize_session=False)
        for position, doc_id in enumerate(item.get("document_ids", [])):
            db.session.add(PlanDocument(policy_id=policy.id, document_id=documents[doc_id].id, position=position))
    db.session.commit()
    return counts
