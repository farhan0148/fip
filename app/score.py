"""Estimated death cover score (Week 7). Death cover only, from the simulator.

    score = existing death cover / estimated need x 100, bounded to 0 to 100

Only when the estimated need is positive. With no need there is no percentage. The dashboard shows the
SAVED scenario (the baseline). The simulator page shows the live what-if score in the browser with the same rule
(static/js/score-live.js). Neither changes the calculator in simulator.py.
"""

from .models import Scenario


def score(cover, need):
    """0 to 100, or None when the estimated need is zero or negative."""
    if need is None or need <= 0:
        return None
    return max(0, min(100, round(cover / need * 100)))


def baseline(user):
    """What the dashboard shows. state is 'setup' (nothing saved), 'no_need' or 'score'."""
    saved = Scenario.query.filter_by(user_id=user.id).first()
    if saved is None:
        return {"state": "setup"}
    cover = saved.inputs["fields"]["existing_cover"]
    need = saved.result["need_today"]
    value = score(cover, need)
    out = {"state": "score" if value is not None else "no_need", "score": value, "need": need, "cover": cover,
           "gap": saved.result["gap_today"], "saved_at": saved.updated_at}
    return out
