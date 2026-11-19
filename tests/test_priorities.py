from app.priorities import MAX_PRIORITIES, TOPICS, compute_priorities


def ans(**pairs):
    """ans(q3="spouse_child") -> input shaped like services.priorities_for builds."""
    return {qid: (key, key.replace("_", " "), qid.upper()) for qid, key in pairs.items()}


def topics(result):
    return [p.topic_key for p in result]


def test_no_answers_gives_no_priorities():
    assert compute_priorities({}) == []


def test_dependants_answer_raises_dependants_topic():
    result = compute_priorities(ans(q3="spouse_child"))
    assert topics(result) == ["dependants"]
    assert result[0].triggers[0][0] == "Q3"


def test_answer_that_means_no_dependants_triggers_nothing():
    assert compute_priorities(ans(q3="no_one")) == []


def test_scores_add_up_across_questions():
    result = compute_priorities(ans(q3="spouse_child", q5="family_income"))
    assert result[0].topic_key == "dependants"
    assert result[0].score == 6
    assert len(result[0].triggers) == 2


def test_budget_and_single_value_age_have_no_effect():
    assert compute_priorities(ans(q1="30", q4="under_100")) == []


def test_at_most_three_topics():
    result = compute_priorities(
        ans(q1="46_plus", q2="self_employed", q3="spouse_child", q5="hospital", q6="before_60", q7="private", q8="none")
    )
    assert len(result) == MAX_PRIORITIES


def test_ties_use_fixed_topic_order():
    # q5 hospital (3) and q5 retirement cannot both be chosen, so build a tie from two questions.
    # q8 "none" gives dependants 2, hospital 2, critical_illness 2: a three-way tie.
    result = compute_priorities(ans(q8="none"))
    order = [key for key, _ in TOPICS]
    assert topics(result) == sorted(topics(result), key=order.index)
    assert topics(result) == ["dependants", "hospital", "critical_illness"]
    # Same input, same output, every time.
    assert compute_priorities(ans(q8="none")) == result
