# Project memory

Technical notes that are not obvious from the code. Guides for backups, video
study and language packs are in their own files under `docs/`.

## Language-neutral core

- Packs and drills are data under `languages/<code>/`; the reference is
  [language_packs.md](language_packs.md).
- The test runner (`config/test_runner.py`) blocks outbound connections and
  name lookups in the test processes and fails the test that tried one, even
  when the code under test swallowed the error. It cannot see subprocesses.
  It also forces `VIDEO_STUDY_TRANSCRIPT_PREFETCH` and
  `VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH` off, so a dashboard render or an
  empty catalog never fetches anything.
- `TrainerAttempt.trainer` and `DrillItemProgress.trainer` have no fixed
  choices, so a new drill needs no migration. `TrainerAttempt.trainer_label`
  resolves the label from the drill registry, then the legacy enum values,
  then the stored name.
- Some enum values in `TrainerAttempt.Trainer` and
  `DailyTaskCompletion.Task` are legacy values kept so stored history loads.
  `RESERVED_TRAINERS` in `reviews/drills.py` keeps new drills from reusing
  them.
- A data-drill round in the session carries `round_format = 2`. A round
  without it is cleared and restarted; stored progress is unaffected.
- The daily plan is study, drill and media (`reviews/daily_plan.py`).

## Deployment

- One Fly.io machine (region, size and volume in `fly.toml`) with the SQLite
  database on a volume at `/data/db.sqlite3`. Deploy with
  `fly deploy --ha=false --update-only` to keep the single machine.
- `scripts/docker-entrypoint.sh` takes a backup, checks the lemma cache, runs
  migrations, starts the backup timer and a catalog refresh in the
  background, then starts gunicorn.
- `auto_stop_machines = "suspend"` with `min_machines_running = 0`: Fly
  suspends an idle machine and resumes it in well under a second, keeping
  memory and skipping the entrypoint. Fly falls back to a plain stop when a
  machine is ineligible for suspension (the limit is 2 GiB of memory; this
  machine has 256 MB). A stopped machine boots through the full entrypoint, so
  if wake-ups are slow, check `fly status` for `stopped` versus `suspended`.

## Learning features

- In German → Russian mode the article drill prefers a curated set of 100
  B1–B2 nouns (34 `der`, 33 `die`, 33 `das`) from
  `card_sets/DE-RU/german_b1_b2_nouns.csv`; the pool rules are in
  [language_packs.md](language_packs.md). `python manage.py seed_article_cards
  --username <username>` adds the set explicitly.
- The Drills hub offers a Flashcards action for every drill. Flashcards
  shuffle the pool, reveal answers on demand and write no scores or review
  records.
- `DrillItemProgress` has one row per user, trainer and item key: the card pk
  for articles, the item `id` for data drills. Three correct answers in a row
  offer a Retire button, and a retired item leaves both the drill and its
  flashcards.
- A drill step page extends `templates/reviews/_drill_page.html` and fills the
  `prompt`, `answer_form` and `feedback` blocks; the completion pages share
  `templates/reviews/_drill_complete.html`.
- Card faces render `Card.display_front`, which prepends `article` to `front`
  unless `front` already starts with it, so a front that carries its article
  as text never renders as "das das Unternehmen". `add_to_global_deck` also
  strips a leading article that repeats the `article` field before saving.

## Podcast study save flow

The podcast workspace (`templates/podcasts/workspace.html`) shares the video
workspace's "Add to global deck" flow. `static/js/video_global_deck.js` finds
its root with `document.querySelector("[data-add-to-global-url]")` instead of
an id, so it serves `#video-study-app` and `#podcast-study-app`.
`static/js/podcast_study.js` mirrors `applyTranslationResult` of
`static/js/video_study.js` to fill the grammar, gender and plural fields and reveal the
add button.

## Transcript prefetch

`video_study.services.prefetch_channel_transcripts` caches captions for a
bounded number of uncached catalog videos per run. The dashboard view starts it
in a daemon thread at most once per local day per process, and
`refresh_media_catalogs` runs it at boot. Both are gated by
`VIDEO_STUDY_TRANSCRIPT_PREFETCH`. The daily-plan link keeps `?autofetch=1`, so
a video the prefetch missed still fetches when opened. Settings and the proxy
are in [video_study.md](video_study.md); search the logs for "prefetch
summary".

## Word capture

- `video_study.normalization.resolve_capture` produces the finished card saved
  when a transcript word is added to the global deck. It runs the offline
  lemmatiser (`suggest_word_metadata`, which joins a separable verb's
  particle when the clause ends in one: "steht ... auf" → "aufstehen"), then
  confirms the candidate against German Wiktionary through
  `video_study/dictionary.py` (`lookup_dictionary`). That reads article,
  plural, part of speech and an example sentence, and caches parsed pages in
  the shared, non-personal `DictionaryEntry` model.
