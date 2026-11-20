"""Plan matching: top 3 on the dashboard, the budget trade-off and the compatibility rows.

Everything here is a fixed rule. No AI is involved and nothing is guessed: a skipped,
"Not sure" or typed-in ("Other") answer is unknown and never counts as a match.
These are discussion aids, not advice to buy.

Rules
-----
* Plan type -> the Review-priority topic it speaks to (PLAN_TOPIC below).
* Budget (Q4) -> a monthly band. A plan "fits" when its premium is within the band's top
  ("Under S$100" is exclusive of S$100; the other bands include their top).
* Top 3: plans whose type matches one of the client's Review priorities come first (earlier
  priority first), then plans that fit the budget, then catalogue order (fixed). A plan counts
  as matched if its type matches OR it fits the budget. If nothing matches, the first three
  plans are shown without a match tag.
"""

from dataclasses import dataclass

from . import services
from .models import Scenario

# Review-priority topic (app/priorities.py) each plan type speaks to.
PLAN_TOPIC = {
    "Term life": "dependants",
    "Whole life": "dependants",
    "Hospitalisation": "hospital",
    "Critical illness": "critical_illness",
    "Retirement": "retirement",
    "Disability": "income_loss",
}

# Q4 answer key -> (label, top of the band in dollars, is the top included?)
BUDGET_BANDS = {
    "under_100": ("Under S$100 a month", 100, False),
    "100_300": ("S$100–300 a month", 300, True),
    "300_500": ("S$300–500 a month", 500, True),
}

GOOD, PARTIAL, WEAK, UNKNOWN = "good", "partial", "weak", "unknown"
STATUS_LABELS = {GOOD: "Good fit", PARTIAL: "Partly fits", WEAK: "Weak fit", UNKNOWN: "Not enough information"}
STATUS_CHIPS = {GOOD: "chip-success", PARTIAL: "chip-warning", WEAK: "chip-muted", UNKNOWN: "chip-info"}

NOT_ENOUGH = "Not enough information"


def money(dollars):
    return f"S${dollars:,}"


def cents_text(cents):
    dollars, rest = divmod(cents, 100)
    return money(dollars) if rest == 0 else f"{money(dollars)}.{rest:02d}"


# ---------- reading the client's answers ----------

@dataclass(frozen=True)
class Budget:
    label: str
    high_cents: int
    inclusive: bool

    def fits(self, premium_cents):
        return premium_cents <= self.high_cents if self.inclusive else premium_cents < self.high_cents

    def left_cents(self, premium_cents):
        """Cents left under the top of the band (negative means over)."""
        return self.high_cents - premium_cents


def budget_for(answers):
    """The client's budget band from Q4, or None when unknown (skipped, Not sure or typed in)."""
    row = answers.get("q4")
    if row is None or row.status != "answered" or not row.selected:
        return None
    band = BUDGET_BANDS.get(row.selected[0])
    return Budget(band[0], band[1] * 100, band[2]) if band else None


def known_answer(answers, question_id):
    """(option key, label) for a real option, or None if skipped, missing or typed in."""
    row = answers.get(question_id)
    if row is None or row.status != "answered" or not row.selected or row.selected[0] == "other":
        return None
    question = next(q for q in services.QUESTIONS if q.id == question_id)
    return row.selected[0], services.option_label(question, row.selected[0])


# ---------- top 3 ----------

def rank_plans(policies, priorities, budget):
    """Rank policies. priorities: ordered [(topic key, title)]. Returns a list of dicts, best first.

    Each dict: policy, type_match (title or None), fits_budget, matched, reason.
    """
    topics = [key for key, _ in priorities]
    titles = dict(priorities)
    ranked = []
    for order, policy in enumerate(policies):
        topic = PLAN_TOPIC.get(policy.type)
        position = topics.index(topic) if topic in topics else None
        fits = (
            budget is not None
            and policy.monthly_premium_cents is not None  # an unknown premium never fits
            and budget.fits(policy.monthly_premium_cents)
        )
        reasons = []
        if position is not None:
            reasons.append(f"Matches your priority: {titles[topic]}")
        if fits:
            reasons.append("Fits your stated budget")
        ranked.append({
            "policy": policy,
            "position": position,
            "fits_budget": fits,
            "matched": position is not None or fits,
            "reason": ". ".join(reasons) + ("." if reasons else ""),
            "_key": (position is None, position if position is not None else 99, not fits, order),
        })
    ranked.sort(key=lambda item: item["_key"])
    return ranked


def top_plans(user, policies, limit=3):
    """Top plans for the dashboard.

    Returns {"plans": [{policy, best, reason}], "any_match": bool}. "best" is True only for #1 and only
    when something actually matched.
    """
    answers = services.answers_by_question(user)
    priorities = [(p.topic_key, p.title) for p in services.priorities_for(user)]
    ranked = rank_plans(policies, priorities, budget_for(answers))
    any_match = any(item["matched"] for item in ranked)
    if not any_match:
        chosen = [{"policy": p, "best": False, "reason": ""} for p in policies[:limit]]
        return {"plans": chosen, "any_match": False}
    chosen = []
    for index, item in enumerate(ranked[:limit]):
        chosen.append({
            "policy": item["policy"],
            "best": index == 0 and item["matched"],
            "reason": item["reason"] if item["matched"] else "",
        })
    return {"plans": chosen, "any_match": True}


# ---------- budget trade-off ----------

