# ES-RU, bundle 1: design

Spanish for a Russian speaker, as a fourth study mode.

## Intent

The owner is at A1–A2 in Spanish and knows some grammar. The goal is
vocabulary: first from a basic word list, then from video. The German modes
serve B1–B2 and assume the base; this mode has to teach it. Grammar drills
support the vocabulary work and stay few and broad.

## Goals

1. `ES-RU` is a selectable mode with its own decks, cards, daily plan and
   drills page.
2. A word list of about 1,000 A1–A2 words can be imported into an `ES-RU`
   deck, and its nouns feed the gender drill.
3. Video study works with Spanish captions, with Dreaming Spanish as the
   channel of the day, and a clicked word is captured as lemma plus Russian
   translation into the global deck.
4. The mode offers four trainers: Gender (built in), Verb forms, Verbs in
   context, Small words.

## Non-goals

- A Spanish podcast. The podcast code is tied to one German feed; the pack
  sets `podcast: false`.
- A pack without a video channel. That constraint stays as documented.
- Dictionary data for captured Spanish words (gender, plural, part of
  speech). A captured noun has no article until the owner sets one on the
  card.
- A sub-topic filter inside a trainer, an on-screen accent keypad, a second
  target language for Spanish, the product name and the "G" mark.

## Anti-goals

- Content copied from a textbook, a course or an exam publisher's list.
- A changed `trainer` name or item `id` after the files are merged.
- A test that reaches the network, or a loosened network guard.
- Any change that alters stored German or Dutch progress.

## Decisions

The owner decided: the mode and its goal, Castilian Spanish as the
standard, Dreaming Spanish as the first channel, four trainers at most with
broad scope, a video of the day that does not repeat once it is marked
studied, and the package and review budget.

The lead decided the following; the owner can flip any of them.

- **Accents are strict.** `hablo` and `habló` are different answers.
- **What Castilian means here.** Verb forms include `vosotros`; the word
  list uses the Peninsular word and notes a common Latin American one
  (`LatAm: el carro`). With `hoy` or `esta semana` the expected past tense
  is the perfecto, with `ayer` the indefinido.
- **The channel draws on two playlists**, Beginner and the beginner podcast
  ("Chats in Beginner Spanish"), not on the whole channel, whose feed mixes
  every level and Shorts. The podcast playlist gains an episode a week.
- **"Studied" is the existing mark**, "Mark media done for today". Only one
  mark is stored per day, so a second video marked on the same day can come
  up again.
- **The no-repeat rule holds in every mode**, Kurzgesagt and the Dutch
  channel included, per account and mode.
- **Spanish mode offers Spanish captions only.**
- **The CSV importer sets a card's article from its front, in every mode.**
  Today it never does, so an imported noun is invisible to the article
  drill. This also applies to future German and Dutch imports; existing
  cards are not touched.
- **The word list goes into the `ES-RU` global deck.** The daily plan's
  study task draws only on the global deck, and captured words land there
  too.
- **Explanations are in Russian, the page text in English.** Drill labels,
  instructions, hints and reveal labels are English, as in the German
  drills; the "why" line and the sentence translation are Russian. Card
  `notes` are English.
- **Captions on Dreaming Spanish are auto-generated.** Eight sampled videos
  (Beginner, Superbeginner, podcast) each had one Spanish track, generated;
  the app already accepts generated tracks.

## The mode and the pack

- `Deck.LanguageMode` gains `ES_RU = "ES-RU", "Russian"`.
- `Card.Article` gains `el` and `la`.
- Choices-only migrations follow in `decks`, `cards`, `reviews` and `core`
  (every model with a mode or article choice list).
- `languages/es/pack.yml`: `code: es`, `name: Spanish`,
  `articles: [el, la]`, `article_genders: [Masculine, Feminine]`,
  `speech_locale: es-ES`, `media.video_channel: dreaming-spanish`,
  `media.podcast: false`.
- Article colours: `el` takes the colour of `der`, `la` the colour of `die`
  (`static/css/app.css`, next to the Dutch rules).

## The importer

