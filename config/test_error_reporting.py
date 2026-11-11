from django.test import RequestFactory, SimpleTestCase, override_settings
from django.views.debug import SafeExceptionReporterFilter, get_default_exception_reporter_filter

from config.error_reporting import ProxySafeExceptionReporterFilter

PROXY = "http://operator:private%40password@proxy.example:8080"


class ProxyErrorReportTests(SimpleTestCase):
    @override_settings(VIDEO_STUDY_YOUTUBE_PROXY_URL=PROXY)
    def test_configured_filter_redacts_proxy_setting_even_in_debug(self):
        for debug in [True, False]:
            with self.subTest(debug=debug), override_settings(DEBUG=debug):
                reporter = get_default_exception_reporter_filter()
                self.assertIsInstance(reporter, ProxySafeExceptionReporterFilter)
                safe = reporter.get_safe_settings()
                self.assertEqual(
                    safe["VIDEO_STUDY_YOUTUBE_PROXY_URL"], reporter.cleansed_substitute
                )
                self.assertNotIn(PROXY, str(safe))

    def test_request_meta_redacts_environment_proxy_values(self):
        request = RequestFactory().get("/")
        request.META.update(
            {
                "VIDEO_STUDY_YOUTUBE_PROXY_URL": PROXY,
                "HTTPS_PROXY": PROXY,
                "http_proxy": PROXY,
                "PUBLIC_VALUE": "visible",
            }
        )
        reporter = get_default_exception_reporter_filter()
        safe = reporter.get_safe_request_meta(request)
        for key in ["VIDEO_STUDY_YOUTUBE_PROXY_URL", "HTTPS_PROXY", "http_proxy"]:
            self.assertEqual(safe[key], reporter.cleansed_substitute)
        self.assertEqual(safe["PUBLIC_VALUE"], "visible")
        self.assertNotIn(PROXY, str(safe))

    def test_all_stock_patterns_and_recursive_redaction_are_preserved(self):
        reporter = ProxySafeExceptionReporterFilter()
        stock = SafeExceptionReporterFilter()
        for key in ["API", "AUTH", "TOKEN", "KEY", "SECRET", "PASS", "SIGNATURE", "HTTP_COOKIE"]:
            with self.subTest(key=key):
                self.assertEqual(
                    reporter.cleanse_setting(key, "private"), stock.cleanse_setting(key, "private")
                )
        self.assertEqual(
            reporter.cleanse_setting("PUBLIC", {"PROXY_URL": PROXY, "TOKEN": "private"}),
            {
                "PROXY_URL": reporter.cleansed_substitute,
                "TOKEN": reporter.cleansed_substitute,
            },
        )
