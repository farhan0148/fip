from datetime import datetime, timezone

from flask import Blueprint, g, render_template

from . import demo_fixture, plan_match, services
from .catalogue import legacy_demo_policies
from .extensions import db
from .security import login_required, onboarding_complete_required

bp = Blueprint("dashboard", __name__)

@bp.get("/dashboard")
@onboarding_complete_required
def index():
    first_visit = g.user.dashboard_first_seen_at is None
    if first_visit:
        g.user.dashboard_first_seen_at = datetime.now(timezone.utc)
        db.session.commit()

    top = plan_match.top_plans(g.user, legacy_demo_policies())
    return render_template(
        "dashboard.html",
        first_visit=first_visit,
        summary=services.profile_summary(g.user),
        priorities=services.priorities_for(g.user),
        demo=demo_fixture.load(),
        top=top,
        page_title="Dashboard",
    )


def _placeholder(title, needs_review):
    def view():
        return render_template("coming_soon.html", title=title, page_title=title)

    view.__name__ = f"{title.lower()}_placeholder"
    return onboarding_complete_required(view) if needs_review else login_required(view)


bp.add_url_rule("/settings", "settings", _placeholder("Settings", False))
