"""One engine behind the vocabulary drills.

A drill is a :class:`DrillSpec`: where its items come from, how a round is
built, who may run it, how an answer is checked, and which templates render
it.  ``start_round``, ``step_round`` and ``complete_round`` run one request
of the three views in ``reviews/drill_views.py`` for a spec resolved from the
drill registry.

The session payload is ``{queue, index, seen, correct, last}`` plus whatever
extra keys a spec declares.
"""

import random
from dataclasses import dataclass, field

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.language_modes import get_language_mode
from reviews.analytics import record_trainer_attempt
from reviews.daily_plan import available_drills, drill_of_the_day
from reviews.models import DrillItemProgress, TrainerAttempt

RETIRE_STREAK = 3


@dataclass(frozen=True)
class DrillItem:
    """One question in a drill.

    ``key`` is stable and unique within the drill (the article drill uses the
    card pk as a string).  ``prompt`` is merged into the step template's
    context, ``answers`` carries whatever the spec's ``check`` needs, and
    ``label`` is the analytics label and the name shown on the retire button.
    """

    key: str
    label: str = ""
    prompt: dict = field(default_factory=dict)
    answers: object = None
    example: str = ""


@dataclass(frozen=True)
class DrillResult:
    is_correct: bool
    feedback: dict
    had_typo: bool = False


class DrillSpec:
    """Declarative description of one drill.

    Subclasses set the identifiers below and override the hooks they need;
    everything else is shared by the three generic views.
    """

    slug = ""
    trainer = ""
    label = ""
    session_key = ""
    step_template = ""
    complete_template = ""
    retry_label = "Start another round"
    round_size = None
    empty_redirect = "core:dashboard"

    # -- urls ------------------------------------------------------------------

    @property
    def start_url(self):
        return reverse("reviews:drill_start", args=[self.slug])

    @property
    def step_url(self):
        return reverse("reviews:drill", args=[self.slug])

    @property
    def complete_url(self):
        return reverse("reviews:drill_complete", args=[self.slug])

    # -- session storage ---------------------------------------------------

    def load(self, request):
        session = request.session.get(self.session_key)
        return session if isinstance(session, dict) else None

    def save(self, request, session):
        request.session[self.session_key] = session

    def clear(self, request):
        request.session.pop(self.session_key, None)

    # -- round context -----------------------------------------------------

    def context(self, request, session=None, **kwargs):
        """Per-request state every hook receives (spec.pool's user/mode)."""
        return {
            "user": request.user,
            "language_mode": get_language_mode(request),
        }

    # -- items -------------------------------------------------------------

    def items(self, context):
        """Every item the drill knows about, before retirement is applied."""
        raise NotImplementedError

    def pool(self, context):
        """All items the user may be asked, retired ones already removed."""
        retired = retired_item_keys(context["user"], self.trainer)
        return [
            item for item in self.items(context) if self.progress_key(item, context) not in retired
        ]

    def progress_key(self, item, context):
        """The DrillItemProgress.item_key for an item."""
        return item.key

    def item_for_progress_key(self, item_key, context):
        """Resolve a progress key back to its item, or None when it is stale."""
        return self.item_for(item_key, context)

    def owns_progress_key(self, item_key, context):
        """Whether a pending progress key belongs to what is on screen now."""
        return isinstance(item_key, str) and bool(item_key)

    def pending_extra(self, item, context):
        """Extra keys the drill's templates read off a pending action."""
        return {}

    def item_for(self, queue_value, context):
        """Resolve one session queue entry, or None when it is stale."""
        raise NotImplementedError

    def queue_value(self, item):
        """What the session queue stores for an item."""
        return item.key

    def start_session(self, session, items, context):
        """Add drill-specific keys to a freshly started round."""

    def prepare(self, session, items, context):
        """Per-item state generated once and kept for the whole round."""

    def normalize(self, session, context):
        """Validate (and where possible repair) a session dict.

        Returning False clears the round and sends the user back to start,
        which is what an in-flight session from an older release gets.
        """
        if not isinstance(session.get("queue"), list):
            return False
        for name in ("index", "seen", "correct"):
            value = session.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return False
        return session["index"] <= len(session["queue"])

    def empty_message(self, context):
        return "There is nothing to drill yet."

    # -- answering ---------------------------------------------------------

    def check(self, item, post, session, context):
        raise NotImplementedError

    def card_for(self, item):
        """The Card to attach to the analytics attempt (article drill only)."""
        return None

    # -- rendering ---------------------------------------------------------

    def step_context(self, item, session, context):
        return {}

    def complete_context(self, session, context):
        return {
            "eyebrow": self.label,
            "retry_label": self.retry_label,
            "step_url": self.step_url,
            "retry_url": self.start_url,
        }


