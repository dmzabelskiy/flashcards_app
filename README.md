# German Flashcards

A multi-user Django app for studying German vocabulary and grammar in the
browser, with smaller Dutch and Spanish modes. Each account owns its own decks, cards and
progress. It is deliberately not an Anki clone: the focus is clear code,
CSV import and a practical daily study routine.

## Features

- Decks and cards: create, edit, browse, search, suspend and delete; import
  and export CSV.
- Spaced review of due cards with a simple scheduler.
- Typed study: shuffled, with several accepted translations and typo
  tolerance, forwards or reversed.
- A daily goal of 20 correct words, with optional practice of every word
  afterwards.
- Drills: the der/die/das article drill, adjective + preposition,
  verb + preposition, and irregular verbs (Russian only). Each has a
  flashcard mode. Three correct answers in a row on an item offer an optional
  Retire action; nothing retires automatically.
- Video study: YouTube and podcast transcripts with click-to-translate and
  word capture into a global deck. A video studied on an earlier day is not
  suggested again.
- Four language modes, chosen in the top bar on every page: German → Russian,
  German → Spanish, Dutch → English and Spanish → Russian. Decks, cards, stats, imports, daily
  plan and translations follow the active mode.
- Dashboard with the daily plan, streaks, activity heatmap and retention.

## Accounts

There is no public signup. A superuser creates accounts in Django admin
(`/admin/`; leave "Staff status" and "Superuser status" unchecked for a normal
user). Every page except the login screen and `/healthz` requires a login by
username (`LoginRequiredMiddleware`). Accounts share only the global,
non-personal caches of transcripts, translations and dictionary entries.

## Quick start

```bash
git clone <repository-url>
cd <repository>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser    # every page needs a login
python manage.py runserver
```

Open `http://127.0.0.1:8000/` and sign in. `requirements.lock` is the pinned,
hashed set used by Docker and CI; `AGENTS.md` has the command that regenerates
it.

## Tests

```bash
python manage.py test --parallel 4
node --test static/js/tests/*.test.mjs
uvx ruff@0.14.14 check . && uvx ruff@0.14.14 format --check .
```

Tests never use the network; the test runner fails any test that tries.

## Using the app

**Review.** `Study words` in the sidebar shows a due card. `Space` shows the
answer; `1` Again, `2` Hard, `3` Good, `4` Easy.

**Study.** `Start studying` on the dashboard begins typed study. Type the
translation and press `Enter`; `I was right` overrides a wrong mark. `Reverse`
next to it shows the translation and asks for the word in the language studied
(in German, with or without its article and with umlauts spelled either way); `Study reversed` on a deck
page does the same for that deck. Answers ignore case and whitespace, accept
several translations (`;`, comma, slash or newline) and tolerate one wrong
letter or one swapped pair. After the daily goal the dashboard button reads
`Practice all words` and practises every active word of the mode's global
deck, not only 20.

**Drills.** The article drill asks a curated set of 100 B1–B2 nouns when your
account holds it and your own noun cards otherwise; in German → Russian mode it
adds the set to the global deck itself (`seed_article_cards --username
<username>` does so explicitly). The other drills are data files under
`languages/<code>/drills/`; the pool rules are in
[docs/language_packs.md](docs/language_packs.md).

**Video study.** Load a YouTube video or choose a podcast episode, then click
words or select phrases to translate them into the mode's target language. A
German word is reduced to its dictionary form with an offline lemmatiser and
checked against German Wiktionary for article, plural and an example before it
is translated and saved as a finished card. Dutch uses English Wiktionary.
Spanish has no dictionary behind it: the word is reduced to its lemma offline
and translated, and a captured noun has no article until you set one on the
card. In Spanish → Russian the daily video comes from Spanish After Hours.
Translations are cached globally per language pair. Providers, captions,
proxy and prefetch settings: [docs/video_study.md](docs/video_study.md).

## CSV import

Required columns `front,back`; optional `example_sentence,notes,tags,card_type`.
The importer strips whitespace, skips empty rows, reports row-level errors and
creates cards as due immediately. A row whose front is already in the deck, or
earlier in the file, is skipped and counted. A vocabulary card whose front starts with a
lower-case article of the language (`der Alltag`, `el coche`) is stored with
that article set, so the article drill can use it. Format and `card_type`
values:
[docs/csv_format.md](docs/csv_format.md).

Starter sets are in `card_sets/<mode>/`: `card_sets/DE-RU/german_test.csv` is
a 100-word German verb list, and `card_sets/ES-RU/spanish_a1_a2_vocabulary.csv`
is about 1,000 Castilian Spanish words and phrases at A1–A2 with Russian
glosses, to import into the `ES-RU` global deck.

## Deployment

The app ships as a Docker image (`Dockerfile`) and runs on one Fly.io machine
configured in `fly.toml`, with SQLite on a Fly volume at `/data/db.sqlite3`.
`scripts/docker-entrypoint.sh` takes a backup, runs `migrate`, starts the
backup timer and serves with gunicorn and WhiteNoise.

1. `fly apps create <app>` and set `app` in `fly.toml`.
2. `fly volumes create flashcards_data --region <region>`.
3. `fly secrets set DJANGO_SECRET_KEY=... DJANGO_ALLOWED_HOSTS=<host>
   DJANGO_CSRF_TRUSTED_ORIGINS=https://<host>`. With `DJANGO_DEBUG=false` the
   app refuses to start without a secret key of at least 50 characters.
4. `fly deploy --ha=false`.

Run exactly one machine: SQLite has one writer. `/healthz` returns no user
data and is meant for an external uptime monitor; see
[docs/project-memory.md](docs/project-memory.md) for why it is not a Fly
health check. Optional settings are listed in `.env.production.example`.

## Backups

A verified SQLite snapshot is taken at every boot and every 24 hours. An
optional off-site copy to an S3-compatible bucket is off until
`BACKUP_REMOTE_ENABLED=true`. Setup, retention and the restore drill:
[docs/backups.md](docs/backups.md).

## Project layout

```text
config/        Django settings and URL root
core/          dashboard, stats, language modes and packs, backups
decks/         decks
cards/         cards, browsing, export
reviews/       review, study, drills, scheduler, daily plan
imports_app/   CSV import
video_study/   video and podcast study, translation cache, word capture
languages/     one folder per studied language: pack.yml and drills/*.yml
card_sets/     starter CSV card sets
templates/     Django templates
static/        CSS, JavaScript, fonts
docs/          guides and reference notes
scripts/       container entrypoint and production start script
```

## Documents

- [docs/language_packs.md](docs/language_packs.md): language folders and drill files
- [docs/video_study.md](docs/video_study.md): video study providers and settings
- [docs/backups.md](docs/backups.md): backups and restore
- [docs/csv_format.md](docs/csv_format.md): CSV import format
- [docs/scheduler.md](docs/scheduler.md): the review scheduler
- [docs/project-memory.md](docs/project-memory.md): technical notes
- [docs/roadmap.md](docs/roadmap.md): what is next
- [DESIGN.md](DESIGN.md) and [PRODUCT.md](PRODUCT.md): design system and product notes
- [AGENTS.md](AGENTS.md): guide for coding agents

## Limitations

- SQLite on one machine: not horizontally scalable.
- No self-service password reset or email verification.
- Text-only cards, no media; CSV only, no Anki `.apkg` import.
- A simple scheduler, not FSRS.

## Licence and credits

MIT, see [LICENSE](LICENSE). The Archivo font in `static/fonts/` is used under
the SIL Open Font License (`static/fonts/OFL.txt`).
