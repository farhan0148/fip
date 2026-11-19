"""Questionnaire definition: eight single-select questions (design.md, screens 02)."""

from dataclasses import dataclass

OTHER_KEY = "other"
OTHER_LABEL = "Other, let me type"
OTHER_MAX_LENGTH = 200


@dataclass(frozen=True)
class Option:
    key: str
    label: str


@dataclass(frozen=True)
class Question:
    id: str
    number: int
    title: str
    why: str
    review_label: str  # caption used on the review screen
    summary_label: str  # label used in the dashboard profile summary
    options: tuple


def _q(number, title, why, review_label, summary_label, options):
    return Question(
        id=f"q{number}",
        number=number,
        title=title,
        why=why,
        review_label=review_label,
        summary_label=summary_label,
        options=tuple(Option(k, l) for k, l in options),
    )


QUESTIONS = (
    _q(1, "What is your age?",
       "Your age helps us tailor relevant protection needs.",
       "Age", "Age",
       [("under_30", "Under 30"), ("30", "30"), ("31_45", "31–45"), ("46_plus", "46 or above")]),
    _q(2, "What is your employment situation?",
       "This helps us understand the protection you may have through work.",
       "Employment", "Employment",
       [("full_time", "Full-time employee"), ("self_employed", "Self-employed"),
        ("part_time", "Part-time employee"), ("not_working", "Not currently working")]),
    _q(3, "Who depends on your income today?",
       "This helps us understand whose everyday needs your income protects.",
       "Dependants", "Depends on your income",
       [("spouse_child", "My spouse and one child"), ("parents_family", "My parents or other family"),
        ("partner_only", "My partner only"), ("no_one", "No one right now")]),
    _q(4, "What monthly budget feels comfortable for insurance?",
       "We use your budget to suggest protection that feels manageable.",
       "Monthly insurance budget", "Monthly budget",
       [("under_100", "Under S$100"), ("100_300", "S$100–300"),
        ("300_500", "S$300–500"), ("not_sure", "Not sure")]),
    _q(5, "What matters most for your protection?",
       "Your priorities help us focus the coverage conversation.",
       "Protection priority", "Top priority",
       [("family_income", "Family income protection"), ("hospital", "Hospital cover"),
        ("retirement", "Retirement savings"), ("not_sure", "Not sure yet")]),
    _q(6, "When would you like to retire?",
       "Your target gives us a horizon for your retirement needs.",
       "Retirement target", "Retirement target",
       [("before_60", "Before 60"), ("at_60", "At 60"), ("at_65", "At 65"), ("not_sure", "Not sure yet")]),
    _q(7, "What hospital ward would you prefer?",
       "Your preferred care setting helps us identify hospital cover gaps.",
       "Hospital preference", "Hospital preference",
       [("private", "Private hospital"), ("public_a", "Public hospital · A ward"),
        ("public_b1", "Public hospital · B1 ward"), ("no_preference", "No preference yet")]),
    _q(8, "Which existing cover do you have?",
       "Knowing your current protection helps us avoid unnecessary overlap.",
       "Existing cover", "Existing cover",
       [("personal_employer", "Personal life + employer plan"), ("personal_only", "Personal life plan only"),
        ("employer_only", "Employer plan only"), ("none", "No existing cover")]),
)

QUESTION_COUNT = len(QUESTIONS)
_BY_NUMBER = {q.number: q for q in QUESTIONS}
_BY_ID = {q.id: q for q in QUESTIONS}


def get_question(number):
    return _BY_NUMBER.get(number)


def get_question_by_id(question_id):
    return _BY_ID.get(question_id)


def option_label(question, key):
    for option in question.options:
        if option.key == key:
            return option.label
    return None


def validate_answer(question, form):
    """Return (selected_key, other_text, error). error is None when valid.

    Single-select: the posted `answer` must be one of the question's option keys
    or OTHER_KEY. "Other" needs non-empty text of at most OTHER_MAX_LENGTH chars.
    """
    key = (form.get("answer") or "").strip()
    if not key:
        return None, None, "Choose an answer, or select Skip."
    if key == OTHER_KEY:
        text = " ".join((form.get("other_text") or "").split())
        if not text:
            return None, None, "Type your answer, or choose another option."
        if len(text) > OTHER_MAX_LENGTH:
            return None, None, f"Keep your answer under {OTHER_MAX_LENGTH + 1} characters."
        return OTHER_KEY, text, None
    if option_label(question, key) is None:
        return None, None, "Choose one of the listed answers."
    return key, None, None