def retired_item_keys(user, trainer):
    return set(
        DrillItemProgress.objects.filter(
            owner=user,
            trainer=trainer,
            retired_at__isnull=False,
        ).values_list("item_key", flat=True)
    )


def _record_item_result(user, trainer, item_key, is_correct):
    progress, _ = DrillItemProgress.objects.get_or_create(
        owner=user,
        trainer=trainer,
        item_key=item_key,
    )
    previous_streak = progress.correct_streak
    progress.correct_streak = previous_streak + 1 if is_correct else 0
    progress.save(update_fields=["correct_streak", "updated_at"])
    return progress, previous_streak


def _restore_correct_result(user, trainer, item_key, pending):
    progress, _ = DrillItemProgress.objects.get_or_create(
        owner=user,
        trainer=trainer,
        item_key=item_key,
    )
    try:
        previous_streak = max(0, int(pending.get("previous_correct_streak", 0)))
    except (TypeError, ValueError):
        previous_streak = 0
    progress.correct_streak = max(progress.correct_streak, previous_streak + 1)
    progress.save(update_fields=["correct_streak", "updated_at"])
    return progress


def _set_retirement_candidate(spec, session, item, progress, feedback, context):
    session["pending_retirement"] = None
    if (
        progress is None
        or item is None
        or progress.correct_streak < RETIRE_STREAK
        or progress.retired_at is not None
    ):
        return
    session["pending_retirement"] = {
        "item_key": spec.progress_key(item, context),
        "label": item.label,
        "correct_streak": progress.correct_streak,
        **spec.pending_extra(item, context),
    }
    feedback["can_retire"] = True


def _retire_item(spec, request, session, context):
    pending = session.pop("pending_retirement", None)
    if not isinstance(pending, dict):
        spec.save(request, session)
        return False
    item_key = pending.get("item_key")
    if not spec.owns_progress_key(item_key, context):
        spec.save(request, session)
        return False
    updated = DrillItemProgress.objects.filter(
        owner=request.user,
        trainer=spec.trainer,
        item_key=item_key,
        correct_streak__gte=RETIRE_STREAK,
        retired_at__isnull=True,
    ).update(retired_at=timezone.now())
    if updated:
        messages.success(request, f"Retired {pending.get('label', '')} from this trainer.")
    spec.save(request, session)
    return bool(updated)


def _mark_last_correct(spec, request, session, context):
    pending = session.pop("pending_correction", None)
    if not isinstance(pending, dict):
        spec.save(request, session)
        return False

    updated = TrainerAttempt.objects.filter(
        pk=pending.get("attempt_id"),
        owner=request.user,
        trainer=spec.trainer,
        is_correct=False,
    ).update(is_correct=True)
    if updated:
        session["correct"] = min(session["seen"], session["correct"] + 1)
        item_key = pending.get("item_key")
        item = None
        progress = None
        if spec.owns_progress_key(item_key, context):
            item = spec.item_for_progress_key(item_key, context)
            if item is not None:
                progress = _restore_correct_result(request.user, spec.trainer, item_key, pending)
        feedback = pending.get("feedback")
        if isinstance(feedback, dict):
            corrected_feedback = {
                **feedback,
                "correct": True,
                "had_typo": False,
                "corrected": True,
            }
            if progress is not None:
                corrected_feedback["correct_streak"] = progress.correct_streak
                _set_retirement_candidate(
                    spec, session, item, progress, corrected_feedback, context
                )
            session["last"] = corrected_feedback
        messages.success(request, "Marked correct.")
    spec.save(request, session)
    return bool(updated)


