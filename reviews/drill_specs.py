"""The drill specifications the engine runs.

Two kinds: the built-in article drill, whose items are the learner's own noun
cards, and the generic data drill, which builds its items, its answer form and
its matching rule from a drill file (``reviews/drills.py``).  Everything shared
-- the session lifecycle, analytics, the templates' common context -- lives in
``reviews/drill_engine.py``.
"""

import random

from cards.models import Card
from core.language_modes import get_target_language, get_target_language_label
from reviews.article_cards import article_cards_for_user
from reviews.drill_engine import DrillItem, DrillResult, DrillSpec
from reviews.drills import ARTICLE_SLUG, ARTICLE_TRAINER
from reviews.normalisers import matches, normalise_exact

DEFAULT_HINT = "Enter submits · Tab moves between controls"
TARGET_LABEL = "{target_label}"
# Stored in every data-drill round. A round without this key is dropped, not
# converted.
ROUND_FORMAT = 2


def _article_item(card):
    return DrillItem(
        key=str(card.pk),
        label=card.front,
        prompt={"card": card},
        answers=card.article,
        example=card.example_sentence,
    )


class ArticleDrillSpec(DrillSpec):
    """The pack's articles for the noun cards in the user's own decks."""

    slug = ARTICLE_SLUG
    trainer = ARTICLE_TRAINER
    label = "Articles"
    session_key = "article_drill_session"
    step_template = "reviews/article_drill.html"
    complete_template = "reviews/article_drill_complete.html"
    retry_label = "Drill again"
    round_size = None

    def empty_message(self, context):
        return "No noun cards with an article to drill yet."

    def items(self, context):
        cards = article_cards_for_user(context["user"], context["language_mode"])
        return [_article_item(card) for card in cards]

    def queue_value(self, item):
        # The queue holds card pks as integers.
        return int(item.key)

    def item_for(self, queue_value, context):
        try:
            card = (
                Card.objects.for_user(context["user"])
                .select_related("deck")
                .filter(pk=queue_value)
                .first()
            )
        except (TypeError, ValueError):
            return None
        return _article_item(card) if card is not None else None

    def check(self, item, post, session, context):
        card = item.prompt["card"]
        answer = post.get("answer", "")
        correct = answer == card.article
        return DrillResult(
            is_correct=correct,
            feedback={
                "correct": correct,
                "expected": card.article,
                "chosen": answer,
                "stem": card.noun_stem,
            },
        )

    def card_for(self, item):
        return item.prompt["card"]


