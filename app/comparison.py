"""Side-by-side comparison of two plans (requirements C6, R3).

Rules
-----
* Each cell has exactly one of four states. A cell that is missing from the
  database means the documents do not mention that row. It is shown as
  "Unconfirmed" with "Not stated in this policy", never as "Not included".
* "Not included" needs a cited clause (also enforced by a database constraint).
* A row is "shared" when every compared plan has a finding for it. A row is
  "distinctive" (auto-detected) when only some plans have one. A row that no
  compared plan mentions is not shown.
"""

from dataclasses import dataclass

from .models import ComparisonCell, ComparisonFlag, Policy

DEMO_ROWS = (
    ("duration", "Coverage duration"),
    ("premium", "Premium"),
    ("death_cover", "Death and disability payout"),
    ("cash_value", "Cash value"),
    ("hospital", "Hospital bills"),
    ("critical_illness", "Critical illness"),
    ("premium_waiver", "Premium waiver"),
)

# Fixed rows for the real PRUShield plans. Premium is not a row of findings: it always says "varies".
# key, label, what the row asks (also shown to the model that proposes the findings)
REAL_ROW_DETAILS = (
    ("ward", "Ward class covered", "Which hospital and ward classes the plan pays for."),
    ("annual_limit", "Annual claim limit", "The most the plan pays in a policy year, or in a lifetime, as the wording states it."),
    ("deductible", "Deductible", "The part of a claim the policyholder pays first."),
    ("coinsurance", "Co-insurance", "The share of the bill the policyholder pays after the deductible."),
    ("hospitalisation", "Pre and post hospitalisation", "Treatment paid for before admission and after discharge."),
    ("cancer", "Cancer drug and treatment cover", "What the plan pays for cancer drugs and cancer treatment."),
    ("exclusions", "Key exclusions", "Main things the plan does not pay for. Use state included when the wording lists exclusions, and name them in the finding."),
    ("renewal", "Renewal terms", "Whether and how the policy renews, and what happens to premiums at renewal."),
)
REAL_ROWS = tuple((key, label) for key, label, _ in REAL_ROW_DETAILS)

# Every row label in one place (the consent screens look labels up here).
ROWS = DEMO_ROWS + REAL_ROWS

STATE_LABELS = {
    "included": "Included (subject to conditions)",
    "optional_rider": "Optional rider",
    "unconfirmed": "Unconfirmed",
    "not_included": "Not included",
}
STATE_ORDER = ("included", "optional_rider", "unconfirmed", "not_included")

NOT_STATED = "Not stated in this policy."
NOT_STATED_REAL = "Not stated in the available wording"


@dataclass
class Cell:
    policy: Policy
    row_key: str
    state: str
    finding: str
    cell: ComparisonCell | None  # None when the documents do not mention the row
    flagged: bool = False

    @property
    def state_label(self):
        return STATE_LABELS[self.state]

    @property
    def clause(self):
        return self.cell.clause if self.cell is not None else None

    @property
    def paragraph(self):
        return self.cell.paragraph if self.cell is not None else None

    @property
    def can_flag(self):
        return self.state == "unconfirmed"


@dataclass
class Row:
    key: str
    label: str
    cells: list


def demo_policies():
    return Policy.query.filter_by(is_demo=True).order_by(Policy.id).all()


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def choose_pair(policies, a_raw, b_raw):
    """Return (policy_a, policy_b, notice). Needs at least two policies.

    Invalid ids fall back to the first available plans. Picking the same plan
    twice swaps the second column for another plan and says so.
    """
    by_id = {p.id: p for p in policies}
    a = by_id.get(_to_int(a_raw))
    b = by_id.get(_to_int(b_raw))
    notice = None
    if a is not None and b is not None and a.id == b.id:
        notice = "Pick two different plans. The second column was changed."
        b = None
    if a is None:
        a = next(p for p in policies if b is None or p.id != b.id)
    if b is None or b.id == a.id:
        b = next(p for p in policies if p.id != a.id)
    return a, b, notice


def build_rows(user, policies):
    ids = [p.id for p in policies]
    stored = {
        (c.policy_id, c.row_key): c
        for c in ComparisonCell.query.filter(ComparisonCell.policy_id.in_(ids)).all()
    }
    flagged = {
        (f.policy_id, f.row_key)
        for f in ComparisonFlag.query.filter(
            ComparisonFlag.user_id == user.id, ComparisonFlag.policy_id.in_(ids)
        )
    }

    shared, distinctive = [], []
    for key, label in ROWS:
        present = [(p.id, key) in stored for p in policies]
        if not any(present):
            continue
        cells = []
        for policy in policies:
            found = stored.get((policy.id, key))
            if found is None:
                cells.append(Cell(policy, key, "unconfirmed", NOT_STATED, None, (policy.id, key) in flagged))
            else:
                cells.append(Cell(policy, key, found.state, found.finding, found, (policy.id, key) in flagged))
        (shared if all(present) else distinctive).append(Row(key, label, cells))
    return shared, distinctive


def flag_count(user, policies):
    ids = [p.id for p in policies]
    return ComparisonFlag.query.filter(
        ComparisonFlag.user_id == user.id, ComparisonFlag.policy_id.in_(ids)
    ).count()
