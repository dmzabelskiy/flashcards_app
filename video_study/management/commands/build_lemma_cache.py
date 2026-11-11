from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from simplemma.strategies.dictionaries import TrieDictionaryFactory

from video_study.metadata import LemmaCacheUnavailable, PrebuiltTrieDictionaryFactory

LEMMA_CACHES = (("de", "Haus"), ("nl", "huis"))


class Command(BaseCommand):
    help = (
        "Build the marisa-trie-backed German and Dutch lemma dictionaries at "
        "settings.LEMMA_CACHE_DIR. Must run at Docker image build time: "
        "building the trie peaks at ~350 MB, which would OOM-kill the "
        "256 MB production machine if it ran there instead."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify the cache files exist and load, without building them.",
        )

    def handle(self, *args, **options):
        for lang, probe in LEMMA_CACHES:
            cache_path = Path(settings.LEMMA_CACHE_DIR) / f"{lang}.dic"

            if options["check"]:
                # PrebuiltTrieDictionaryFactory never builds: a missing or corrupt
                # cache file fails the check instead of silently passing (a bare
                # existence check would let a corrupt file through) or building
                # in-process (~350 MB peak, an OOM risk on the machine this runs on).
                factory = PrebuiltTrieDictionaryFactory(disk_cache_dir=settings.LEMMA_CACHE_DIR)
                try:
                    lemma = factory.get_dictionary(lang).get(probe)
                except LemmaCacheUnavailable as exc:
                    raise CommandError(str(exc)) from exc
                if lemma != probe:
                    raise CommandError(
                        f"Lemma cache lookup for {probe!r} returned {lemma!r}, not {probe!r}."
                    )
            else:
                # This is the build tool: always builds with the stock factory,
                # regardless of settings.LEMMA_CACHE_BUILD_ALLOWED.
                factory = TrieDictionaryFactory(disk_cache_dir=settings.LEMMA_CACHE_DIR)
                dictionary = factory.get_dictionary(lang)
                # Force the trie to materialize (and be written to disk) now,
                # rather than lazily on the first real lemmatize() call.
                dictionary.get(probe)

            if not cache_path.exists():
                raise CommandError(f"Lemma cache file not found: {cache_path}")

            size = cache_path.stat().st_size
            self.stdout.write(self.style.SUCCESS(f"{cache_path} ({size} bytes)"))
