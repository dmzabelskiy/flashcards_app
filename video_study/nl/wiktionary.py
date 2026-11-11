"""English Wiktionary lookups for Dutch word capture: lemma, article, plural, glosses.

Only the single word being looked up is sent. Parsed pages are cached in the
shared `NlDictionaryEntry` table; failed lookups are not cached so the next
click tries again.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from video_study.dictionary import (
    FOUND,
    MISSING,
    MISSING_PAGE_CODES,
    UNAVAILABLE,
    DictionaryUnavailable,
)
from video_study.models import NlDictionaryEntry
from video_study.services import clean_translation_text

logger = logging.getLogger(__name__)

VOWELS = "aeiou"

POS_HEADINGS = {
    "Noun": "noun",
    "Verb": "verb",
    "Adjective": "adjective",
    "Adverb": "adverb",
    "Participle": "verb",
}

HEADING = re.compile(r"^(=+)(?!=)(.*?)(?<!=)\1\s*$", re.M)
GLOSS_LINE = re.compile(r"^# (.*)$", re.M)
DIMINUTIVE_OF = re.compile(r"^\{\{diminutive of\|nl\|")
POINTER_TEMPLATE = re.compile(
    r"^\{\{(?:plural of|infl of|inflection of|past participle of)\|nl\|([^|}]+)"
)
POINTER_NOUN_FORM = re.compile(r"^\{\{nl-noun form of\|([^|}]+)")
OBSOLETE_LABEL = re.compile(r"\{\{lb\|nl\|([^}]*)\}\}")
NOUN_HEAD = re.compile(r"\{\{nl-noun\|([^}]*)\}\}")
NOUN_DIM_HEAD = re.compile(r"\{\{nl-noun-dim(?:-tant)?\}\}")


def _empty_fields(status):
    return {
        "status": status,
        "lemma": "",
        "base_form": "",
        "article": "",
        "plural": "",
        "part_of_speech": "",
        "glosses": "",
    }


def _dutch_section(wikitext):
    headings = list(HEADING.finditer(wikitext))
    for index, heading in enumerate(headings):
        if len(heading.group(1)) == 2 and heading.group(2).strip() == "Dutch":
            end = len(wikitext)
            for later in headings[index + 1 :]:
                if len(later.group(1)) == 2:
                    end = later.start()
                    break
            return wikitext[heading.end() : end]
    return None


def _pos_sections(section):
    """Return (heading title, body) for each level 3/4 part-of-speech heading."""
    headings = list(HEADING.finditer(section))
    sections = []
    for index, heading in enumerate(headings):
        level = len(heading.group(1))
        title = heading.group(2).strip()
        if level not in (3, 4) or title not in POS_HEADINGS:
            continue
        end = len(section)
        for later in headings[index + 1 :]:
            if len(later.group(1)) <= 4:
                end = later.start()
                break
        sections.append((title, section[heading.end() : end]))
    return sections


def _classify_gloss(line, title):
    """Return ("diminutive" | "self" | "pointer" | "gloss", value, is_obsolete)."""
    if DIMINUTIVE_OF.match(line):
        return "diminutive", None, False
    match = POINTER_TEMPLATE.match(line) or POINTER_NOUN_FORM.match(line)
    if match:
        base = match.group(1).strip()
        if base and base != title:
            return "pointer", base, False
        return "self", None, False
    label = OBSOLETE_LABEL.search(line)
    is_obsolete = False
    if label:
        args = {part.strip().casefold() for part in label.group(1).split("|")}
        is_obsolete = bool(args & {"obsolete", "archaic"})
    return "gloss", line, is_obsolete


def _analyse_section(body, title):
    """Return (real gloss lines, all obsolete, first pointer base, has a diminutive-of gloss) for a section."""
    real_glosses = []
    obsolete_flags = []
    pointer_base = None
    has_diminutive_of = False
    for line in GLOSS_LINE.findall(body):
        kind, value, is_obsolete = _classify_gloss(line, title)
        if kind == "gloss":
            real_glosses.append(value)
            obsolete_flags.append(is_obsolete)
        elif kind == "pointer" and pointer_base is None:
            pointer_base = value
        elif kind == "diminutive":
            has_diminutive_of = True
    all_obsolete = bool(real_glosses) and all(obsolete_flags)
    return real_glosses, all_obsolete, pointer_base, has_diminutive_of


def _join_glosses(real_glosses):
    return clean_translation_text("; ".join(real_glosses), max_alternatives=3)


def _nl_plural(title, plural_form):
    """Apply a `-suffix` plural shorthand from `{{nl-noun|...}}` to `title`."""
    if not plural_form or plural_form == "-":
        return ""
    if not plural_form.startswith("-"):
        return plural_form
    suffix = plural_form[1:]
    if not suffix:
        return ""
    if suffix[0] == "z" and title.endswith("s"):
        return title[:-1] + suffix
    if suffix[0] == "v" and title.endswith("f"):
        return title[:-1] + suffix
    if (
        len(title) >= 3
        and title[-1] not in VOWELS
        and title[-2] in VOWELS
        and title[-2] == title[-3]
        and suffix[0] in VOWELS
    ):
        return title[:-2] + title[-1] + suffix
    ends_in_two_consonants = len(title) >= 2 and title[-1] not in VOWELS and title[-2] not in VOWELS
    if ends_in_two_consonants or suffix[0] not in VOWELS or suffix in ("s", "'s"):
        return title + suffix
    return ""


def _noun_fields(body, title):
    dim_head = NOUN_DIM_HEAD.search(body)
    if dim_head:
        return "het", title + "s"
    head = NOUN_HEAD.search(body)
    if not head:
        return "", ""
    args = head.group(1).split("|")
    genders = {part.strip() for part in (args[0] if args else "").split(",")}
    if genders & {"m", "f", "c"}:
        article = "de"
    elif "n" in genders:
        article = "het"
    else:
        article = ""
    plural_arg = args[1].strip() if len(args) > 1 else ""
    first_form = plural_arg.split(",")[0].strip() if plural_arg else ""
    return article, _nl_plural(title, first_form)


def parse_nl_entry(title, wikitext):
    """Parse one page's wikitext into `NlDictionaryEntry` field values."""
    section = _dutch_section(wikitext)
    if section is None:
        return _empty_fields(MISSING)

    lemma_section = None
    diminutive_section = None
    obsolete_section = None
    pointer_base = None
    for heading_title, body in _pos_sections(section):
        if heading_title == "Proper noun":
            continue
        real_glosses, all_obsolete, section_pointer, has_diminutive_of = _analyse_section(
            body, title
        )
        if pointer_base is None and section_pointer:
            pointer_base = section_pointer
        if real_glosses and not all_obsolete and lemma_section is None:
            lemma_section = (heading_title, body, real_glosses)
        elif real_glosses and all_obsolete and obsolete_section is None:
            obsolete_section = (heading_title, body, real_glosses)
        elif (
            not real_glosses
            and has_diminutive_of
            and heading_title == "Noun"
            and NOUN_DIM_HEAD.search(body)
            and diminutive_section is None
        ):
            # A diminutive noun's own page often carries no independent gloss
            # beyond `{{diminutive of|nl|X}}`; the head template alone still
            # identifies it as a found `het` lemma with its own plural.
            diminutive_section = (heading_title, body, real_glosses)

    if lemma_section is not None:
        chosen = lemma_section
    elif diminutive_section is not None:
        chosen = diminutive_section
    elif pointer_base:
        fields = _empty_fields(FOUND)
        fields.update(lemma=pointer_base, base_form=pointer_base)
        return fields
    elif obsolete_section is not None:
        chosen = obsolete_section
    else:
        return _empty_fields(MISSING)

    heading_title, body, real_glosses = chosen
    fields = _empty_fields(FOUND)
    fields["lemma"] = title
    fields["part_of_speech"] = POS_HEADINGS[heading_title]
    fields["glosses"] = _join_glosses(real_glosses)
    if fields["part_of_speech"] == "noun":
        fields["article"], fields["plural"] = _noun_fields(body, title)
    return fields


