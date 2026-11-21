"""FC Gap Analysis (feature F3), built only from data the client has shared.

What it shows
-------------
* A cover summary: estimated death-cover need, existing death cover, and the shortfall or surplus, side by side.
  The figures come from the client's latest SAVED simulator scenario (the baseline), reusing the same calculator
  result that the simulator and the dashboard score use. What-if scenarios (life events, added plans) are kept out
  of the baseline figures. When the simulator is not shared, or nothing is saved, the summary is "Not assessed".
  An explicitly entered zero is treated as zero; a value the client never provided is unknown.

* Possible policy overlaps: ONLY where there is supporting sample wording. An overlap candidate is a comparison row
  where two or more of the client's flagged items cite a clause and describe cover for the same kind of event. The
  finding links to the exact cited clauses so the FC can judge whether both policies really cover the same event
  under comparable conditions. A possible overlap is never a recommendation to cancel either policy.

Consent and access
-------------------
Every read goes through sharing.visible_categories(fc, client_id): the cover summary needs the "simulator" grant,
overlaps need "flagged_items". Revoking a grant removes the data from the analysis on the very next request. The
route layer (app/fc_portal.py) refuses access entirely when the client shares nothing.

FC decisions
------------
Each finding can be confirmed or dismissed, with an optional note, stored in `gap_findings` (one row per FC, client
and finding key). The source and assumptions stay visible. When a decision is saved, a hash of the data it was made
against is stored. If that data later changes, the finding is flagged "source changed, review again" and is NOT
reused (it stops prefilling the ROA) until the FC confirms it afresh. Only confirmed, not-stale findings prefill the
ROA.
"""

import hashlib

from . import comparison, sharing
from .extensions import db
from .models import ComparisonCell, ComparisonFlag, GapFinding, Policy, PolicyClause, Scenario

ROW_LABELS = dict(comparison.ROWS)
MAX_NOTE = 600

# Comparison rows that describe a payout for a specific event. A possible overlap only makes sense between
# policies that both pay for the same kind of event, so overlaps are grouped by these rows.
OVERLAP_ROWS = {
    "death_cover": "death or total permanent disability",
    "critical_illness": "a diagnosed critical illness",
    "hospital": "hospital or medical bills",
}
COVER_KEY = "cover"


def money(value):
    return ("\u2212" if value < 0 else "") + "S$" + f"{abs(int(value)):,}"