- `translate_word` (the preview) and `add_to_global_deck` (the save) both call
  the resolver, so the panel shows exactly what is saved. New cards carry no
  transcript sentence, `tags`, `source_url` or `notes`; `example_sentence` is
  the dictionary's example, or empty.
- The save is refused with HTTP 422 only when the capture is not trustworthy:
  the dictionary did not return `found` and the lemma cache is unavailable
  (`lemmatizer_available` is false). Either source alone is enough.
- `VIDEO_STUDY_DICTIONARY_ENDPOINT=""` disables dictionary lookups: every call
  returns `unavailable` without a request. Tests stub the lookup.
- Dutch capture (`video_study/nl/`) is parallel machinery, not a
  generalisation of the German one: `resolve_capture_nl` reads English
  Wiktionary through `VIDEO_STUDY_NL_DICTIONARY_ENDPOINT` and caches pages in
  the separate, non-personal `NlDictionaryEntry` model. A found entry's
  glosses become the translation directly (`WordTranslation` provider
  `wiktionary-en`), skipping the provider chain that Dutch capture otherwise
  falls back to.

## Offline lemmatiser

- Word capture reduces the selection to its dictionary lemma with `simplemma`
  (`video_study.metadata.lemmatize_german`). The lemma, not the surface form,
  is translated and cached.
- `simplemma` lists many weak past participles as headwords of their own (the
  adjective reading: "betäubt", "enttäuscht"), so they come back unchanged.
  `suggest_word_metadata` rebuilds the infinitive ("betäuben") and uses it when
  the dictionary knows that form and the sentence puts the word where only a
  verb fits (after a noun, an object pronoun or a form of haben/werden). After
  sein ("ist verrückt") the participle is kept, as that is usually the
  adjective.

## Lemma cache

- `simplemma`'s default dictionary factory loads the German dictionary as a
  plain Python dict, about 150 MB resident. On the 256 MB production machine
  that gets the process OOM-killed.
- A prebuilt marisa-trie dictionary (`simplemma[marisa-trie]`) loads in about
  7 MB, but building it peaks at about 350 MB, so it is built at Docker image
  build time, never on the machine.
- `video_study.metadata.get_lemmatizer()` builds a module-level
  `simplemma.Lemmatizer` from `settings.LEMMA_CACHE_DIR` (default
  `<repo>/lemma_cache/`, `/app/lemma_cache` in the image).
  `LEMMA_CACHE_BUILD_ALLOWED` (default true when `DEBUG`, false otherwise)
  picks the factory. When true, the stock `TrieDictionaryFactory` builds the
  trie in-process on a cache miss. When false (set in the `Dockerfile`),
  `PrebuiltTrieDictionaryFactory` only loads `<lang>.dic` (`de.dic`, `nl.dic`)
  and raises `LemmaCacheUnavailable` on a missing or corrupt file, instead of
  building or racing concurrent first requests on writing it.
  `lemmatize_german` catches that, logs a warning once per process and returns
  the token unchanged, so capture degrades instead of crashing.
- `python manage.py build_lemma_cache` builds both tries into
  `LEMMA_CACHE_DIR` regardless of `LEMMA_CACHE_BUILD_ALLOWED`. `--check` loads
  each through `PrebuiltTrieDictionaryFactory` and does one lookup per
  language, so a corrupt file fails too. The `Dockerfile` runs the build and
  then `--check`; the entrypoint runs `--check` before `migrate` and only
  warns if the cache is missing.
- Locally `lemma_cache/` (git-ignored) builds itself on first use. Tests use a
  prebuilt trie from `video_study.testutils_lemma`, so they never touch it and
  always exercise the fail-closed path.

## Logging and health

- `DJANGO_LOG_LEVEL` (default `INFO`) sets the root logger and the loggers of
  the project apps (`config/settings.py`). Output is one stdout handler; Fly
  adds timestamps, so the format omits them. `django.request` logs at
  `WARNING`. `django.db.backends` is not configured: too noisy.
- `GET /healthz` (`core.views.healthz`, `login_not_required`) runs `SELECT 1`
  and returns `{"status": "ok"}`, or `{"status": "error"}` with a 503 and a
  logged error. It returns no user data.
- `fly.toml` has no `[[http_service.checks]]`. Fly's check calls the machine
  directly over plain HTTP with the private IP as the `Host` header. Django
  rejects that host (400, `DisallowedHost`), the check goes critical and the
  proxy stops routing to the only machine. A check would need a `Host` header
  and an exemption from the HTTPS redirect, and on one auto-stopping machine a
  false negative takes the site down. Use an external uptime monitor that
  polls `https://<host>/healthz` through the proxy.
