"""Source contract PDFs: hash check, paragraph extraction (PDF text layer, no AI) and page rendering."""

import hashlib
import re
from pathlib import Path

import pymupdf

from .extensions import db
from .models import Document, DocumentVersion, Paragraph, Policy

# Which plans each page range of a sample contract applies to (plan slugs).
# PRUShield V2 (Apr 2026): Part One (pages 1-36) is Premier and Plus, Part Two (pages 37-59) is Standard.
CONTRACT_SCOPES = {
    "prushield-premier-plus-standard-apr-2026": (
        (1, 36, ("prushield-premier", "prushield-plus")),
        (37, 59, ("prushield-standard-plan",)),
    ),
    "prushield-a-b-apr-2026": ((1, 21, ("prushield-a", "prushield-b")),),
}

_CLAUSE = re.compile(r"^(\d+(?:\.\d+)+)\.?\s+(\S.*)$")


class IngestError(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise(text):
    """Whitespace-normalised text: runs of whitespace become one space, ends trimmed."""
    return " ".join(text.split())


def paragraph_hash(text):
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def scope_for_page(document_slug, page):
    for first, last, slugs in CONTRACT_SCOPES[document_slug]:
        if first <= page <= last:
            return list(slugs)
    raise IngestError(f"Page {page} of {document_slug} has no plan scope.")


def _line_text(line):
    return "".join(span["text"] for span in line["spans"])


def extract_paragraphs(pdf_path):
    """Paragraphs of a PDF: [{page, ordinal, bbox, text, clause_number, heading}], pages 1-based.

    A paragraph is a PyMuPDF text block. When a block starts with a clause heading line such as
    "1.10.4 Co-insurance" followed by indented body lines, the heading becomes its own paragraph and the
    body is a paragraph that carries the clause number and heading.
    """
    out = []
    with pymupdf.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            ordinal = 0
            for block in page.get_text("dict")["blocks"]:
                if block["type"] != 0 or not block["lines"]:
                    continue
                lines = [ln for ln in block["lines"] if normalise(_line_text(ln))]
                if not lines:
                    continue

                def add(chunk, clause=None, heading=None):
                    nonlocal ordinal
                    text = normalise(" ".join(_line_text(ln) for ln in chunk))
                    x0 = min(ln["bbox"][0] for ln in chunk)
                    y0 = min(ln["bbox"][1] for ln in chunk)
                    x1 = max(ln["bbox"][2] for ln in chunk)
                    y1 = max(ln["bbox"][3] for ln in chunk)
                    out.append(
                        {
                            "page": page_index,
                            "ordinal": ordinal,
                            "bbox": (x0, y0, x1, y1),
                            "text": text,
                            "clause_number": clause,
                            "heading": heading,
                        }
                    )
                    ordinal += 1

                first_text = normalise(_line_text(lines[0]))
                match = _CLAUSE.match(first_text)
                if match and len(lines) > 1 and lines[1]["bbox"][0] > lines[0]["bbox"][0] + 5:
                    add(lines[:1], clause=match.group(1), heading=match.group(2)[:200])
                    add(lines[1:], clause=match.group(1), heading=match.group(2)[:200])
                else:
                    add(lines, clause=match.group(1) if match else None)
    return out


def ingest_document(document, base_dir):
    """Verify the PDF against the catalogue hash, then store its version and paragraphs. Idempotent.

    Raises IngestError, writing nothing, if the file is missing or its SHA-256 does not match.
    Returns (number of paragraphs, True if the version was new).
    """
    if document.doc_id not in CONTRACT_SCOPES:
        raise IngestError(f"{document.doc_id}: no plan scope is defined for this contract.")
    if not document.local_path or not document.sha256:
        raise IngestError(f"{document.doc_id}: the catalogue has no local file or hash for this document.")
    path = Path(base_dir) / document.local_path
    if not path.is_file():
        raise IngestError(f"{document.doc_id}: file not found at {path}.")
    actual = sha256_file(path)
    if actual != document.sha256:
        raise IngestError(
            f"{document.doc_id}: SHA-256 mismatch. The catalogue says {document.sha256}, the file is {actual}. "
            "Nothing was ingested."
        )
    with pymupdf.open(path) as pdf:
        pages = len(pdf)
    if document.page_count and pages != document.page_count:
        raise IngestError(f"{document.doc_id}: the PDF has {pages} pages, the catalogue says {document.page_count}.")

    known = {p.slug for p in Policy.query.filter(Policy.slug.isnot(None))}
    for _, _, slugs in CONTRACT_SCOPES[document.doc_id]:
        unknown = [s for s in slugs if s not in known]
        if unknown:
            raise IngestError(f"{document.doc_id}: scope names plans that are not imported: {unknown}. Run import-catalogue first.")

    extracted = extract_paragraphs(path)
    for item in extracted:  # fail before writing anything if a page is out of scope
        item["scope"] = scope_for_page(document.doc_id, item["page"])

    version = DocumentVersion.query.filter_by(document_id=document.id, sha256=actual).first()
    created = version is None
    if created:
        version = DocumentVersion(
            document_id=document.id, sha256=actual, version=document.version, page_count=pages
        )
        db.session.add(version)
        db.session.flush()

    existing = {(p.page, p.ordinal): p for p in Paragraph.query.filter_by(document_version_id=version.id)}
    seen = set()
    for item in extracted:
        key = (item["page"], item["ordinal"])
        seen.add(key)
        row = existing.get(key)
        if row is None:
            row = Paragraph(document_version_id=version.id, page=item["page"], ordinal=item["ordinal"])
            db.session.add(row)
        row.x0, row.y0, row.x1, row.y1 = item["bbox"]
        row.text = item["text"]
        row.paragraph_sha256 = paragraph_hash(item["text"])
        row.clause_number = item["clause_number"]
        row.heading = item["heading"]
        row.scope = item["scope"]
    for key, row in existing.items():
        if key not in seen:
            db.session.delete(row)
    db.session.commit()
    return len(extracted), created


def ingest_all(base_dir):
    """Ingest every catalogue contract that has a scope definition. Returns {doc_id: (paragraphs, created)}."""
    documents = Document.query.filter(Document.doc_id.in_(list(CONTRACT_SCOPES))).order_by(Document.id).all()
    if not documents:
        raise IngestError("No contract documents in the database. Run flask import-catalogue first.")
    # Check every hash first so a bad file stops the whole run before anything is written.
    for document in documents:
        if not document.local_path or not document.sha256:
            raise IngestError(f"{document.doc_id}: the catalogue has no local file or hash for this document.")
        path = Path(base_dir) / document.local_path
        if not path.is_file():
            raise IngestError(f"{document.doc_id}: file not found at {path}.")
        actual = sha256_file(path)
        if actual != document.sha256:
            raise IngestError(
                f"{document.doc_id}: SHA-256 mismatch. The catalogue says {document.sha256}, the file is {actual}. "
                "Nothing was ingested."
            )
    return {d.doc_id: ingest_document(d, base_dir) for d in documents}


# ---------- viewer helpers ----------

def verified_pdf_path(document, version, base_dir):
    """Path of the source PDF if its current SHA-256 equals the stored version hash, else raises IngestError."""
    if not document.local_path:
        raise IngestError("This document has no local copy.")
    path = Path(base_dir) / document.local_path
    if not path.is_file():
        raise IngestError("The source PDF file is missing.")
    if sha256_file(path) != version.sha256:
        raise IngestError("The source PDF has changed since it was ingested (its SHA-256 no longer matches).")
    return path


def highlight_rect(bbox, page_size):
    """The highlight rectangle for a paragraph. All values keep the PDF's top-left origin.

    Returns points (x, y, width, height) and the same as percentages of the page, so a page image of any
    size can be overlaid without knowing its pixel size.
    """
    x0, y0, x1, y1 = bbox
    width, height = page_size
    return {
        "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
        "pct": {
            "left": x0 / width * 100, "top": y0 / height * 100,
            "width": (x1 - x0) / width * 100, "height": (y1 - y0) / height * 100,
        },
    }


def pixel_rect(bbox, scale):
    """The same box in image pixels for a page rendered at `scale` pixels per PDF point."""
    x0, y0, x1, y1 = bbox
    return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)


RENDER_SCALE = 2.0


def render_page_png(pdf_path, page_number, scale=RENDER_SCALE):
    with pymupdf.open(pdf_path) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise IngestError("No such page.")
        pixmap = pdf[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")


def page_size(pdf_path, page_number):
    with pymupdf.open(pdf_path) as pdf:
        rect = pdf[page_number - 1].rect
        return (rect.width, rect.height)
