"""Drill files and the registry of what each language pack offers.

A drill is a YAML file in ``languages/<code>/drills/`` (see
``docs/language_packs.md``).  This
module is pure data: it reads and validates those files, and answers "which
drills does this study mode offer".  It imports no models, so the video-study
code may read drill content too.  Everything that runs a drill lives in
``reviews/drill_specs.py`` and ``reviews/drill_engine.py``.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from django.conf import settings

from core.language_packs import PACKS, load_packs
from reviews.normalisers import NORMALISERS

DRILLS_FOLDER = "drills"
ARTICLE_SLUG = "articles"
ARTICLE_TRAINER = "article"
# Trainer names the stored history already uses for something else.
RESERVED_TRAINERS = frozenset(
    {
        ARTICLE_TRAINER,
        "typed_review",
        "spaced_review",
        "video",
        "podcast",
        "grammar",
        "telc_b2",
        "deutsch_online",
    }
)
FIELD_KINDS = ("typed", "choice", "pool_choice")
DEFAULT_DISTRACTORS = 2
DEFAULT_PICTOGRAM = "link"  # a symbol of templates/core/_pictograms.html
MAX_KEY_LENGTH = 200  # DrillItemProgress.item_key
MAX_TRAINER_LENGTH = 32  # TrainerAttempt.trainer

_SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
_DRILL_FIELDS = {
    "slug",
    "trainer",
    "label",
    "description",
    "order",
    "round_size",
    "targets",
    "instruction",
    "hint",
    "pictogram",
    "fields",
    "items",
}
_DRILL_REQUIRED = {"slug", "trainer", "label", "description", "fields", "items"}
_FIELD_FIELDS = {
    "key",
    "label",
    "kind",
    "normalise",
    "hint",
    "placeholder",
    "options",
    "distractors",
}
_ITEM_RESERVED = {"example", "reveal"}


class DrillError(ValueError):
    """A drill file is malformed; the message names the file and the field."""


@dataclass(frozen=True, eq=False)
class Field:
    key: str
    label: str
    kind: str
    normalise: str
    hint: str = ""
    placeholder: str = ""
    options: tuple = ()  # (value, label) pairs of a ``choice`` field
    distractors: int = DEFAULT_DISTRACTORS

    @property
    def normaliser(self):
        return NORMALISERS[self.normalise]

    def option_label(self, value):
        for option_value, label in self.options:
            if option_value == value:
                return label
        return value


@dataclass(frozen=True, eq=False)
class Alternative:
    """One complete answer set: every field must match within one alternative."""

    answers: dict  # field key -> tuple of answers, or {target: tuple}
    example: str = ""
    reveal: tuple = ()  # (label, value) pairs shown, never graded

    def accepted(self, key, target):
        """The accepted answers of a field for a target language, or None."""
        value = self.answers[key]
        if isinstance(value, dict):
            return value.get(target)
        return value


@dataclass(frozen=True, eq=False)
class Item:
    id: str
    prompt: str
    alternatives: tuple

    def alternatives_for(self, target):
        """The alternatives that have an answer for every field in ``target``."""
        return tuple(
            alternative
            for alternative in self.alternatives
            if all(alternative.accepted(key, target) for key in alternative.answers)
        )


@dataclass(frozen=True, eq=False)
class Drill:
    pack: str
    slug: str
    trainer: str
    label: str
    description: str
    fields: tuple = ()
    items: tuple = ()
    order: int = 100
    round_size: int = None
    targets: tuple = None
    instruction: str = ""
    hint: str = ""
    pictogram: str = DEFAULT_PICTOGRAM
    builtin: bool = False
    path: str = ""

    def offered_for(self, target):
        return self.targets is None or target in self.targets

    def field(self, key):
        return next((field for field in self.fields if field.key == key), None)

    def item(self, item_id):
        return self._index.get(item_id)

    @property
    def _index(self):
        index = self.__dict__.get("_item_index")
        if index is None:
            index = {item.id: item for item in self.items}
            object.__setattr__(self, "_item_index", index)
        return index

    def items_for(self, target):
        """The items with at least one complete alternative in ``target``."""
        return [item for item in self.items if item.alternatives_for(target)]

    def pool(self, field_key, target):
        """Every answer of a field across the drill, for ``target``."""
        answers = set()
        for item in self.items:
            for alternative in item.alternatives_for(target):
                answers.update(alternative.accepted(field_key, target))
        return sorted(answers)


# -- reading and validating one file ------------------------------------------


def _fail(path, where, problem):
    raise DrillError(f"{path}: field '{where}' {problem}")


def _text(path, where, value, *, required=True):
    if not isinstance(value, str) or (required and not value.strip()):
        _fail(path, where, "must be a non-empty string" if required else "must be a string")
    return value.strip()


def _optional_text(path, data, key, where=""):
    return _text(path, f"{where}{key}", data[key], required=False) if key in data else ""


def _check_keys(path, data, allowed, required, where=""):
    for key in data:
        if key not in allowed:
            _fail(path, f"{where}{key}", "is not a known field")
    for key in sorted(required - set(data)):
        _fail(path, f"{where}{key}", "is missing")


def _answers(path, where, value):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(answer, str) or not answer.strip() for answer in value)
    ):
        _fail(path, where, "must be a non-empty list of non-empty strings")
    return tuple(answer.strip() for answer in value)


def _read_field(path, index, data):
    where = f"fields[{index}]."
    if not isinstance(data, dict):
        _fail(path, f"fields[{index}]", "must be a mapping")
    _check_keys(path, data, _FIELD_FIELDS, {"key", "label", "kind"}, where)
    key = _text(path, where + "key", data["key"])
    if not _IDENTIFIER.fullmatch(key) or key in _ITEM_RESERVED or key == "alternatives":
        _fail(path, where + "key", "must be a lowercase identifier other than example or reveal")
    kind = data["kind"]
    if kind not in FIELD_KINDS:
        _fail(path, where + "kind", f"must be one of {', '.join(FIELD_KINDS)}")
    normalise = data.get("normalise", "exact" if kind != "typed" else None)
    if normalise not in NORMALISERS:
        _fail(path, where + "normalise", f"must be one of {', '.join(sorted(NORMALISERS))}")

    options = ()
    if kind == "choice":
        raw = data.get("options")
        if not isinstance(raw, list) or len(raw) < 2:
            _fail(path, where + "options", "must list at least two options")
        pairs = []
        for number, option in enumerate(raw):
            if isinstance(option, str):
                option = {"value": option, "label": option}
            if not isinstance(option, dict) or set(option) - {"value", "label"}:
                _fail(path, f"{where}options[{number}]", "must be a string or a value/label map")
            value = _text(path, f"{where}options[{number}].value", option.get("value"))
            label = _text(path, f"{where}options[{number}].label", option.get("label", value))
            pairs.append((value, label))
        if len({value for value, _ in pairs}) != len(pairs):
            _fail(path, where + "options", "must not repeat a value")
        options = tuple(pairs)
    elif "options" in data:
        _fail(path, where + "options", "is only allowed on a choice field")

    distractors = data.get("distractors", DEFAULT_DISTRACTORS)
    if "distractors" in data and kind != "pool_choice":
        _fail(path, where + "distractors", "is only allowed on a pool_choice field")
    if not isinstance(distractors, int) or isinstance(distractors, bool) or distractors < 1:
        _fail(path, where + "distractors", "must be a positive integer")

    return Field(
        key=key,
        label=_text(path, where + "label", data["label"]),
        kind=kind,
        normalise=normalise,
        hint=_optional_text(path, data, "hint", where),
        placeholder=_optional_text(path, data, "placeholder", where),
        options=options,
        distractors=distractors,
    )


def _read_answers(path, where, value):
    """A field's answers: a list, or a map from target language to a list."""
    if isinstance(value, dict):
        if not value:
            _fail(path, where, "must not be an empty map")
        return {
            _text(path, f"{where}.<target>", str(target)).lower(): _answers(
                path, f"{where}.{target}", answers
            )
            for target, answers in value.items()
        }
    return _answers(path, where, value)


