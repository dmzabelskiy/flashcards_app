# Video study

Open `/videos/` or choose **Video study** in the main navigation.

## Workflow

1. Choose the dashboard's language mode, then paste a YouTube video URL.
2. Choose an available transcript language: German or English in the German
   modes, or Dutch in NL → EN. The server extracts that caption track and
   caches its timestamped segments in SQLite.
3. The page embeds the YouTube player next to the transcript.
4. Playback highlights the current transcript segment. Selecting a timestamp
   seeks the player.
5. Click a transcript word or select a phrase to translate it into the active
   mode's target language: Russian, Spanish or English. Results are cached
   globally per language pair. Save a selected word to the global deck to
   practise it.

## Viewing and selecting

- Choose **Theater mode** to expand the player to the viewport while keeping the
  transcript docked on the right. Press `Esc` or choose **Exit theater** to leave.
- Click a word to translate it, or drag across several transcript words to
  translate the selected phrase. Selections are limited to 500 UTF-8 bytes.
- On small screens, use **Transcript** to open and close the transcript drawer.
  In short landscape windows, scroll the player area to reach caption, media
  completion and retry controls.

## Dutch + English video captions

In **NL → EN** mode, captions start as **Dutch only**. Select **Dutch + English
captions** before loading a video, or use **Captions → Dutch + English → Apply
captions** beside the player (also available in theater mode). The choice stays
with the signed-in account's browser session through loading, refreshing and
opening cached videos. Choose **Dutch only** and apply to turn it off.

The transcript shows English references below the overlapping Dutch sentence.
The labeled caption area below the player follows each track's own timestamps,
including paused seeks and gaps. Different segment counts do not need to match.
Click Dutch words to use the Dutch dictionary and capture to the global deck;
select a phrase within a Dutch sentence. English references do not trigger Dutch
lookups, and a selection crossing an English reference is ignored.

English comes from YouTube's caption tracks, preferring manual captions and
accepting regional English (such as `en-GB`). If there is no English track,
YouTube's built-in translation of the selected Dutch track is used when offered.
The page labels generated captions and captions auto-translated by YouTube.
English JSON is shared public cache data, stored separately from Dutch under the
English language key; the actual track code and origin are stored with it.
The full transcript is never sent to the word-translation providers.

English is fetched only after selecting bilingual captions, including navigation
while that explicit session preference is on. Ordinary visits, default loading
and background caption prefetch do not fetch English. **Refresh transcript**
refreshes both tracks when bilingual captions are selected. If English fails,
Dutch stays usable and any previous English cache stays visible; **Retry English
captions** retries only English. Other video language modes and podcasts keep
their existing controls.

Accepted links include regular YouTube watch links, `youtu.be` links, Shorts,
Live, embed URLs, and raw 11-character video IDs.

## External services

- Captions use the unofficial `youtube-transcript-api` package. Videos with
  disabled or missing captions in the selected language cannot be loaded, and
  YouTube may block transcript requests.
- Translation providers are tried in the order of
  `VIDEO_STUDY_TRANSLATION_PROVIDERS`; unconfigured ones are skipped.
  - `wiktionary`: Russian Wiktionary, single words, Russian target only, no key.
  - `deepl`: used when `VIDEO_STUDY_DEEPL_API_KEY` is set.
  - `libretranslate`: used when `VIDEO_STUDY_LIBRETRANSLATE_ENDPOINT` is set.
  - `mymemory`: the no-key fallback.
- Word capture reads German Wiktionary (German) or English Wiktionary (Dutch)
  for dictionary data, before translating.

Only the word or phrase the user selected is sent to a provider; the
surrounding sentence stays local. Russian results without Cyrillic characters
are rejected and not cached. No video or audio is downloaded; only caption
JSON and word translations are stored.

## Settings

