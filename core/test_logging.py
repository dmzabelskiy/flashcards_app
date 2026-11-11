from django.conf import settings
from django.test import SimpleTestCase


class LoggingConfigTests(SimpleTestCase):
    def test_console_handler_writes_to_stdout(self):
        handler = settings.LOGGING["handlers"]["console"]
        self.assertEqual(handler["class"], "logging.StreamHandler")

    def test_django_request_logger_is_warning_and_propagates(self):
        logger_config = settings.LOGGING["loggers"]["django.request"]
        self.assertEqual(logger_config["level"], "WARNING")
        self.assertTrue(logger_config["propagate"])
        self.assertNotIn("handlers", logger_config)

    def test_app_loggers_are_configured(self):
        for app in (
            "video_study",
            "core",
            "reviews",
            "cards",
            "decks",
            "imports_app",
        ):
            self.assertIn(app, settings.LOGGING["loggers"])

    def test_db_backends_logger_is_not_enabled(self):
        self.assertNotIn("django.db.backends", settings.LOGGING["loggers"])
