"""Citation viewer for ingested source PDFs: /citations/<paragraph id>.

The page is rendered on the server as a PNG (same origin, no external scripts) and the cited paragraph is
drawn over it as an SVG rectangle whose coordinates are the paragraph's PDF-point box.
The viewer refuses to show anything if the PDF on disk no longer has the SHA-256 stored at ingestion,
and it only shows pages whose wording applies to the same plans as the cited paragraph.
"""

from pathlib import Path

from flask import Blueprint, Response, abort, render_template, request, url_for

from . import catalogue, catalogue_import, contracts
from .extensions import db
from .models import Paragraph, Policy
from .security import onboarding_complete_required

bp = Blueprint("citations", __name__)


def _error(title, message, status):
    from flask import current_app

    current_app.logger.warning("citation viewer: %s", message)
    return render_template("error.html", title=title, message=message), status


def _plan_names(slugs):
    names = {p.slug: p.name for p in Policy.query.filter(Policy.slug.in_(list(slugs)))}
    return [names.get(slug, slug) for slug in slugs]


def _allowed_pages(paragraph):
    """Pages of the same document version whose wording has exactly the cited paragraph's scope."""
    rows = db.session.query(Paragraph.page, Paragraph.scope).filter(
        Paragraph.document_version_id == paragraph.document_version_id
    )
    return sorted({page for page, scope in rows if scope == paragraph.scope})


def _source_dir():
    from flask import current_app

    return Path(current_app.config.get("CATALOGUE_DIR") or catalogue_import.default_dir(current_app))


def _load(paragraph_id):
    """(paragraph, pdf path) or an error response. Hash and scope checks happen here."""
    paragraph = db.session.get(Paragraph, paragraph_id)
    if paragraph is None:
        abort(404)
    version = paragraph.document_version
    try:
        path = contracts.verified_pdf_path(version.document, version, _source_dir())
    except contracts.IngestError as error:
        return paragraph, None, _error(
            "Source cannot be shown",
            f"{error} For safety this page is not shown. Open the official source PDF instead.",
            409,
        )
    return paragraph, path, None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@bp.get("/citations/<int:paragraph_id>")
@onboarding_complete_required
def view(paragraph_id):
    paragraph, path, problem = _load(paragraph_id)
    if problem is not None:
        return problem

    plan_slug = request.args.get("plan")
    plan = catalogue.plan_by_slug(plan_slug) if plan_slug else None
    if plan_slug and plan is None:
        abort(404)
    if plan is not None and plan.slug not in paragraph.scope:
        return _error(
            "This wording is for a different plan",
            f"This passage applies to {', '.join(_plan_names(paragraph.scope))}. "
            f"It does not apply to {plan.name}, so it is not shown for that plan.",
            409,
        )

    pages = _allowed_pages(paragraph)
    view_page = _to_int(request.args.get("page"))
    if view_page not in pages:
        view_page = paragraph.page
    index = pages.index(view_page)

    def link(page):
        return url_for("citations.view", paragraph_id=paragraph.id, page=page, plan=plan.slug if plan else None)

    size = contracts.page_size(path, view_page)
    document = paragraph.document_version.document
    highlight = contracts.highlight_rect(paragraph.bbox, size) if view_page == paragraph.page else None
    return render_template(
        "citation_pdf.html",
        paragraph=paragraph,
        document=document,
        version=paragraph.document_version,
        doc_label=catalogue.DOCUMENT_TYPE_LABELS.get(document.type, document.type),
        applies_to=_plan_names(paragraph.scope),
        view_page=view_page,
        pages=pages,
        page_position=index + 1,
        page_size=size,
        highlight=highlight,
        image_url=url_for("citations.page_image", paragraph_id=paragraph.id, page=view_page),
        prev_url=link(pages[index - 1]) if index > 0 else None,
        next_url=link(pages[index + 1]) if index < len(pages) - 1 else None,
        cited_url=link(paragraph.page),
        source_url=(f"{document.url}#page={view_page}" if document.url else None),
        back_url=url_for("plans.detail_slug", slug=plan.slug) if plan else url_for("plans.index"),
        back_label=f"Back to {plan.name}" if plan else "Back to plans",
        page_title=f"Source, page {view_page}",
    )


@bp.get("/citations/<int:paragraph_id>/page/<int:page>.png")
@onboarding_complete_required
def page_image(paragraph_id, page):
    paragraph, path, problem = _load(paragraph_id)
    if problem is not None:
        return problem
    if page not in _allowed_pages(paragraph):
        abort(404)
    try:
        png = contracts.render_page_png(path, page)
    except contracts.IngestError:
        abort(404)
    response = Response(png, mimetype="image/png")
    response.headers["Cache-Control"] = "private, max-age=300"
    return response