All are environment variables read in `config/settings.py`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `VIDEO_STUDY_TRANSLATION_PROVIDERS` | `wiktionary,deepl,libretranslate,mymemory` | Provider order. |
| `VIDEO_STUDY_TRANSLATION_TIMEOUT` | `8` | Seconds per provider call. |
| `VIDEO_STUDY_TRANSLATION_BUDGET` | `12` | Seconds one translation request may spend across all providers. |
| `VIDEO_STUDY_TRANSLATION_EMAIL` | empty | Contact sent to MyMemory for higher-volume use. |
| `VIDEO_STUDY_DEEPL_API_KEY` | empty | Enables DeepL. |
| `VIDEO_STUDY_DEEPL_ENDPOINT` | `https://api-free.deepl.com/v2/translate` | DeepL endpoint; change it for a paid plan. |
| `VIDEO_STUDY_LIBRETRANSLATE_ENDPOINT` | empty | Enables LibreTranslate. |
| `VIDEO_STUDY_LIBRETRANSLATE_API_KEY` | empty | Key, if the instance needs one. |
| `VIDEO_STUDY_DICTIONARY_ENDPOINT` | `https://de.wiktionary.org/w/api.php` | German dictionary lookups; empty disables them. |
| `VIDEO_STUDY_NL_DICTIONARY_ENDPOINT` | `https://en.wiktionary.org/w/api.php` | Dutch dictionary lookups; empty disables them. |
| `VIDEO_STUDY_YOUTUBE_PROXY_URL` | empty | Caption proxy, see below. |
| `VIDEO_STUDY_YOUTUBE_TIMEOUT` | `15` | Seconds of read inactivity per caption request; positive and finite. |
| `VIDEO_STUDY_TRANSCRIPT_PREFETCH` | `true` | Background caching of the captions of the mode channel's daily video. |
| `VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS` | `3` | Uncached videos attempted per run; `0` stops caption requests. |
| `VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH` | `true` | Whether an empty catalog is fetched from a page view. |

## YouTube caption proxy

Cloud hosting addresses can be blocked by YouTube. When direct caption
requests are blocked, set `VIDEO_STUDY_YOUTUBE_PROXY_URL` to an authenticated
rotating proxy endpoint, for example
`http://USERNAME:ENCODED_PASSWORD@ROTATING_HOST:PORT`. Percent-encode reserved
characters in the credentials (`@` becomes `%40`). `http://` still carries
YouTube's HTTPS through a CONNECT tunnel; use `https://` only for a listener
that speaks TLS to the proxy itself. A static endpoint stays static, so use
the provider's rotating one. A malformed URL fails with a sanitised error and
never falls back to direct requests.

- Only server-side caption requests use the proxy: primary captions, selected
  English captions, YouTube's English translation and background prefetch.
  Catalogs, podcasts, dictionaries, translations, browser traffic and the
  embedded player do not.
- Each caption workflow uses its own session and ignores ambient proxy
  environment variables.
- Requests have a connection timeout of at most 5 seconds and the read
  inactivity limit above, so listing plus fetching can take longer than 15
  seconds. There are no automatic retries; a manual retry may get the
  provider's next connection. Cached captions stay available when a refresh
  or the optional English fetch fails.
- Logs carry video IDs and error types, never provider exception text,
  credentials or the proxy value. Django error reports redact proxy settings.

Prefetch tries today's video and then upcoming days of the dashboard's
rotation, skipping cached captions, so a persistent failure cannot use up
every run. It stops on a YouTube connection block, and failures count against
the cap. The cap is not a billing quota: each process restart, boot catalog
refresh and manual command run can start another capped run, and interactive
loads and the bilingual option use traffic outside it. Set spend and traffic
limits at the proxy provider.

### Fly secrets

Keep credentials out of source control, images, `fly.toml` and shell history.
Put them in an access-restricted file outside the checkout and import it
through stdin, staged for the next deploy:

```sh
chmod 600 /secure/path/caption-secrets.env
fly secrets import --stage --app <app> < /secure/path/caption-secrets.env
fly deploy --app <app> --ha=false
```

The file holds `VIDEO_STUDY_YOUTUBE_PROXY_URL=...` and, if changed from the
defaults, `VIDEO_STUDY_YOUTUBE_TIMEOUT` and
`VIDEO_STUDY_TRANSCRIPT_PREFETCH_MAX_VIDEOS`. Without `--stage` the import
restarts the machines at once. To return to direct access:

```sh
fly secrets unset --stage --app <app> VIDEO_STUDY_YOUTUBE_PROXY_URL
fly deploy --app <app> --ha=false
```
