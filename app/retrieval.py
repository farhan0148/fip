"""Deterministic retrieval of contract paragraphs for one plan (no AI, no randomness).

For a plan with ingested contract wording this picks the paragraphs most relevant to a fixed list of
topics by keyword scoring. Only paragraphs whose scope includes the plan's own slug are considered, so a
Standard plan can never be handed Part One wording and the reverse. The same plan and the same stored
paragraphs always give the same result in the same order.
"""

import hashlib
import json
import re
from dataclasses import dataclass

from .models import Document, DocumentVersion, Paragraph, PlanDocument

CAP = 40               # most paragraphs returned for a plan
MIN_LENGTH = 40        # shorter paragraphs are headings, page furniture or table cells
MIN_SCORE = 2          # a paragraph must score at least this to be picked for a topic

# key, label, [(keyword, weight)], paragraphs taken per topic before the overall cap
TOPICS = (
    ("covered", "What is covered",
     [("we will pay", 2), ("we pay", 2), ("we reimburse", 2), ("reimburse", 2), ("benefit", 1), ("covered", 1)], 4),
    ("ward", "Ward class and hospitals",
     [("ward", 2), ("private hospital", 2), ("restructured", 2), ("subsidised", 1), ("class", 1)], 3),
    ("limits", "Annual and lifetime limits",
     [("claim limit", 3), ("limit per policy year", 3), ("per policy year", 2), ("lifetime", 3), ("annual", 2),
      ("limit", 1)], 4),
    ("deductible", "Deductible", [("deductible", 3)], 3),
    ("coinsurance", "Co-insurance", [("co-insurance", 3), ("co-pay", 2)], 3),
    ("hospitalisation", "Pre and post hospitalisation",
     [("pre-hospitalisation", 3), ("post-hospitalisation", 3), ("pre- and post", 3), ("pre-hospital", 2),
      ("post-hospital", 2), ("after discharge", 1), ("before admission", 1)], 3),
    ("cancer", "Cancer drug and treatment",
     [("cancer drug", 3), ("cancer treatment", 3), ("cancer", 1), ("chemotherapy", 1)], 3),
    ("exclusions", "Exclusions",
     [("not covered", 3), ("we do not pay", 3), ("we will not pay", 3), ("exclusion", 3), ("pre-existing", 2),
      ("do not cover", 2)], 4),
    ("waiting", "Waiting periods", [("waiting period", 3), ("wait out", 2), ("waiting", 2)], 2),
    ("claims", "Claims",
     [("make a claim", 3), ("submit", 2), ("claim form", 3), ("notice of claim", 3), ("claims", 1), ("claim", 1)], 3),
    ("renewal", "Premiums and renewal",
     [("renew", 3), ("premium", 1), ("guarantee", 1), ("premium payment", 2), ("age next birthday", 1)], 4),
    ("termination", "Termination", [("terminat", 3), ("free-look", 2), ("cancel", 1), ("cease", 1)], 3),
    ("riders", "Riders", [("rider", 3), ("pruextra", 3), ("supplementary", 3)], 2),
)
TOPIC_LABELS = {key: label for key, label, _, _ in TOPICS}

_PAGE_FURNITURE = re.compile(r"\bpage \d+ of \d+\b", re.IGNORECASE)


@dataclass(frozen=True)
class Retrieved:
    paragraphs: list    # Paragraph rows, in document order (page, then position)
    by_topic: dict      # topic key -> [paragraph ids], best first
    digest: str         # changes when any retrieved paragraph (id or wording) changes


def paragraphs_in_scope(policy):
    """Every stored paragraph of the plan's own contract documents whose scope includes the plan."""
    if not policy.slug:
        return []
    rows = (
        Paragraph.query.join(DocumentVersion, DocumentVersion.id == Paragraph.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(PlanDocument, PlanDocument.document_id == Document.id)
        .filter(PlanDocument.policy_id == policy.id)
        .order_by(Paragraph.id)
        .all()
    )
    return [p for p in rows if policy.slug in (p.scope or [])]


def _eligible(paragraph):
    text = paragraph.text
    if len(text) < MIN_LENGTH:
        return False
    if _PAGE_FURNITURE.search(text) and len(text) < 120:
        return False
    if paragraph.clause_number and paragraph.heading and text == f"{paragraph.clause_number} {paragraph.heading}":
        return False  # just the clause heading line; its body carries the heading
    return True


def score(paragraph, keywords):
    """Each keyword found in the heading or text counts its weight once; a keyword in the heading counts twice."""
    heading = (paragraph.heading or "").lower()
    haystack = heading + " " + paragraph.text.lower()
    return sum(weight * (2 if word in heading else 1) for word, weight in keywords if word in haystack)


def retrieve(policy, cap=CAP):
    """The plan's paragraphs for the fixed topics, capped at `cap`, same result every time."""
    candidates = [p for p in paragraphs_in_scope(policy) if _eligible(p)]
    ranked = {}
    for key, _label, keywords, take in TOPICS:
        scored = [(score(p, keywords), p) for p in candidates]
        scored = [(s, p) for s, p in scored if s >= MIN_SCORE]
        scored.sort(key=lambda item: (-item[0], item[1].page, item[1].ordinal, item[1].id))
        ranked[key] = [p for _, p in scored[:take]]

    # Round-robin: every topic gets its best paragraph before any gets a second one.
    chosen, seen = [], set()
    depth = max((len(v) for v in ranked.values()), default=0)
    for level in range(depth):
        for key, _label, _keywords, _take in TOPICS:
            if level < len(ranked[key]) and len(chosen) < cap:
                paragraph = ranked[key][level]
                if paragraph.id not in seen:
                    seen.add(paragraph.id)
                    chosen.append(paragraph)
    chosen.sort(key=lambda p: (p.page, p.ordinal, p.id))
    by_topic = {key: [p.id for p in ranked[key] if p.id in seen] for key in ranked}
    return Retrieved(chosen, by_topic, paragraphs_hash(chosen))


def paragraphs_hash(paragraphs):
    payload = [[p.id, p.paragraph_sha256, sorted(p.scope or [])] for p in paragraphs]
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()


def first_sentence(text, limit=240):
    """The first sentence of a paragraph, trimmed to `limit` characters."""
    match = re.search(r"(?<=[a-z0-9\)])[.!?](?=\s|$)", text)
    sentence = text[: match.end()] if match else text
    if len(sentence) > limit:
        sentence = sentence[: limit - 3].rstrip() + "..."
    return sentence