def _hash(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


# ---------- the cover summary ----------

def _cover_summary(client_id, visible):
    """Need, existing cover and shortfall/surplus from the saved baseline. 'Not assessed' when data is missing."""
    if "simulator" not in visible:
        return {"assessed": False, "reason": "not_shared",
                "message": "Not assessed. The client has not shared their simulator scenario with you."}
    scenario = Scenario.query.filter_by(user_id=client_id).first()
    if scenario is None:
        return {"assessed": False, "reason": "no_scenario",
                "message": "Not assessed. The client has not saved a scenario yet."}
    need = scenario.result["need_today"]            # baseline: today, before any what-if event or added plan
    cover = scenario.inputs["fields"]["existing_cover"]
    gap = scenario.result["gap_today"]              # need - existing cover
    return {
        "assessed": True,
        "need": need,
        "cover": cover,
        "gap": gap,                                 # positive = shortfall, negative = surplus, zero = exactly covered
        "shortfall": max(gap, 0),
        "surplus": max(-gap, 0),
        "saved_at": scenario.updated_at,
        "assumptions": _cover_assumptions(scenario),
        "source_hash": _hash("cover", need, cover, scenario.updated_at.isoformat()),
    }


def _cover_assumptions(scenario):
    """Plain, visible list of what the baseline figures rest on."""
    from . import simulator

    fields = scenario.inputs["fields"]
    out = []
    for key, (label, kind) in simulator.FIELDS.items():
        value = fields.get(key)
        if kind in ("age", "years"):
            out.append((label, str(value)))
        else:
            out.append((label, money(value)))
    out.append(("Scope", "Death cover only. Estimate, to be confirmed with the client."))
    out.append(("What-if excluded", "Life events and added plans are kept in the simulator and are not in these figures."))
    return out


# ---------- possible overlaps (only with supporting wording) ----------

def _overlap_candidates(client_id, visible):
    """Rows where two or more flagged, clause-cited policies describe cover for the same kind of event."""
    if "flagged_items" not in visible:
        return []
    flags = ComparisonFlag.query.filter_by(user_id=client_id).order_by(ComparisonFlag.created_at).all()
    by_row = {}
    for flag in flags:
        if flag.row_key not in OVERLAP_ROWS:
            continue
        cell = ComparisonCell.query.filter_by(policy_id=flag.policy_id, row_key=flag.row_key).first()
        # Only surface an overlap where there is supporting sample wording: the cell must cite a clause.
        if cell is None or cell.clause_id is None:
            continue
        clause = db.session.get(PolicyClause, cell.clause_id)
        policy = db.session.get(Policy, flag.policy_id)
        if clause is None or policy is None:
            continue
        by_row.setdefault(flag.row_key, []).append({
            "policy_id": policy.id,
            "plan": policy.name,
            "finding": cell.finding,
            "state": comparison.STATE_LABELS.get(cell.state, cell.state),
            "clause_id": clause.id,
            "clause_ref": f"p.{clause.page}, cl.{clause.clause}, line {clause.line}",
            "clause_text": clause.text,
        })
    candidates = []
    for row_key, entries in by_row.items():
        # A single policy on a row is not an overlap: an overlap needs at least two policies citing wording.
        distinct = {e["policy_id"]: e for e in entries}
        if len(distinct) < 2:
            continue
        policies = list(distinct.values())
        candidates.append({
            "row_key": row_key,
            "event": OVERLAP_ROWS[row_key],
            "row_label": ROW_LABELS.get(row_key, row_key),
            "policies": policies,
            "finding_key": f"overlap:{row_key}",
            "source_hash": _hash("overlap", row_key, *(f"{p['policy_id']}:{p['clause_id']}:{p['clause_text']}"
                                                       for p in sorted(policies, key=lambda x: x["policy_id"]))),
        })
    return candidates


# ---------- decisions ----------

def _decisions(fc_id, client_id):
    return {f.finding_key: f for f in GapFinding.query.filter_by(fc_id=fc_id, client_id=client_id)}


def _decorate(item, decision):
    """Attach the stored decision and a staleness flag to a generated finding."""
    item["status"] = "open"
    item["note"] = ""
    item["stale"] = False
    if decision is not None:
        item["status"] = decision.status
        item["note"] = decision.note or ""
        # A decision made against a different version of the source must be reviewed again before reuse.
        item["stale"] = bool(decision.status != "open" and decision.source_hash != item["source_hash"])
    item["confirmed_for_roa"] = item["status"] == "confirmed" and not item["stale"]
    return item


def build_analysis(fc, client_id):
    """The full analysis for the FC's view. Caller must already have confirmed the client shares something."""
    visible = sharing.visible_categories(fc, client_id)
    decisions = _decisions(fc.id, client_id)

    cover = _cover_summary(client_id, visible)
    cover_finding = None
    if cover["assessed"]:
        cover_finding = _decorate({
            "finding_key": COVER_KEY,
            "kind": "cover",
            "source_hash": cover["source_hash"],
        }, decisions.get(COVER_KEY))

    overlaps = []
    for candidate in _overlap_candidates(client_id, visible):
        overlaps.append(_decorate({**candidate, "kind": "overlap"}, decisions.get(candidate["finding_key"])))

    return {
        "visible": visible,
        "cover": cover,
        "cover_finding": cover_finding,
        "overlaps": overlaps,
        "has_overlaps": bool(overlaps),
    }


def record_decision(fc, client_id, finding_key, status, note):
    """Confirm or dismiss one finding, with a note, storing the source it was decided against.

    Returns True on success, False when the finding no longer exists in the current analysis (so a stale form
    cannot create a decision for something that is gone)."""
    from datetime import datetime, timezone

    if status not in ("confirmed", "dismissed", "open"):
        return False
    analysis = build_analysis(fc, client_id)
    source_hash = None
    if finding_key == COVER_KEY and analysis["cover_finding"] is not None:
        source_hash = analysis["cover_finding"]["source_hash"]
    else:
        match = next((o for o in analysis["overlaps"] if o["finding_key"] == finding_key), None)
        if match is not None:
            source_hash = match["source_hash"]
    if source_hash is None:
        return False
    kind = "cover" if finding_key == COVER_KEY else "overlap"
    row = GapFinding.query.filter_by(fc_id=fc.id, client_id=client_id, finding_key=finding_key).first()
    if row is None:
        row = GapFinding(fc_id=fc.id, client_id=client_id, finding_key=finding_key, kind=kind)
        db.session.add(row)
    row.status = status
    row.kind = kind
    row.note = (note or "").strip()[:MAX_NOTE] or None
    row.source_hash = source_hash
    row.decided_at = datetime.now(timezone.utc) if status != "open" else None
    db.session.commit()
    return True


def confirmed_for_roa(fc, client_id):
    """The confirmed, not-stale findings a ROA may reuse. Returns {"cover": {...} or None, "overlaps": [...]}."""
    analysis = build_analysis(fc, client_id)
    cover = analysis["cover_finding"]
    overlaps = [o for o in analysis["overlaps"] if o.get("confirmed_for_roa")]
    return {
        "cover": analysis["cover"] if (cover is not None and cover.get("confirmed_for_roa")) else None,
        "overlaps": overlaps,
    }
