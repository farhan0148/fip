"""Minimal FC portal. Read only for shared client data, plus the FC's gap-analysis decisions.

Every route goes through sharing.visible_categories. The gap analysis page shows figures built only from the
categories the client has shared; the FC may confirm or dismiss each finding and add a note.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import gap_analysis, sharing
from .security import check_csrf, fc_required

bp = Blueprint("fc", __name__, url_prefix="/fc")


@bp.get("")
@fc_required
def index():
    return render_template(
        "fc/index.html",
        clients=sharing.clients_for_fc(g.user),
        info=sharing.CATEGORY_INFO,
        page_title="My clients",
    )


@bp.get("/clients/<int:client_id>")
@fc_required
def client(client_id):
    categories = sharing.visible_categories(g.user, client_id)
    if not categories:
        # Same answer for "no such client", "not your client" and "nothing shared (any more)".
        abort(404)
    ordered = sharing.ordered(categories)
    sections = [(category, sharing.CATEGORY_INFO[category], sharing.load_category(category, client_id))
                for category in ordered]
    label = next((data["name"] for cat, _, data in sections if cat == "profile_answers"), f"Client #{client_id}")
    sharing.record_views(g.user, client_id, categories)
    return render_template("fc/client.html", label=label, sections=sections, page_title=label, client_id=client_id)


def _gap_client(client_id):
    """Guard for the gap-analysis routes: the FC must have at least one active grant for this client."""
    categories = sharing.visible_categories(g.user, client_id)
    if not categories:
        abort(404)                                   # same answer as "no such client": ids reveal nothing
    return categories


def _client_label(client_id, categories):
    if "profile_answers" in categories:
        data = sharing.load_category("profile_answers", client_id)
        return data["name"]
    return f"Client #{client_id}"


@bp.get("/clients/<int:client_id>/gap")
@fc_required
def gap(client_id):
    categories = _gap_client(client_id)
    analysis = gap_analysis.build_analysis(g.user, client_id)
    label = _client_label(client_id, categories)
    return render_template("fc/gap.html", analysis=analysis, client_id=client_id, label=label,
                           saved=request.args.get("saved") == "1", page_title=f"Gap analysis: {label}")


@bp.post("/clients/<int:client_id>/gap/decide")
@fc_required
def gap_decide(client_id):
    check_csrf()
    _gap_client(client_id)
    finding_key = request.form.get("finding_key", "")
    status = request.form.get("status", "")
    note = request.form.get("note", "")
    if not gap_analysis.record_decision(g.user, client_id, finding_key, status, note):
        abort(400, description="That finding is no longer available. Reload the page and try again.")
    return redirect(url_for("fc.gap", client_id=client_id, saved=1))