def budget_tradeoff(premium_cents, budget):
    """Plain facts about one plan against the budget band. Never guesses an unknown budget."""
    if budget is None:
        return {
            "known": False,
            "headline": "Your monthly budget is unknown.",
            "note": "You skipped the budget question, chose Not sure, or typed your own answer, so we cannot compare "
                    "this plan's premium with a budget.",
        }
    if premium_cents is None:
        return {
            "known": False,
            "headline": "This plan's premium varies.",
            "note": "The catalogue has no premium for this plan, so it cannot be compared with your budget.",
        }
    left = budget.left_cents(premium_cents)
    fits = budget.fits(premium_cents)
    if fits:
        headline = f"Within your budget: {cents_text(premium_cents)} a month. Your band is {budget.label}."
        detail = f"Up to {cents_text(left)} a month of your band is left." if left > 0 else "None of your band is left."
    else:
        headline = f"Above your budget: {cents_text(premium_cents)} a month. Your band is {budget.label}."
        detail = f"It is {cents_text(-left)} a month over the top of your band."
    return {
        "known": True,
        "fits": fits,
        "headline": headline,
        "detail": detail,
        "band": budget.label,
        "premium": cents_text(premium_cents),
        "left": cents_text(max(left, 0)),
        "note": "This is one plan on its own. Any other premiums you pay use up the same band.",
        "premium_cents": premium_cents,
        "high_cents": budget.high_cents,
    }


# ---------- compatibility rows ----------

DEPENDANT_ANSWERS = {"spouse_child", "parents_family", "partner_only"}


def _row(title, status, driver, text):
    return {"title": title, "status": status, "label": STATUS_LABELS[status], "chip": STATUS_CHIPS[status],
            "driver": driver, "text": text}


def family_row(dependants, death_cover_dollars):
    if dependants is None:
        return _row("Family obligations", UNKNOWN, None, NOT_ENOUGH + ": you skipped or typed your own answer.")
    key, label = dependants
    driver = f"You said: {label}"
    if key in DEPENDANT_ANSWERS:
        if death_cover_dollars > 0:
            return _row("Family obligations", GOOD, driver,
                        f"This plan pays a death benefit of {money(death_cover_dollars)}, which speaks to people who rely on your income.")
        return _row("Family obligations", WEAK, driver, "This plan has no death benefit, so it does not protect them if you die.")
    return _row("Family obligations", PARTIAL, driver, "You said no one relies on your income today, so death cover matters less right now.")


def budget_row(budget, premium_cents, answered):
    if budget is None:
        return _row("Budget fit", UNKNOWN, None, NOT_ENOUGH + ": your budget is skipped, Not sure or typed in.")
    driver = f"Your budget: {budget.label}"
    if premium_cents is None:
        return _row("Budget fit", UNKNOWN, driver, NOT_ENOUGH + ": this plan's premium varies.")
    if budget.fits(premium_cents):
        return _row("Budget fit", GOOD, driver, f"{cents_text(premium_cents)} a month is within your band.")
    return _row("Budget fit", WEAK, driver, f"{cents_text(premium_cents)} a month is over the top of your band.")


def hospital_row(ward, hospital_state):
    """ward: (key, label) or None. hospital_state: the plan's comparison cell state for hospital bills, or None."""
    if ward is None:
        return _row("Hospital preference", UNKNOWN, None, NOT_ENOUGH + ": your hospital preference is skipped or typed in.")
    if hospital_state is None:
        return _row("Hospital preference", UNKNOWN, f"You said: {ward[1]}", NOT_ENOUGH + ": this plan's wording does not say.")
    key, label = ward
    driver = f"You said: {label}"
    pays = hospital_state in ("included", "optional_rider")
    if key == "no_preference":
        return _row("Hospital preference", PARTIAL, driver, "You have no ward preference yet, so hospital cover is optional for now.")
    if pays:
        return _row("Hospital preference", GOOD, driver, "This plan pays hospital bills. Check its ward and limits against your preference.")
    return _row("Hospital preference", WEAK, driver, "This plan does not pay hospital bills.")


def overlap_row(saved_existing_cover, existing_answer, death_cover_dollars):
    """saved_existing_cover: dollars typed into the saved simulator scenario, or None. existing_answer: Q8 (key, label) or None."""
    if saved_existing_cover is not None:
        driver = f"In the simulator you typed existing death cover of {money(saved_existing_cover)}"
        if death_cover_dollars == 0:
            return _row("Overlap with cover you have", GOOD, driver, "This plan has no death benefit, so it does not overlap with that cover.")
        if saved_existing_cover == 0:
            return _row("Overlap with cover you have", GOOD, driver, f"This adds new death cover of {money(death_cover_dollars)}.")
        return _row("Overlap with cover you have", PARTIAL, driver,
                    f"This plan adds {money(death_cover_dollars)} on top of it. Check your gap in the simulator first.")
    if existing_answer is None:
        return _row("Overlap with cover you have", UNKNOWN, None,
                    NOT_ENOUGH + ": you have not saved a simulator scenario and skipped or typed your own cover answer.")
    key, label = existing_answer
    driver = f"You said: {label}"
    if key == "none":
        return _row("Overlap with cover you have", GOOD, driver, "You have no existing cover, so nothing overlaps.")
    return _row("Overlap with cover you have", PARTIAL, driver, "You already have some cover. Check what it pays before adding more.")


def compatibility_rows(user, policy, hospital_state):
    """Four rows for one plan, each with the answer that drove it."""
    answers = services.answers_by_question(user)
    scenario = Scenario.query.filter_by(user_id=user.id).first()
    saved_cover = scenario.inputs["fields"].get("existing_cover") if scenario is not None else None
    death_cover = (policy.death_cover_cents or 0) // 100
    return [
        family_row(known_answer(answers, "q3"), death_cover),
        budget_row(budget_for(answers), policy.monthly_premium_cents, answers.get("q4")),
        hospital_row(known_answer(answers, "q7"), hospital_state),
        overlap_row(saved_cover, known_answer(answers, "q8"), death_cover),
    ]
