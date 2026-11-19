"""Review priorities: which topics are worth discussing with a Financial Consultant (FC).

These weights only rank discussion topics. They are NOT a coverage score, a
coverage percentage or a recommendation to buy anything. The overall score,
radar and percentages stay pending until a calculation is agreed (tasks.md).

How it works
------------
1. Each rule below says: "if question Q was answered with option O, add W points
   to topic T, because REASON".
2. Skipped questions, unanswered questions and free-text "Other" answers are
   treated as unknown and trigger no rule.
3. A topic's score is the sum of the weights of the rules that fired. Topics
   with a score of 0 are not shown.
4. Topics are ranked by score, highest first. Ties are broken by the fixed order
   of TOPICS, so the ranking is stable. At most MAX_PRIORITIES are returned.

Budget (Q4) has no rule: it limits what is affordable, not what is relevant.
The age option "30" has no rule: it is a single value, not a band.
"""

from dataclasses import dataclass

MAX_PRIORITIES = 3

# Fixed order. It is also the tie-break order.
TOPICS = (
    ("dependants", "Protecting the people who rely on your income"),
    ("hospital", "Hospital and medical cover"),
    ("retirement", "Retirement planning"),
    ("critical_illness", "Critical illness cover"),
    ("income_loss", "Income if you cannot work"),
    ("existing_cover", "Checking your existing cover for gaps or overlap"),
)
_TOPIC_TITLES = dict(TOPICS)
_TOPIC_ORDER = {key: index for index, (key, _) in enumerate(TOPICS)}


@dataclass(frozen=True)
class Rule:
    question_id: str
    option: str
    topic: str
    weight: int
    reason: str  # shown to the user, starts with the effect of their answer


RULES = (
    # Q1 age
    Rule("q1", "31_45", "critical_illness", 1, "Critical illness risk generally rises with age."),
    Rule("q1", "46_plus", "critical_illness", 2, "Critical illness risk generally rises with age."),
    Rule("q1", "46_plus", "retirement", 1, "Retirement is closer, so there is less time to adjust."),
    # Q2 employment
    Rule("q2", "self_employed", "income_loss", 2, "Self-employed people often have no employer-provided income protection."),
    Rule("q2", "self_employed", "hospital", 1, "Self-employed people often have no employer medical benefits."),
    Rule("q2", "part_time", "hospital", 1, "Part-time work may come with fewer employer benefits."),
    Rule("q2", "part_time", "income_loss", 1, "Part-time work may come with fewer employer benefits."),
    Rule("q2", "not_working", "income_loss", 1, "Without a regular income, cover for lost income needs a different approach."),
    # Q3 dependants
    Rule("q3", "spouse_child", "dependants", 3, "Your spouse and child rely on your income."),
    Rule("q3", "parents_family", "dependants", 3, "Your parents or other family rely on your income."),
    Rule("q3", "partner_only", "dependants", 2, "Your partner relies on your income."),
    # Q5 what matters most
    Rule("q5", "family_income", "dependants", 3, "You said family income protection matters most."),
    Rule("q5", "hospital", "hospital", 3, "You said hospital cover matters most."),
    Rule("q5", "retirement", "retirement", 3, "You said retirement savings matter most."),
    # Q6 retirement
    Rule("q6", "before_60", "retirement", 2, "Retiring before 60 leaves a longer period to fund."),
    Rule("q6", "at_60", "retirement", 1, "Retiring at 60 still needs a plan for the years after work."),
    # Q7 hospital preference
    Rule("q7", "private", "hospital", 2, "Private hospital care usually costs more than public wards."),
    Rule("q7", "public_a", "hospital", 1, "A-ward care usually costs more than B1 or C wards."),
    Rule("q7", "public_b1", "hospital", 1, "Your ward choice affects which hospital cover fits."),
    # Q8 existing cover
    Rule("q8", "none", "dependants", 2, "With no existing cover, nothing protects your dependants' income today."),
    Rule("q8", "none", "hospital", 2, "With no existing cover, hospital bills would be paid out of pocket."),
    Rule("q8", "none", "critical_illness", 2, "With no existing cover, a critical illness would not be covered."),
    Rule("q8", "employer_only", "dependants", 1, "Employer cover usually ends when you leave the job."),
    Rule("q8", "employer_only", "critical_illness", 1, "Employer plans often exclude critical illness."),
    Rule("q8", "personal_only", "hospital", 1, "A personal life plan usually does not pay hospital bills."),
    Rule("q8", "personal_employer", "existing_cover", 2, "Two sets of cover can overlap or leave gaps."),
)


@dataclass(frozen=True)
class Priority:
    topic_key: str
    title: str
    score: int
    triggers: tuple  # tuple of (question caption, answer label, reason)


def compute_priorities(answers):
    """answers: dict question_id -> (selected_key, answer_label, question_caption).

    Only answered, non-"Other" questions should be passed in; anything else is
    ignored here too. Returns a list of Priority, best first, at most three.
    """
    scores = {}
    triggers = {}
    for rule in RULES:
        entry = answers.get(rule.question_id)
        if entry is None:
            continue
        key, label, caption = entry
        if key != rule.option:
            continue
        scores[rule.topic] = scores.get(rule.topic, 0) + rule.weight
        triggers.setdefault(rule.topic, []).append((caption, label, rule.reason))

    ranked = sorted(scores, key=lambda topic: (-scores[topic], _TOPIC_ORDER[topic]))
    return [
        Priority(topic, _TOPIC_TITLES[topic], scores[topic], tuple(triggers[topic]))
        for topic in ranked[:MAX_PRIORITIES]
    ]
