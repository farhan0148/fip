from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, render_template, request

from . import simulator
from .extensions import db
from .models import Policy, Scenario
from .security import check_csrf, onboarding_complete_required

bp = Blueprint("simulator", __name__)


def _catalogue():
    """Demo plans in a stable order, keyed by id."""
    policies = Policy.query.filter_by(is_demo=True).order_by(Policy.id).all()
    return {p.id: simulator.catalogue_entry(p) for p in policies}


def _to_form_strings(inputs, catalogue):
    """Saved ints back to the strings the form shows. Drops plans that no longer exist."""
    plans = [pid for pid in inputs.get("plans", []) if pid in catalogue and catalogue[pid]["addable"]]
    dropped = len(inputs.get("plans", [])) - len(plans)
    return {
        "fields": {key: str(value) for key, value in inputs["fields"].items()},
        "plans": plans,
        "events": [
            {"type": e["type"], "age": str(e["age"]), "amount": str(e["amount"])}
            for e in inputs.get("events", [])
        ],
    }, dropped


@bp.get("/simulator")
@onboarding_complete_required
def index():
    catalogue = _catalogue()
    saved = Scenario.query.filter_by(user_id=g.user.id).first()
    state, notice, saved_at = None, None, None
    if saved is not None:
        state, dropped = _to_form_strings(saved.inputs, catalogue)
        saved_at = saved.updated_at.isoformat()
        if dropped:
            notice = "A plan in your saved scenario is no longer available and was removed. Save again to keep this change."
    data = {
        "catalogue": list(catalogue.values()),
        "saved": state,
        "saved_at": saved_at,
        "notice": notice,
        "event_types": [
            {"key": key, "label": info["label"], "supported": info["supported"]}
            for key, info in simulator.EVENT_TYPES.items()
        ],
        "limits": {"max_events": simulator.MAX_EVENTS, "max_plans": simulator.MAX_PLANS},
    }
    return render_template("simulator.html", data=data, page_title="Coverage Needs Simulator")


@bp.post("/simulator/save")
@onboarding_complete_required
def save():
    """Validate and recalculate on the server, then store the scenario for this account."""
    check_csrf()
    payload = request.get_json(silent=True)
    catalogue = _catalogue()
    clean, errors = simulator.validate(payload, catalogue)
    if errors:
        return jsonify(ok=False, errors=errors), 400

    result = simulator.calculate(clean, catalogue)
    scenario = Scenario.query.filter_by(user_id=g.user.id).first()
    if scenario is None:
        scenario = Scenario(user_id=g.user.id, inputs=clean, result=result)
        db.session.add(scenario)
    else:
        scenario.inputs = clean
        scenario.result = result
        scenario.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(ok=True, result=result, saved_at=scenario.updated_at.isoformat())
