"""German Wiktionary lookups for word capture: lemma, article, plural, example.

Only the single word being looked up is sent. Parsed pages are cached in the
shared `DictionaryEntry` table; failed lookups are not cached so the next
click tries again.
"""

import html
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from video_study.models import DictionaryEntry

logger = logging.getLogger(__name__)

FOUND = "found"
MISSING = "missing"
UNAVAILABLE = "unavailable"

EXAMPLE_MAX_LENGTH = 200
GENUS_TO_ARTICLE = {"m": "der", "f": "die", "n": "das"}
WORTART_TO_PART_OF_SPEECH = {
    "Substantiv": "noun",
    "Verb": "verb",
    "Adjektiv": "adjective",
    "Adverb": "adverb",
}
NOT_ORDINARY_MARKERS = ("Toponym", "Vorname", "Nachname", "Eigenname")
MISSING_PAGE_CODES = {"missingtitle", "invalidtitle"}

LANGUAGE_HEADING = re.compile(r"^==(?!=)\s*.*\(\{\{Sprache\|([^}]+)\}\}\)\s*==\s*$", re.M)
LEVEL_THREE_HEADING = re.compile(r"^===(?!=).*$", re.M)
WORTART = re.compile(r"\{\{Wortart\|([^|}]+)\|Deutsch\}\}")
HEADING_GENUS = re.compile(r"\{\{([mfn])\}\}")
POINTER = re.compile(r"\{\{Grundformverweis (?:Dekl|Konj)\|([^|}]+)")
EXAMPLE_PREFIX = re.compile(r"^:\s*\[[^\]]*\]\s*")
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")


class DictionaryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DictionaryResult:
    status: str
    lemma: str = ""
    article: str = ""
    plural: str = ""
    part_of_speech: str = ""
    example_sentence: str = ""


def _empty_fields(status):
    return {
        "status": status,
        "lemma": "",
        "base_form": "",
        "article": "",
        "plural": "",
        "part_of_speech": "",
        "example_sentence": "",
    }


def _german_section(wikitext):
    headings = list(LANGUAGE_HEADING.finditer(wikitext))
    for index, heading in enumerate(headings):
        if heading.group(1).strip() == "Deutsch":
            end = headings[index + 1].start() if index + 1 < len(headings) else len(wikitext)
            return wikitext[heading.end() : end]
    return None


def _first_wortart_section(section):
    """Return (heading line, body) of the first part-of-speech section."""
    headings = list(LEVEL_THREE_HEADING.finditer(section))
    for index, heading in enumerate(headings):
        if WORTART.search(heading.group(0)):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
            return heading.group(0), section[heading.end() : end]
    return "", ""


