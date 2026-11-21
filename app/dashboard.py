from datetime import datetime, timezone

from flask import Blueprint, g, render_template

from . import demo_fixture, plan_match, score as death_score, services
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
        cover_score=death_score.baseline(g.user),
        top=top,
        page_title="Dashboard",
    )

