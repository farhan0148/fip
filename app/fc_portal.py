"""Minimal FC portal. Read only. Every route goes through sharing.visible_categories."""

from flask import Blueprint, abort, g, render_template

from . import sharing
from .security import fc_required

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
    return render_template("fc/client.html", label=label, sections=sections, page_title=label)