def _template_fields(body, name):
    """Return the `|key=value` parameters of one `{{name ...}}` invocation.

    Reads parameters whether they sit on the template's opening line
    (`{{name|Genus=n}}`) or each on their own line, stopping at the
    template's closing `}}`.
    """
    start = body.find("{{" + name)
    if start == -1:
        return {}
    end = body.find("}}", start)
    invocation = body[start:] if end == -1 else body[start:end]
    fields = {}
    for part in invocation.split("|")[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _clean_example(text):
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    previous = None
    while previous != text:
        previous = text
        text = TEMPLATE.sub("", text)
    text = LINK.sub(r"\1", text)
    text = text.replace("'''", "").replace("''", "")
    text = html.unescape(text).replace("\xa0", " ")
    return " ".join(text.split())


def _example_sentence(body):
    marker = body.find("{{Beispiele}}")
    if marker == -1:
        return ""
    plain, quoted = [], []
    for line in body[marker:].splitlines()[1:]:
        if not line.startswith(":"):
            break
        raw = EXAMPLE_PREFIX.sub("", line)
        cleaned = _clean_example(raw)
        if not cleaned or len(cleaned) > EXAMPLE_MAX_LENGTH:
            continue
        is_quotation = raw.lstrip().startswith("„") or "<ref" in raw
        (quoted if is_quotation else plain).append(cleaned)
    return (plain or quoted or [""])[0]


def parse_entry(title, wikitext):
    """Parse one page's wikitext into `DictionaryEntry` field values."""
    section = _german_section(wikitext)
    if section is None:
        return _empty_fields(MISSING)
    heading, body = _first_wortart_section(section)
    wortart = WORTART.search(heading)
    wortart_name = wortart.group(1).strip() if wortart else ""
    ordinary = wortart_name in WORTART_TO_PART_OF_SPEECH and not any(
        marker in heading for marker in NOT_ORDINARY_MARKERS
    )
    pointer = POINTER.search(section)
    if pointer and pointer.group(1).strip() != title and not ordinary:
        fields = _empty_fields(FOUND)
        base = pointer.group(1).strip()
        fields.update(lemma=base, base_form=base)
        return fields

    fields = _empty_fields(FOUND)
    fields["lemma"] = title
    fields["part_of_speech"] = WORTART_TO_PART_OF_SPEECH.get(wortart_name, "other")
    fields["example_sentence"] = _example_sentence(body)
    if fields["part_of_speech"] == "noun":
        overview = _template_fields(body, "Deutsch Substantiv Übersicht")
        genus = overview.get("Genus") or overview.get("Genus 1") or ""
        if not genus:
            heading_genus = HEADING_GENUS.search(heading)
            genus = heading_genus.group(1) if heading_genus else ""
        fields["article"] = GENUS_TO_ARTICLE.get(genus, "")
        plural = overview.get("Nominativ Plural") or overview.get("Nominativ Plural 1") or ""
        fields["plural"] = "" if plural in {"—", "-", "–"} else plural
    return fields


def _fetch(title, deadline):
    """Return (resolved title, wikitext), or None when the page does not exist."""
    endpoint = getattr(settings, "VIDEO_STUDY_DICTIONARY_ENDPOINT", "")
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
        logger.warning("Dictionary lookup for %r failed (status=%s): %s", title, status, exc)
        raise DictionaryUnavailable(str(exc)) from exc
    if not isinstance(payload, dict):
        logger.warning("Dictionary lookup for %r failed: unexpected response shape", title)
        raise DictionaryUnavailable("Unexpected response shape.")
    error = payload.get("error")
    if error is not None:
        if not isinstance(error, dict):
            logger.warning("Dictionary lookup for %r failed: unexpected response shape", title)
            raise DictionaryUnavailable("Unexpected response shape.")
        if error.get("code") in MISSING_PAGE_CODES:
            return None
        logger.warning("Dictionary lookup for %r failed: %s", title, error.get("code"))
        raise DictionaryUnavailable(str(error.get("code")))
    parse = payload.get("parse")
    if parse is None:
        parse = {}
    if not isinstance(parse, dict):
        logger.warning("Dictionary lookup for %r failed: unexpected response shape", title)
        raise DictionaryUnavailable("Unexpected response shape.")
    wikitext = parse.get("wikitext") or ""
    if not isinstance(wikitext, str):
        logger.warning("Dictionary lookup for %r failed: unexpected response shape", title)
        raise DictionaryUnavailable("Unexpected response shape.")
    return parse.get("title") or title, wikitext


def _page(title, deadline):
    entry = DictionaryEntry.objects.filter(headword=title).first()
    if entry is not None:
        return entry
    fetched = _fetch(title, deadline)
    fields = _empty_fields(MISSING) if fetched is None else parse_entry(*fetched)
    entry, _created = DictionaryEntry.objects.update_or_create(headword=title, defaults=fields)
    return entry


def _as_result(entry):
    return DictionaryResult(
        status=entry.status,
        lemma=entry.lemma,
        article=entry.article,
        plural=entry.plural,
        part_of_speech=entry.part_of_speech,
        example_sentence=entry.example_sentence,
    )


def _resolve(title, deadline):
    entry = _page(title, deadline)
    if entry.status != FOUND or not entry.base_form:
        return _as_result(entry)
    base = _page(entry.base_form, deadline)
    if base.status == FOUND and not base.base_form:
        return _as_result(base)
    return DictionaryResult(status=FOUND, lemma=entry.base_form)


def lookup_dictionary(candidate, surface_form, budget_start=None):
    """Confirm ``candidate`` on German Wiktionary, falling back to ``surface_form``."""
    start = time.monotonic() if budget_start is None else budget_start
    deadline = start + getattr(settings, "VIDEO_STUDY_TRANSLATION_BUDGET", 12)
    try:
        result = _resolve(candidate, deadline)
        if result.status == MISSING and surface_form and surface_form != candidate:
            result = _resolve(surface_form, deadline)
    except DictionaryUnavailable:
        return DictionaryResult(status=UNAVAILABLE)
    return result