`imports_app/services.py` sets `article` on a row when all of these hold
(`cards.articles.parse_leading_article` already takes a pack's list):

- the card type is `vocabulary`;
- the front starts with one of the articles of the deck's pack, written in
  lower case, followed by a space and at least one more word
  (`la tarjeta de crédito` counts);
- the front does not end in `.`, `!` or `?`.

So `Die Katze schläft.` stays a card without an article. The front is stored
unchanged, as the seeded German nouns are. `docs/csv_format.md` says so.

## Text that names German

Goal 1 covers the screens the mode uses. Text that names German or
Kurzgesagt on those screens follows the mode instead: "→ Deutsch" and "Type
the German word" in `templates/reviews/study_card.html`, the caption hint and
the empty state in `templates/video_study/workspace.html`, and any other
found the same way. The product name "German Flashcards" stays.

## Answer check `spanish`

A sixth normaliser in `reviews/normalisers.py`: Unicode NFC, trim, casefold,
`¿ ¡ . , ; : ! ?` removed at both ends, runs of whitespace collapsed. Accents,
`ñ` and `ü` are significant. `docs/language_packs.md` and the test that pins
the set of names are updated.

## Video

- A `MediaChannel` `dreaming-spanish`, name "Dreaming Spanish", source
  language `es`, with two feeds:
  `https://www.youtube.com/feeds/videos.xml?playlist_id=PLlpPf-YgbU7HWrrenMs3-nuhxgzyAiA-C`
  (Beginner) and
  `https://www.youtube.com/feeds/videos.xml?playlist_id=PLlpPf-YgbU7HDyS2Ic86G7tc1I0Tpr2Ty`
  (beginner podcast). A channel may list more than one feed; the German and
  Dutch channels keep one. A playlist feed has the same Atom shape as a
  channel feed and lists 15 videos; the catalog accumulates across
  refreshes. One feed failing does not lose the other's videos.
- `refresh_media_catalogs` refreshes this channel too. It lists its channels
  by hand today.
- The workspace offers `es` in `ES-RU` and nothing else; the German modes
  keep `de` and `en`, Dutch keeps `nl`. Today a pack code outside the form's
  choices raises `KeyError` in `video_study/views.py`. Text that says
  "German" in the non-Dutch branch of
  `templates/video_study/workspace.html` becomes mode-neutral or
  mode-specific.
- Bilingual captions stay Dutch-only.

## A studied video does not come back

- A video counts as studied by an account in a mode when a
  `DailyTaskCompletion` of task `media` for that owner and mode has the
  label `video:<youtube id>`. No new table or migration.
- The video of the day is chosen from the channel's catalog without the
  videos studied before today. Today's own mark does not change today's
  video, so the dashboard keeps showing the video just finished.
- When every video of the catalog is studied, there is no video of the day:
  the dashboard and the daily plan say so and point to video study, where
  any video can be loaded by URL. The media task can still be marked done
  from such a video.
- The choice is per account and mode, so the dashboard and the daily plan
  pass the user. Background transcript prefetch stays per channel.
- The podcast of the day is unchanged.

## Word capture

- `video_study/es/` mirrors `video_study/nl/` in shape: a lemmatiser wrapper
  (`simplemma`, `lang="es"`; verified here: `tengo → tener`,
  `fuimos → ir`, `niñas → niño`) and a resolver returning a
  `CaptureResolution` with the lemma, the example sentence, and empty
  article, plural and part of speech. The existing word patterns already
  admit accented letters and `ñ`.
- A selection of several words comes back as a `phrase`, trustworthy and
  unlemmatised, as in Dutch. With no `es.dic` the token comes back
  unchanged and untrustworthy, so saving answers 422, as in Dutch.
- The translation is the provider chain's answer for the lemma, source `es`,
  target `ru`. Only the selected text goes to a provider, as today. Whether
  Russian Wiktionary marks Spanish sections the way the provider expects
  (`{{-es-}}`) is not verified; the other providers follow in the chain.
- The two places that choose a resolver by `"nl"` or else
  (`video_study/views.py`, `cards/global_views.py`) choose by source
  language instead, and so do their guards that the source language fits
  the mode (`(source_language == "nl") != …`).
- Saving treats a list card as the same word: when a resolution has neither
  an article nor a part of speech (every Spanish word today), a card in the
  global deck whose front is a pack article plus the lemma (`el coche` for
  `coche`) counts as existing. German and Dutch saving is unchanged.
- The lemma cache gains `es`: `build_lemma_cache`, the test cache helper and
  the fail-closed behaviour work as for `de` and `nl`. The Docker build
  produces `es.dic`.

## Word list

`card_sets/ES-RU/spanish_a1_a2_vocabulary.csv`, about 1,000 rows, columns as
in the other card sets.

- `front`: a noun with its article (`el coche`), a verb as infinitive
  (`levantarse`), an adjective in the masculine singular, or a short fixed
  phrase (`por favor`). A front that is both a verb and a noun is the verb.
  A feminine noun that takes `el` (`agua`, `hambre`) and a noun of both
  genders (`estudiante`) are written without an article, with the detail in
  `notes`, so the gender drill never shows a misleading label.
- `back`: one to three Russian glosses, separated by `; `.
- `example_sentence`: one original Spanish sentence at A1–A2 level.
- `notes`, in English: `A1` or `A2`, then what is irregular
  (`irregular: tengo, tuve`, a feminine form, a plural) and a Latin American
  alternative.
- `tags`: `spanish`, the level, one theme.
- The selection is the author's own, by theme. One public word-frequency
  list (OpenSubtitles-derived, `hermitdave/FrequencyWords`, `es_50k.txt`)
  may be downloaded once into `./temp/` as a coverage check: which common
  lemmas are missing. Nothing from it is copied into the repository.
- The owner imports it into the `ES-RU` global deck on the import page.
- A test imports the file into an `ES-RU` global deck: no errors, at least
  900 rows, unique fronts, and every front that starts with `el ` or `la `
  has that article set.

## Trainers

Three files in `languages/es/drills/`, each with `targets: [ru]`,
`round_size: 20` and one typed field with `normalise: spanish`. Sentences
are original.

| File | `trainer` | Items | Prompt | Shown after the answer |
|---|---|---|---|---|
| `verb-forms.yml` | `es_verb_form` | about 300 | `tener · presente · yo` | Russian gloss of the verb |
| `verbs-in-context.yml` | `es_verb_context` | about 200 | `Madrid ___ en España. (ser / estar)` | the full sentence, its Russian translation, one line on why |
| `small-words.yml` | `es_small_word` | about 200 | `Pienso ___ ti.` | the full sentence, its Russian translation, one line on why |

- **Verb forms:** presente (three regular models in full; stem-changing,
  irregular `yo` and fully irregular verbs in their telling persons;
  one reflexive model), indefinido (regular models, the irregular stems,
  third-person changes, `-qué`/`-gué`/`-cé`), imperfecto (models and `ser`,
  `ir`, `ver`), irregular participles, irregular gerunds. Item ids are
  `<verb>-<tense>-<person>`.
- **Verbs in context:** ser / estar / hay; indefinido / imperfecto /
  perfecto; `gustar`-type verbs; pairs such as saber / conocer. The cue in
  brackets names the verb or the choice, and the person when the sentence
  does not show it.
- **Small words:** por / para; the preposition after a verb or an
  adjective and of place and time; `al` / `del`; personal `a`; object
  pronouns; muy / mucho; también / tampoco; algo / nada, alguien / nadie.
  Every gap takes a non-empty answer.
- A gap has one correct answer given its cue, or every correct answer is
  accepted. Item ids start with the topic (`ser-estar-…`, `por-para-…`).
- What is shown after the answer uses `example` for the full sentence and
  `reveal` with English labels and Russian values:
  `{Translation: Мадрид находится в Испании., Why: местоположение → estar}`;
  for a verb form, `{Translation: иметь}`.
- The drill page shows its prompt in a display size made for one word. A
  sentence-length prompt gets a smaller, readable style within `DESIGN.md`,
  and the feedback line stays readable with the two reveal lines.

## Documents

`AGENTS.md`, `README.md`, `PRODUCT.md` where it lists modes,
`docs/language_packs.md`, `docs/video_study.md`, `docs/csv_format.md` and
`docs/roadmap.md` describe the mode once it is merged; the roadmap keeps the
podcast, the pack without a channel and dictionary-based capture as later
work. This spec and its plan are deleted then.

## Packages

1. **Code** (Sonnet 5.5, `high`): everything above except the word list and
   the three trainer files.
2. **Word list** (Sonnet 5.5, `high`), on top of 1.
3. **Trainer files** (Sonnet 5.5, `high`), on top of 1, in parallel with 2.

One review of the bundle by Codex Sol 6.1 at `xhigh`, covering the code and
the Spanish and Russian content; one fresh Sonnet 5.5 `high` worker for the
fixes. The lead runs the full gate, imports the word list into a scratch
database, and checks the gender drill, a sentence drill and the video
workspace in an isolated browser session against a local server.

Workers commit on `agent/es-ru-*` branches. The lead merges into local
`main`; nothing is pushed or deployed without the owner's request.

## Left to the builder

The layout inside `video_study/es/`, how the refresh command lists its
channels, the CSS of the long prompt, the themes of the word list, which
verbs and persons make up the verb-form items, and the pictograms (from the
existing set).