def _read_alternative(path, where, data, fields):
    if not isinstance(data, dict):
        _fail(path, where, "must be a mapping")
    keys = {field.key for field in fields}
    _check_keys(path, data, keys | _ITEM_RESERVED, keys, f"{where}.")
    answers = {}
    for field in fields:
        answers[field.key] = _read_answers(path, f"{where}.{field.key}", data[field.key])
        values = (
            [v for group in answers[field.key].values() for v in group]
            if isinstance(answers[field.key], dict)
            else answers[field.key]
        )
        if field.kind == "choice":
            known = {value for value, _ in field.options}
            for value in values:
                if value not in known:
                    _fail(path, f"{where}.{field.key}", f"has '{value}', which is not an option")
    reveal = data.get("reveal", {})
    if not isinstance(reveal, dict):
        _fail(path, f"{where}.reveal", "must be a map of label to value")
    return Alternative(
        answers=answers,
        example=_optional_text(path, data, "example", f"{where}."),
        reveal=tuple(
            (
                _text(path, f"{where}.reveal", str(label)),
                _text(path, f"{where}.reveal.{label}", value),
            )
            for label, value in reveal.items()
        ),
    )


def _read_item(path, index, data, fields, seen):
    where = f"items[{index}]"
    if not isinstance(data, dict):
        _fail(path, where, "must be a mapping")
    _check_keys(
        path, data, {"id", "prompt", "alternatives"}, {"id", "prompt", "alternatives"}, where + "."
    )
    item_id = _text(path, where + ".id", data["id"])
    if len(item_id) > MAX_KEY_LENGTH:
        _fail(path, where + ".id", f"must be at most {MAX_KEY_LENGTH} characters")
    if item_id in seen:
        _fail(path, where + ".id", f"repeats '{item_id}'")
    seen.add(item_id)
    alternatives = data["alternatives"]
    if not isinstance(alternatives, list) or not alternatives:
        _fail(path, where + ".alternatives", "must be a non-empty list")
    return Item(
        id=item_id,
        prompt=_text(path, where + ".prompt", data["prompt"]),
        alternatives=tuple(
            _read_alternative(path, f"{where}.alternatives[{number}]", alternative, fields)
            for number, alternative in enumerate(alternatives)
        ),
    )


