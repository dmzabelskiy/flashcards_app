"""What each source language offers, read from ``languages/<code>/pack.yml``.

Pure data: no model imports. The folder comes from ``settings.LANGUAGES_DIR``.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from django.conf import settings

PACK_FILE = "pack.yml"

_TOP_LEVEL_FIELDS = {
    "code",
    "name",
    "articles",
    "article_genders",
    "speech_locale",
    "media",
    "extra_letters",
}
_REQUIRED_FIELDS = _TOP_LEVEL_FIELDS - {"extra_letters"}
_MEDIA_FIELDS = {"video_channel", "podcast"}


class PackError(ValueError):
    """A pack.yml is malformed or incomplete; the message names file and field."""


@dataclass(frozen=True)
class LanguagePack:
    code: str
    name: str
    articles: tuple
    article_genders: tuple
    features: frozenset
    video_channel_key: str
    speech_locale: str
    extra_letters: tuple = ()


def _fail(path, field, problem):
    raise PackError(f"{path}: field '{field}' {problem}")


def _text(path, data, field):
    value = data[field]
    if not isinstance(value, str) or not value.strip():
        _fail(path, field, "must be a non-empty string")
    return value.strip()


def _text_list(path, value, field, *, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        _fail(path, field, "must be a list of non-empty strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _fail(path, field, "must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _check_keys(path, data, allowed, required, where=""):
    for key in data:
        if key not in allowed:
            _fail(path, f"{where}{key}", "is not a known field")
    for key in sorted(required - set(data)):
        _fail(path, f"{where}{key}", "is missing")


def load_pack(path, folder_code):
    """Read one pack.yml into a LanguagePack, or raise PackError."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PackError(f"{path}: cannot be read as YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise PackError(f"{path}: must contain a mapping of fields")
    _check_keys(path, data, _TOP_LEVEL_FIELDS, _REQUIRED_FIELDS)

    code = _text(path, data, "code").lower()
    if code != folder_code:
        _fail(path, "code", f"is '{code}' but the folder is '{folder_code}'")
    name = _text(path, data, "name")
    speech_locale = _text(path, data, "speech_locale")
    extra_letters = _text_list(
        path, data.get("extra_letters", []), "extra_letters", allow_empty=True
    )
    articles = _text_list(path, data["articles"], "articles", allow_empty=True)
    article_genders = _text_list(path, data["article_genders"], "article_genders", allow_empty=True)
    if len(article_genders) != len(articles):
        _fail(path, "article_genders", "must have one entry per article")

    media = data["media"]
    if not isinstance(media, dict):
        _fail(path, "media", "must be a mapping")
    _check_keys(path, media, _MEDIA_FIELDS, _MEDIA_FIELDS, where="media.")
    video_channel = media["video_channel"]
    if video_channel is not None and (
        not isinstance(video_channel, str) or not video_channel.strip()
    ):
        _fail(path, "media.video_channel", "must be a channel key or null")
    video_channel = (video_channel or "").strip()
    podcast = media["podcast"]
    if not isinstance(podcast, bool):
        _fail(path, "media.podcast", "must be true or false")

    features = set()
    if video_channel:
        features.add("video")
    if podcast:
        features.add("podcast")
    if articles:
        features.add("article_drill")

    return LanguagePack(
        code=code,
        name=name,
        articles=articles,
        article_genders=article_genders,
        features=frozenset(features),
        video_channel_key=video_channel,
        speech_locale=speech_locale,
        extra_letters=extra_letters,
    )


def load_packs(directory):
    """Load every ``<directory>/<code>/pack.yml`` into a {code: LanguagePack} map."""
    directory = Path(directory)
    if not directory.is_dir():
        raise PackError(f"{directory}: language folder does not exist")
    packs = {}
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()):
        pack_path = folder / PACK_FILE
        if not pack_path.is_file():
            raise PackError(f"{pack_path}: file is missing")
        packs[folder.name] = load_pack(pack_path, folder.name)
    return packs


PACKS = load_packs(settings.LANGUAGES_DIR)
for _required_code in ("de", "nl"):
    if _required_code not in PACKS:
        raise PackError(f"{settings.LANGUAGES_DIR / _required_code}: language folder is missing")
GERMAN = PACKS["de"]
DUTCH = PACKS["nl"]


def pack_for_source(code):
    return PACKS.get(str(code or "").lower(), GERMAN)


def pack_for_mode(mode):
    return pack_for_source(str(mode or "").split("-", 1)[0])
