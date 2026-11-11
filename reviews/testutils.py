"""Helpers that drive the study forms the way a browser does.

A result is only accepted with the one-time token the server rendered into the
form, so tests read it from the page instead of building it by hand.
"""

import re

from django.urls import reverse

_TOKEN_RE = re.compile(r'name="submission" value="([^"]+)"')


def rendered_submission(response):
    match = _TOKEN_RE.search(response.content.decode())
    assert match, "the page rendered no submission token"
    return match.group(1)


def check_typed_answer(client, answer=""):
    """Submit the answer form with the current position's rendered token."""
    url = reverse("reviews:study_current")
    front = client.get(url)
    return client.post(
        url,
        {"action": "check", "answer": answer, "submission": rendered_submission(front)},
    )


def submit_typed_result(client, *, result="auto", answer="", auto_correct="1"):
    """Check an answer, then submit the result from the rendered continue form."""
    url = reverse("reviews:study_current")
    page = check_typed_answer(client, answer)
    return client.post(
        url,
        {
            "action": "submit_result",
            "result": result,
            "answer": answer,
            "auto_correct": auto_correct,
            "submission": rendered_submission(page),
        },
    )


def post_quick_review(client, card, rating="good", **extra):
    """Open the answer side of a card, then submit its rating form."""
    url = reverse("reviews:review_card", kwargs={"card_id": card.pk})
    page = client.get(url, {"answer": "1"})
    return client.post(url, {"rating": rating, "submission": rendered_submission(page), **extra})
