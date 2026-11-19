"""Onboarding state and derived values. No Flask request handling here."""

from .extensions import db
from .models import QuestionnaireAnswer
from .priorities import compute_priorities
from .questions import OTHER_KEY, QUESTION_COUNT, QUESTIONS, get_question, option_label


def answers_by_question(user):
    return {a.question_id: a for a in user.answers}


def next_unrecorded_number(user):
    """First question with no row (neither answered nor skipped).

    Returns QUESTION_COUNT + 1 when every question has been answered or skipped.
    """
    recorded = answers_by_question(user)
    for question in QUESTIONS:
        if question.id not in recorded:
            return question.number
    return QUESTION_COUNT + 1


def review_is_ready(user):
    return next_unrecorded_number(user) > QUESTION_COUNT


def save_answer(user, question, key, other_text):
    _upsert(user, question, "answered", [key], other_text if key == OTHER_KEY else None)


def save_skip(user, question):
    _upsert(user, question, "skipped", [], None)


def _upsert(user, question, status, selected, other_text):
    row = QuestionnaireAnswer.query.filter_by(user_id=user.id, question_id=question.id).first()
    if row is None:
        row = QuestionnaireAnswer(user_id=user.id, question_id=question.id)
        db.session.add(row)
    row.status = status
    row.selected = selected
    row.other_text = other_text
    db.session.commit()


def display_value(question, row):
    """Text shown for an answer, or None when not answered."""
    if row is None or row.status != "answered" or not row.selected:
        return None
    key = row.selected[0]
    if key == OTHER_KEY:
        return row.other_text
    return option_label(question, key)


def review_rows(user):
    rows = answers_by_question(user)
    result = []
    for question in QUESTIONS:
        result.append({"question": question, "value": display_value(question, rows.get(question.id))})
    return result


def profile_summary(user):
    """Short 'Label: value' parts built only from answered questions."""
    parts = []
    for row in review_rows(user):
        if row["value"] is not None:
            parts.append((row["question"].summary_label, row["value"]))
    return parts


def priorities_for(user):
    """Rank discussion topics. Skipped, missing and free-text answers trigger nothing."""
    rows = answers_by_question(user)
    usable = {}
    for question in QUESTIONS:
        row = rows.get(question.id)
        if row is None or row.status != "answered" or not row.selected:
            continue
        key = row.selected[0]
        if key == OTHER_KEY:
            continue
        usable[question.id] = (key, option_label(question, key), question.review_label)
    return compute_priorities(usable)


def question_by_number(number):
    return get_question(number)
