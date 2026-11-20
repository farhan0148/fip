"""The plans the UI may show.

Today that is the fictional demo plans. The Plans pages call only this function, so the
catalogue can later come from real policy documents without touching those pages.
"""

from .models import Policy


def catalogue_policies():
    """Plans the UI may show, in a fixed order (oldest first)."""
    return Policy.query.filter_by(is_demo=True).order_by(Policy.id).all()
