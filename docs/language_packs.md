# Language packs and drills

Each studied language is a folder under `languages/<code>/`. What a study mode
offers follows from its files, so a new drill is one data file.
`core/language_packs.py` loads the packs and `reviews/drills.py` the drills.
A malformed file fails at start-up with a message naming the file and field.

```text
languages/<code>/
  pack.yml        settings of the language
  drills/*.yml    one file per drill (optional)
```

A study mode is `<source>-<target>`, such as `DE-RU`: the language studied,
then the language of the translations. The source picks the pack, the target
picks the answers of a drill. The packs `de` and `nl` must exist.

## pack.yml

Every field is required; unknown fields are rejected.

- `code`: equals the folder name.
- `name`: language name, shown as "German → Russian" in the mode list.
- `articles`: the articles in order (`[der, die, das]`), or `[]`.
- `article_genders`: one gender label per article. Both lists are the choices
  for a card's article.
- `speech_locale`: locale of the speech button on card faces (`de-DE`).
- `media.video_channel`: a key of `MEDIA_CHANNELS` in
  `video_study/services.py`, or `null`.
- `media.podcast`: `true` or `false`.

Features derive from these: video from a channel, podcast from
`media.podcast`, the article drill from a non-empty `articles`.

## Drill files

Required keys: `slug`, `trainer`, `label`, `description`, `fields`, `items`.

- `slug`: lowercase words joined by hyphens, unique, not `articles`. The pages
  are at `/reviews/drill/<slug>/`.
- `trainer`: the name stored with attempts and progress; a lowercase
  identifier of at most 32 characters, unique, and not a built-in or legacy
  name.
- `label`, `description`: shown on the drills page.
- `order`: position within the pack (default 100). The article drill is first.
- `round_size`: items per round (default all).
- `targets`: target codes that offer the drill (`[ru]`); default every mode of
  the pack.
- `instruction`, `hint`: text on the drill page.
- `pictogram`: a symbol of `templates/core/_pictograms.html`, without `pic-`
  (default `link`).

`{target_label}` in `instruction`, `description` and a field's `label`, `hint`
and `placeholder` becomes the mode's target language ("Russian").

### Fields

A field has `key` (a lowercase identifier, not `example`, `reveal` or
`alternatives`), `label` and `kind`, and may have `hint` and `placeholder`.

- `typed`: a text input; `normalise` is required.
- `choice`: radio buttons from `options` (at least two, each a string or
  `{value, label}`). Item answers are option values.
- `pool_choice`: buttons built from the answers of the whole drill: the item's
  own answers plus `distractors` others (default 2; an integer of at least 1).

`normalise` says how typed text and accepted answers are compared; `choice`
and `pool_choice` fields default to `exact`. All of them trim and casefold.

- `exact`: nothing else.
- `german`: `ä ö ü ß` equal `ae oe ue ss`; the round brackets `(` and `)` are
  dropped and the text inside stays; runs of whitespace collapse to one space.
  Square and curly brackets are kept.
- `russian`: `ё` equals `е`; the characters `. , ; : ! ?` are removed at both
  ends; runs of whitespace collapse.
- `translation`: `ё` equals `е`; accents are removed; every punctuation
  character becomes a space; runs of whitespace collapse.
- `preposition`: `ae oe ue` become `ä ö ü`, `ß` becomes `ss`, then accents are
  removed, so `ä` and `ae` both give `a`. Inner whitespace is kept.

### Items

An item has `id` (unique in the file, at most 200 characters), `prompt` and
`alternatives`, a non-empty list. An alternative lists the accepted answers of
every field, each a non-empty list of non-empty strings; the learner's answers
must all match within one alternative. It may also have `example` (a sentence
shown after the answer) and `reveal` (a map of label to value, shown after the
answer and never graded).

An answer list may be split by target: `translation: {ru: [...], es: [...]}`.
An item is asked in a mode only when each field has an answer for its target.
`languages/de/drills/adjective-prepositions.yml` shows several alternatives;
`languages/de/drills/verb-prepositions.yml` shows per-target answers and
`reveal`.

### What the loader rejects

A file fails to load when:

- it is not a mapping, or a drill, field, item or alternative has a key that
  is not listed here, or lacks a required one;
- `fields` or `items` is empty, or two fields share a `key`;
- `order` is not an integer, or `round_size` is not a positive integer;
- `targets` is not a non-empty list of strings;
- `normalise` is missing on a `typed` field or is not one of the five names;
- `options` is set on a field that is not `choice`, or has fewer than two
  entries or a repeated value; `distractors` is set on a field that is not
  `pool_choice`;
- an answer of a `choice` field is not one of its option values, or an answer
  map is empty;
- an item `id` repeats, or two drills of different packs share a `slug` or a
  `trainer`.

## The article drill

Built in for every pack with articles, and always listed first. It draws on
the user's own active noun cards in the mode's decks whose `article` is one of
the pack's articles (`reviews/article_cards.py`):

- Cards tagged `article-drill-b1-b2` (`ARTICLE_DRILL_TAG`) are the pool
  whenever any exist, and the drill then asks only those. In `DE-RU` the set
  is `card_sets/DE-RU/german_b1_b2_nouns.csv`; the drill seeds it into the
  account's `DE-RU` global deck whenever that deck exists.
- Without tagged cards, the pool is all those noun cards.
- In `DE-RU` with no such cards, the drill creates the global deck if needed
  and seeds the tagged set into it.

## Names that never change

Progress is keyed on the `trainer` name and the item `id` (`TrainerAttempt`,
`DrillItemProgress`). Changing either orphans stored history. Prompts and
answers may be edited; a new item gets a new `id`. The legacy values of
`TrainerAttempt.Trainer` stay reserved.

## Add a drill

Add `languages/<code>/drills/<slug>.yml` and run the tests. No code or
migration changes.

## Add a language

1. Create `languages/<code>/pack.yml`, and `drills/` if it has drills. A video
   channel needs a `MediaChannel` in `video_study/services.py`.
2. Add one entry to `Deck.LanguageMode` in `decks/models.py`, for example
   `ES_RU = "ES-RU", "Russian"`: the value is `<SOURCE>-<TARGET>`, the label
   the target language.
3. If the pack has articles that `Card.Article` in `cards/models.py` lacks,
   add them there. A card's `article` is a fixed choice list (today `der`,
   `die`, `das`, `de`, `het`), and each article is at most three characters.
4. Run `python3 manage.py makemigrations`. The migrations change the `choices`
   of the mode fields and of `Card.article`.

Two constraints hold today:

- Every pack needs a video channel. `video_study/services.py` looks up
  `MEDIA_CHANNELS[pack.video_channel_key]` without a check, and
  `core/views.py` and `reviews/daily_plan.py` call it, so a pack with
  `media.video_channel: null` breaks the dashboard.
- Card articles come only from `Card.Article`, as step 3 says.

## Tests

A new file is checked without a new test. `core/test_language_pack_loader.py`
and `reviews/test_drill_files.py` cover the format.
`reviews/test_drill_contract.py` loads every file and checks unique slugs and
trainers, an existing pictogram, an askable item in each offering mode, that
every accepted answer grades correct and a wrong one does not, and that the
pages render. `core/test_language_packs.py` checks that each pack's video
channel exists.