def load_drill(path, pack_code):
    """Read one drill file into a Drill, or raise DrillError."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DrillError(f"{path}: cannot be read as YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise DrillError(f"{path}: must contain a mapping of fields")
    _check_keys(path, data, _DRILL_FIELDS, _DRILL_REQUIRED)

    slug = _text(path, "slug", data["slug"])
    if not _SLUG.fullmatch(slug) or slug == ARTICLE_SLUG:
        _fail(path, "slug", "must be lowercase words joined by hyphens, and not 'articles'")
    trainer = _text(path, "trainer", data["trainer"])
    if (
        not _IDENTIFIER.fullmatch(trainer)
        or len(trainer) > MAX_TRAINER_LENGTH
        or trainer in RESERVED_TRAINERS
    ):
        _fail(path, "trainer", "must be a new lowercase identifier of at most 32 characters")
    order = data.get("order", 100)
    if not isinstance(order, int) or isinstance(order, bool):
        _fail(path, "order", "must be an integer")
    round_size = data.get("round_size")
    if round_size is not None and (
        not isinstance(round_size, int) or isinstance(round_size, bool) or round_size < 1
    ):
        _fail(path, "round_size", "must be a positive integer")
    targets = data.get("targets")
    if targets is not None:
        targets = tuple(target.lower() for target in _answers(path, "targets", targets))

    raw_fields = data["fields"]
    if not isinstance(raw_fields, list) or not raw_fields:
        _fail(path, "fields", "must be a non-empty list")
    fields = tuple(_read_field(path, index, raw) for index, raw in enumerate(raw_fields))
    if len({field.key for field in fields}) != len(fields):
        _fail(path, "fields", "must not repeat a key")

    raw_items = data["items"]
    if not isinstance(raw_items, list) or not raw_items:
        _fail(path, "items", "must be a non-empty list")
    seen = set()
    items = tuple(_read_item(path, index, raw, fields, seen) for index, raw in enumerate(raw_items))

    return Drill(
        pack=pack_code,
        slug=slug,
        trainer=trainer,
        label=_text(path, "label", data["label"]),
        description=_text(path, "description", data["description"]),
        fields=fields,
        items=items,
        order=order,
        round_size=round_size,
        targets=targets,
        instruction=_optional_text(path, data, "instruction"),
        hint=_optional_text(path, data, "hint"),
        pictogram=_optional_text(path, data, "pictogram") or DEFAULT_PICTOGRAM,
        path=str(path),
    )


def _article_drill(pack):
    articles = " / ".join(pack.articles)
    return Drill(
        pack=pack.code,
        slug=ARTICLE_SLUG,
        trainer=ARTICLE_TRAINER,
        label="Articles",
        description=f"{articles} for the noun cards in your deck.",
        order=0,
        pictogram="article",
        builtin=True,
    )


# -- the registry --------------------------------------------------------------


class DrillRegistry:
    """The drills of every language pack, in pack order, article drill first."""

    def __init__(self, drills_by_pack):
        self.drills_by_pack = drills_by_pack
        self._all = [drill for drills in drills_by_pack.values() for drill in drills]
        for attribute in ("slug", "trainer"):
            seen = {}
            # Every pack with articles has its own copy of the built-in article drill.
            for drill in (d for d in self._all if not d.builtin):
                other = seen.setdefault(getattr(drill, attribute), drill)
                if other is not drill:
                    raise DrillError(
                        f"{drill.path or drill.pack}: {attribute} '{getattr(drill, attribute)}' "
                        f"is also used by {other.path or other.pack}"
                    )

    def pack_code_for_mode(self, mode):
        code = str(mode or "").split("-", 1)[0].lower()
        return code if code in self.drills_by_pack else "de"

    @staticmethod
    def target_for_mode(mode):
        return str(mode or "").rsplit("-", 1)[-1].lower()

    def for_mode(self, mode):
        """The drills offered in a study mode, article drill first."""
        target = self.target_for_mode(mode)
        drills = self.drills_by_pack.get(self.pack_code_for_mode(mode), ())
        return [drill for drill in drills if drill.offered_for(target)]

    def find(self, mode, slug):
        return next((drill for drill in self.for_mode(mode) if drill.slug == slug), None)

    def for_trainer(self, mode, trainer):
        return next((drill for drill in self.for_mode(mode) if drill.trainer == trainer), None)

    def by_slug(self, slug):
        """A drill of any pack, offered or not."""
        return next((drill for drill in self._all if drill.slug == slug), None)

    def by_trainer(self, trainer):
        return next((drill for drill in self._all if drill.trainer == trainer), None)


def load_registry(directory, packs):
    """Load every pack's drill files from ``<directory>/<code>/drills/*.yml``."""
    directory = Path(directory)
    drills_by_pack = {}
    for code, pack in packs.items():
        drills = []
        if pack.articles:
            drills.append(_article_drill(pack))
        folder = directory / code / DRILLS_FOLDER
        if folder.is_dir():
            loaded = [load_drill(path, code) for path in sorted(folder.glob("*.yml"))]
            drills.extend(sorted(loaded, key=lambda drill: (drill.order, drill.path)))
        drills_by_pack[code] = tuple(drills)
    return DrillRegistry(drills_by_pack)


def registry_from_directory(directory):
    """A registry for a language folder that is not the project's own (tests)."""
    return load_registry(directory, load_packs(directory))


REGISTRY = load_registry(settings.LANGUAGES_DIR, PACKS)


def drills_for_mode(mode):
    return REGISTRY.for_mode(mode)


def drill_for_slug(mode, slug):
    return REGISTRY.find(mode, slug)


def drill_for_trainer(mode, trainer):
    return REGISTRY.for_trainer(mode, str(trainer))


def drill_exists(slug):
    return REGISTRY.by_slug(slug) is not None


def is_drill_trainer(trainer):
    return REGISTRY.by_trainer(str(trainer)) is not None
