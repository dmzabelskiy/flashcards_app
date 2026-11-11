import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from video_study.services import (
    KURZGESAGT_DE,
    MEDIA_CHANNELS,
    ChannelUnavailable,
    PodcastUnavailable,
    is_catalog_fresh,
    prefetch_channel_transcripts,
    prefetch_kurzgesagt_transcripts,
    refresh_channel_catalog,
    refresh_kurzgesagt_catalog,
    refresh_podcast_catalog,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Refresh the video channel catalogs and the 14 Minuten podcast catalog from their "
        "upstream sources. Run this on a schedule (e.g. daily) so the dashboard "
        "never has to fetch inside a request."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--if-stale",
            type=float,
            default=None,
            metavar="HOURS",
            dest="if_stale",
            help=(
                "Only refresh a catalog whose newest item was synced more than HOURS "
                "ago (skip it otherwise). Lets a boot-time hook refresh at most once "
                "per window without hitting the upstream on every machine start."
            ),
        )

    def _refresh_channel(self, channel, hours, now):
        """Refresh one video channel's catalog and prefetch its transcripts."""
        from video_study.models import ChannelVideo

        if is_catalog_fresh(ChannelVideo, hours, now, channel_key=channel.key):
            self.stdout.write(f"{channel.name} catalog is fresh; skipping.")
        else:
            try:
                videos = refresh_channel_catalog(channel)
                self.stdout.write(
                    self.style.SUCCESS(f"Refreshed {len(videos)} {channel.name} video(s).")
                )
            except ChannelUnavailable as exc:
                logger.warning("%s catalog refresh failed: %s", channel.name, exc)
                self.stderr.write(self.style.WARNING(f"{channel.name} unavailable: {exc}"))

        if settings.VIDEO_STUDY_TRANSCRIPT_PREFETCH:
            prefetch_summary = prefetch_channel_transcripts(channel)
            self.stdout.write(
                self.style.SUCCESS(f"{channel.name} transcript prefetch: {prefetch_summary}")
            )
        else:
            self.stdout.write(f"{channel.name} transcript prefetch disabled; skipping.")

    def handle(self, *args, **options):
        # Imported lazily so the command still loads if models move.
        from video_study.models import ChannelVideo, PodcastEpisode

        hours = options["if_stale"]
        now = timezone.now()

        if is_catalog_fresh(ChannelVideo, hours, now, channel_key=KURZGESAGT_DE.key):
            self.stdout.write("Kurzgesagt catalog is fresh; skipping.")
        else:
            try:
                videos = refresh_kurzgesagt_catalog()
                self.stdout.write(
                    self.style.SUCCESS(f"Refreshed {len(videos)} Kurzgesagt video(s).")
                )
            except ChannelUnavailable as exc:
                logger.warning("Kurzgesagt catalog refresh failed: %s", exc)
                self.stderr.write(self.style.WARNING(f"Kurzgesagt channel unavailable: {exc}"))

        if settings.VIDEO_STUDY_TRANSCRIPT_PREFETCH:
            prefetch_summary = prefetch_kurzgesagt_transcripts()
            self.stdout.write(
                self.style.SUCCESS(f"Kurzgesagt transcript prefetch: {prefetch_summary}")
            )
        else:
            self.stdout.write("Kurzgesagt transcript prefetch disabled; skipping.")

        for channel in MEDIA_CHANNELS.values():
            if channel is KURZGESAGT_DE:
                continue
            self._refresh_channel(channel, hours, now)

        if is_catalog_fresh(PodcastEpisode, hours, now):
            self.stdout.write("14 Minuten catalog is fresh; skipping.")
        else:
            try:
                episodes = refresh_podcast_catalog()
                self.stdout.write(
                    self.style.SUCCESS(f"Refreshed {len(episodes)} podcast episode(s).")
                )
            except PodcastUnavailable as exc:
                logger.warning("14 Minuten catalog refresh failed: %s", exc)
                self.stderr.write(self.style.WARNING(f"14 Minuten podcast unavailable: {exc}"))