@dataclass(frozen=True)
class NlDictionaryResult:
    status: str
    lemma: str = ""
    article: str = ""
    plural: str = ""
    part_of_speech: str = ""
    glosses: str = ""


def _fetch(title, deadline):
    """Return (resolved title, wikitext), or None when the page does not exist."""
    endpoint = getattr(settings, "VIDEO_STUDY_NL_DICTIONARY_ENDPOINT", "")
    if not endpoint:
        raise DictionaryUnavailable("Dictionary lookups are disabled.")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DictionaryUnavailable("The lookup budget is spent.")
    timeout = max(0.001, min(getattr(settings, "VIDEO_STUDY_TRANSLATION_TIMEOUT", 8), remaining))
    query = urlencode(
        {
            "action": "parse",
            "page": title,
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
    )
    request = Request(
        f"{endpoint}?{query}",
        headers={"Accept": "application/json", "User-Agent": "GermanFlashcards/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        status = getattr(exc, "code", None) or type(exc).__name__
        logger.warning("Dutch dictionary lookup for %r failed (status=%s): %s", title, status, exc)
        raise DictionaryUnavailable(str(exc)) from exc
    if not isinstance(payload, dict):
        logger.warning("Dutch dictionary lookup for %r failed: unexpected response shape", title)
        raise DictionaryUnavailable("Unexpected response shape.")
    error = payload.get("error")
    if error is not None:
        if not isinstance(error, dict):
            logger.warning(
                "Dutch dictionary lookup for %r failed: unexpected response shape", title
            )
            raise DictionaryUnavailable("Unexpected response shape.")
        if error.get("code") in MISSING_PAGE_CODES:
            return None
        logger.warning("Dutch dictionary lookup for %r failed: %s", title, error.get("code"))
        raise DictionaryUnavailable(str(error.get("code")))
    parse = payload.get("parse")
    if parse is None:
        parse = {}
    if not isinstance(parse, dict):
        logger.warning("Dutch dictionary lookup for %r failed: unexpected response shape", title)
        raise DictionaryUnavailable("Unexpected response shape.")
    wikitext = parse.get("wikitext") or ""
    if not isinstance(wikitext, str):
        logger.warning("Dutch dictionary lookup for %r failed: unexpected response shape", title)
        raise DictionaryUnavailable("Unexpected response shape.")
    return parse.get("title") or title, wikitext


def _page(title, deadline):
    entry = NlDictionaryEntry.objects.filter(headword=title).first()
    if entry is not None:
        return entry
    fetched = _fetch(title, deadline)
    fields = _empty_fields(MISSING) if fetched is None else parse_nl_entry(*fetched)
    entry, _created = NlDictionaryEntry.objects.update_or_create(headword=title, defaults=fields)
    return entry


def _as_result(entry):
    return NlDictionaryResult(
        status=entry.status,
        lemma=entry.lemma,
        article=entry.article,
        plural=entry.plural,
        part_of_speech=entry.part_of_speech,
        glosses=entry.glosses,
    )


def _resolve(title, deadline):
    entry = _page(title, deadline)
    if entry.status != FOUND or not entry.base_form:
        return _as_result(entry)
    base = _page(entry.base_form, deadline)
    if base.status == FOUND and not base.base_form:
        return _as_result(base)
    return NlDictionaryResult(status=FOUND, lemma=entry.base_form)


def lookup_nl_dictionary(candidate, surface_form, budget_start=None):
    """Confirm ``candidate`` on English Wiktionary, falling back to ``surface_form``."""
    start = time.monotonic() if budget_start is None else budget_start
    deadline = start + getattr(settings, "VIDEO_STUDY_TRANSLATION_BUDGET", 12)
    try:
        result = _resolve(candidate, deadline)
        if result.status == MISSING and surface_form and surface_form != candidate:
            result = _resolve(surface_form, deadline)
    except DictionaryUnavailable:
        return NlDictionaryResult(status=UNAVAILABLE)
    return result