def _round_percentage(session):
    seen = session["seen"]
    return round(session["correct"] / seen * 100) if seen else 0


def _resume(spec, request):
    """Load and validate a round; returns (session, context, response)."""
    session = spec.load(request)
    if session is None:
        spec.clear(request)
        return None, None, redirect(spec.start_url)
    context = spec.context(request, session=session)
    if not spec.normalize(session, context):
        spec.clear(request)
        return None, None, redirect(spec.start_url)
    return session, context, None


def start_round(request, spec):
    context = spec.context(request)
    items = spec.pool(context)
    if not items:
        messages.info(request, spec.empty_message(context))
        return redirect(spec.empty_redirect)

    random.shuffle(items)
    if spec.round_size:
        items = items[: spec.round_size]
    session = {
        "queue": [spec.queue_value(item) for item in items],
        "index": 0,
        "seen": 0,
        "correct": 0,
        "last": None,
        "pending_correction": None,
        "pending_retirement": None,
    }
    spec.start_session(session, items, context)
    spec.prepare(session, items, context)
    spec.save(request, session)
    return redirect(spec.step_url)


def step_round(request, spec):
    session, context, response = _resume(spec, request)
    if response is not None:
        return response

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_last_correct":
            _mark_last_correct(spec, request, session, context)
            return redirect(spec.step_url)
        if action == "retire_word":
            _retire_item(spec, request, session, context)
            return redirect(spec.step_url)

    if session["index"] >= len(session["queue"]):
        spec.save(request, session)
        return redirect(spec.complete_url)

    item = spec.item_for(session["queue"][session["index"]], context)
    if item is None:
        spec.clear(request)
        return redirect(spec.start_url)

    if request.method == "POST":
        result = spec.check(item, request.POST, session, context)
        session["pending_correction"] = None
        session["pending_retirement"] = None
        attempt = record_trainer_attempt(
            user=request.user,
            trainer=spec.trainer,
            language_mode=context["language_mode"],
            is_correct=result.is_correct,
            card=spec.card_for(item),
            item_label=item.label,
        )
        item_key = spec.progress_key(item, context)
        progress, previous_streak = _record_item_result(
            request.user, spec.trainer, item_key, result.is_correct
        )
        session["seen"] += 1
        session["correct"] += int(result.is_correct)
        feedback = {**result.feedback, "correct_streak": progress.correct_streak}
        _set_retirement_candidate(spec, session, item, progress, feedback, context)
        session["last"] = feedback
        if not result.is_correct:
            session["pending_correction"] = {
                "attempt_id": attempt.pk,
                "item_key": item_key,
                "label": item.label,
                "previous_correct_streak": previous_streak,
                "feedback": feedback,
                **spec.pending_extra(item, context),
            }
        session["index"] += 1
        spec.save(request, session)
        return redirect(spec.step_url)

    last = session.get("last")
    session["last"] = None
    # step_context may fill in per-item state (the preposition choices),
    # so it runs before the round is stored.
    extra = spec.step_context(item, session, context)
    spec.save(request, session)
    return render(
        request,
        spec.step_template,
        {
            "session": session,
            "last": last,
            "position": session["index"] + 1,
            "total": len(session["queue"]),
            **item.prompt,
            **extra,
        },
    )


def complete_round(request, spec):
    session, context, response = _resume(spec, request)
    if response is not None:
        return response

    spec.save(request, session)
    return render(
        request,
        spec.complete_template,
        {
            "session": session,
            "percentage": _round_percentage(session),
            "incorrect": session["seen"] - session["correct"],
            **spec.complete_context(session, context),
        },
    )


def drills_index(request):
    """The hub listing every drill the engine runs for this language mode."""
    mode = get_language_mode(request)
    today = drill_of_the_day(mode)
    drills = available_drills(mode)
    for drill in drills:
        drill["is_today"] = drill["trainer"] == today
    return render(request, "reviews/drills_index.html", {"drills": drills})