class DataDrillSpec(DrillSpec):
    """A drill read from a drill file: typed, choice and pool_choice fields."""

    step_template = "reviews/drill_step.html"
    complete_template = "reviews/drill_complete.html"

    def __init__(self, drill):
        self.drill = drill
        self.slug = drill.slug
        self.trainer = drill.trainer
        self.label = drill.label
        self.session_key = f"{drill.trainer}_drill_session"
        self.round_size = drill.round_size

    # -- round context -------------------------------------------------------

    def context(self, request, session=None, **kwargs):
        context = super().context(request, session=session, **kwargs)
        language_mode = context["language_mode"]
        context["target"] = get_target_language(language_mode)
        context["target_label"] = get_target_language_label(language_mode)
        return context

    def _text(self, text, context):
        return text.replace(TARGET_LABEL, context["target_label"])

    # -- items ---------------------------------------------------------------

    def _item(self, entry):
        return DrillItem(
            key=entry.id,
            label=entry.prompt,
            prompt={"drill_prompt": entry.prompt},
            answers=entry,
        )

    def items(self, context):
        return [self._item(entry) for entry in self.drill.items_for(context["target"])]

    def item_for(self, queue_value, context):
        if not isinstance(queue_value, str):
            return None
        entry = self.drill.item(queue_value)
        if entry is None or not entry.alternatives_for(context["target"]):
            return None
        return self._item(entry)

    def empty_message(self, context):
        return "There is nothing to drill yet."

    # -- pool_choice ---------------------------------------------------------

    def _pool_fields(self):
        return [field for field in self.drill.fields if field.kind == "pool_choice"]

    def _accepted_values(self, entry, field, target):
        """Every value of the field that any alternative of the item accepts."""
        values = {}
        for alternative in entry.alternatives_for(target):
            for value in alternative.accepted(field.key, target):
                values.setdefault(normalise_exact(value), value)
        return list(values.values())

    def _build_choices(self, entry, field, context):
        target = context["target"]
        accepted = self._accepted_values(entry, field, target)
        excluded = {normalise_exact(value) for value in accepted}
        candidates = [
            answer
            for answer in self.drill.pool(field.key, target)
            if normalise_exact(answer) not in excluded
        ]
        distractors = random.sample(candidates, min(field.distractors, len(candidates)))
        choices = [*accepted, *distractors]
        random.shuffle(choices)
        return choices

    def _choices_for(self, session, entry, field, context):
        """The round's fixed options for one pool_choice field of one item."""
        store = session.get("choices")
        if not isinstance(store, dict):
            store = session["choices"] = {}
        stored = store.get(entry.id)
        if not isinstance(stored, dict):
            stored = {}
        choices = stored.get(field.key)
        accepted = self._accepted_values(entry, field, context["target"])
        if not isinstance(choices, list) or not all(isinstance(choice, str) for choice in choices):
            choices = None
        else:
            offered = {normalise_exact(choice) for choice in choices}
            if not all(normalise_exact(value) in offered for value in accepted):
                choices = None
        if choices is None:
            choices = self._build_choices(entry, field, context)
        stored[field.key] = choices
        store[entry.id] = stored
        return choices

    def start_session(self, session, items, context):
        session["round_format"] = ROUND_FORMAT

    def prepare(self, session, items, context):
        if not self._pool_fields():
            return
        session["choices"] = {}
        for item in items:
            for field in self._pool_fields():
                self._choices_for(session, item.answers, field, context)

    def normalize(self, session, context):
        if session.get("round_format") != ROUND_FORMAT:
            return False
        if not super().normalize(session, context):
            return False
        if not isinstance(session.get("choices", {}), dict):
            session["choices"] = {}
        return True

    # -- answering -----------------------------------------------------------

    def _given(self, field, post):
        value = post.get(field.key, "")
        # A radio sends one option value whole, so a choice is never trimmed.
        return value if field.kind == "choice" else value.strip()

    def _field_matches(self, field, given, alternative, target, choices):
        accepted = alternative.accepted(field.key, target)
        if field.kind == "choice" and given != given.strip():
            return False
        if choices is not None and not matches(given, choices, normalise_exact):
            return False
        return matches(given, accepted, field.normaliser)

    def _expected(self, field, alternative, target):
        values = alternative.accepted(field.key, target)
        return " / ".join(field.option_label(value) for value in values)

    def _shown_given(self, field, given):
        if field.kind == "choice":
            for value, label in field.options:
                if normalise_exact(value) == normalise_exact(given) and given:
                    return label
        return given

    def check(self, item, post, session, context):
        entry = item.answers
        target = context["target"]
        fields = self.drill.fields
        alternatives = entry.alternatives_for(target)
        given = {field.key: self._given(field, post) for field in fields}
        choices = {
            field.key: self._choices_for(session, entry, field, context)
            for field in self._pool_fields()
        }

        outcomes = [
            [
                self._field_matches(
                    field, given[field.key], alternative, target, choices.get(field.key)
                )
                for field in fields
            ]
            for alternative in alternatives
        ]
        matched = next(
            (
                alternative
                for alternative, flags in zip(alternatives, outcomes, strict=True)
                if all(flags)
            ),
            None,
        )
        shown = matched or alternatives[0]
        flags = [True] * len(fields) if matched else outcomes[0]

        rows = [
            {
                "label": self._text(field.label, context),
                "given": self._shown_given(field, given[field.key]),
                "expected": self._expected(field, shown, target),
                "correct": flag,
            }
            for field, flag in zip(fields, flags, strict=True)
        ]
        accepted = []
        if len(alternatives) > 1:
            accepted = [
                " · ".join(self._expected(field, alternative, target) for field in fields)
                for alternative in alternatives
            ]
        return DrillResult(
            is_correct=matched is not None,
            feedback={
                "correct": matched is not None,
                "prompt": entry.prompt,
                "rows": rows,
                "accepted": accepted,
                "reveal": [list(pair) for pair in shown.reveal],
                "example": shown.example,
            },
        )

    # -- rendering -----------------------------------------------------------

    def step_context(self, item, session, context):
        fields = []
        for number, field in enumerate(self.drill.fields):
            view = {
                "key": field.key,
                "label": self._text(field.label, context),
                "kind": field.kind,
                "hint": self._text(field.hint, context),
                "placeholder": self._text(field.placeholder, context),
                "autofocus": number == 0 and field.kind == "typed",
                "options": [{"value": value, "label": label} for value, label in field.options],
            }
            if field.kind == "pool_choice":
                view["choices"] = self._choices_for(session, item.answers, field, context)
            fields.append(view)
        return {
            "drill": self.drill,
            "fields": fields,
            "instruction": self._text(self.drill.instruction, context),
            "drill_hint": self.drill.hint or DEFAULT_HINT,
            "description": self._text(self.drill.description, context),
        }

    def complete_context(self, session, context):
        return {**super().complete_context(session, context), "drill": self.drill}


def spec_for(drill):
    """The spec that runs a drill from the registry."""
    return ArticleDrillSpec() if drill.builtin else DataDrillSpec(drill)
