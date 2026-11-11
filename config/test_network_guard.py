import io
import socket
import sys
import unittest
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase
from django.test.runner import DebugSQLTextTestResult, PDBDebugResult

from config import test_runner
from config.test_runner import (
    GuardedParallelTestSuite,
    GuardedRemoteTestRunner,
    GuardedTextTestResult,
    NetworkAccessError,
    NetworkGuardResultMixin,
    PrefetchDisabledTestRunner,
    guarded_result_class,
    network_guard,
)


class NetworkGuardTests(SimpleTestCase):
    """Exercise the guard's own functions; nothing here opens a connection."""

    def tearDown(self):
        # Attempts recorded on purpose here must not fail this test itself.
        network_guard.take_attempts()
        super().tearDown()

    def test_guard_is_installed_while_the_suite_runs(self):
        self.assertTrue(network_guard.installed)
        self.assertEqual(socket.getaddrinfo.__name__, "guarded_getaddrinfo")
        self.assertEqual(socket.socket.connect.__name__, "guarded_connect")

    def test_non_loopback_connect_is_blocked_and_recorded(self):
        with self.assertRaises(NetworkAccessError) as caught:
            network_guard.check_address(("14minuten.de", 443))

        self.assertIn("14minuten.de", str(caught.exception))
        attempts = network_guard.take_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertIn("connect to '14minuten.de'", attempts[0])

    def test_non_loopback_ip_connect_is_blocked_and_recorded(self):
        with self.assertRaises(NetworkAccessError):
            network_guard.check_address(("203.0.113.7", 80))

        self.assertIn("203.0.113.7", network_guard.take_attempts()[0])

    def test_non_loopback_lookup_is_blocked_and_recorded(self):
        with self.assertRaises(NetworkAccessError) as caught:
            network_guard.check("www.youtube.com", "look up")

        self.assertIn("www.youtube.com", str(caught.exception))
        attempts = network_guard.take_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertIn("look up 'www.youtube.com'", attempts[0])

    def test_loopback_is_allowed_and_not_recorded(self):
        for host in ("127.0.0.1", "127.1.2.3", "::1", "[::1]", "localhost", "LOCALHOST.", None, ""):
            with self.subTest(host=host):
                network_guard.check(host)
                network_guard.check_address((host, 8000))
        # A real loopback lookup goes through the installed guard and succeeds.
        self.assertTrue(socket.getaddrinfo("127.0.0.1", 8000))
        self.assertEqual(network_guard.take_attempts(), [])

    def test_unix_socket_paths_are_not_checked(self):
        network_guard.check_address("/tmp/some.sock")
        self.assertEqual(network_guard.take_attempts(), [])

    def test_lookalike_hosts_are_not_loopback(self):
        for host in ("localhost.example.com", "127.0.0.1.example.com", "128.0.0.1"):
            with self.subTest(host=host):
                with self.assertRaises(NetworkAccessError):
                    network_guard.check(host)
                # Inside the sub-test: its result would otherwise report it.
                network_guard.take_attempts()


class SocketEntryPointTests(SimpleTestCase):
    """The installed guard wraps every resolver and datagram entry point.

    The real transports are replaced by mocks, so nothing leaves the machine
    even if the guard were broken.
    """

    def tearDown(self):
        network_guard.take_attempts()
        super().tearDown()

    def datagram_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        return sock

    def assert_blocked(self, real_name, call, host):
        with mock.patch.object(test_runner, real_name) as real:
            with self.assertRaises(NetworkAccessError):
                call()
            real.assert_not_called()
        self.assertIn(host, network_guard.take_attempts()[0])

    def assert_allowed(self, real_name, call):
        with mock.patch.object(test_runner, real_name) as real:
            call()
            real.assert_called_once()
        self.assertEqual(network_guard.take_attempts(), [])

    def test_gethostbyname_family_is_blocked_for_external_hosts(self):
        for function, real_name in (
            (socket.gethostbyname, "_real_gethostbyname"),
            (socket.gethostbyname_ex, "_real_gethostbyname_ex"),
            (socket.gethostbyaddr, "_real_gethostbyaddr"),
        ):
            with self.subTest(function=real_name):
                self.assert_blocked(real_name, lambda f=function: f("example.org"), "example.org")
                self.assert_blocked(real_name, lambda f=function: f("203.0.113.9"), "203.0.113.9")

    def test_gethostbyname_family_allows_loopback(self):
        for function, real_name in (
            (socket.gethostbyname, "_real_gethostbyname"),
            (socket.gethostbyname_ex, "_real_gethostbyname_ex"),
            (socket.gethostbyaddr, "_real_gethostbyaddr"),
        ):
            with self.subTest(function=real_name):
                self.assert_allowed(real_name, lambda f=function: f("127.0.0.1"))

    def test_sendto_to_an_external_address_is_blocked(self):
        sock = self.datagram_socket()
        self.assert_blocked(
            "_real_socket_sendto", lambda: sock.sendto(b"x", ("203.0.113.7", 53)), "203.0.113.7"
        )
        self.assert_blocked(
            "_real_socket_sendto", lambda: sock.sendto(b"x", 0, ("203.0.113.8", 53)), "203.0.113.8"
        )

    def test_sendto_to_loopback_is_allowed(self):
        sock = self.datagram_socket()
        self.assert_allowed("_real_socket_sendto", lambda: sock.sendto(b"x", ("127.0.0.1", 9)))

    @unittest.skipUnless(hasattr(socket.socket, "sendmsg"), "no sendmsg on this platform")
    def test_sendmsg_to_an_external_address_is_blocked(self):
        sock = self.datagram_socket()
        self.assert_blocked(
            "_real_socket_sendmsg",
            lambda: sock.sendmsg([b"x"], [], 0, ("203.0.113.7", 53)),
            "203.0.113.7",
        )
        self.assert_blocked(
            "_real_socket_sendmsg",
            lambda: sock.sendmsg([b"x"], address=("203.0.113.8", 53)),
            "203.0.113.8",
        )

    @unittest.skipUnless(hasattr(socket.socket, "sendmsg"), "no sendmsg on this platform")
    def test_sendmsg_to_loopback_or_a_connected_socket_is_allowed(self):
        sock = self.datagram_socket()
        self.assert_allowed("_real_socket_sendmsg", lambda: sock.sendmsg([b"x"], [], 0, ("::1", 9)))
        self.assert_allowed("_real_socket_sendmsg", lambda: sock.sendmsg([b"x"]))


class GuardedResultTests(SimpleTestCase):
    """A swallowed network error still fails the test that made the attempt."""

    def tearDown(self):
        network_guard.take_attempts()
        super().tearDown()

    def _result(self):
        return GuardedTextTestResult(stream=io.StringIO(), descriptions=False, verbosity=0)

    def _attempt_and_swallow(self, host):
        try:
            network_guard.check(host, "look up")
        except Exception:
            pass  # what the app's broad `except Exception` blocks do

    def test_a_passing_test_that_swallowed_an_attempt_becomes_an_error(self):
        result = self._result()
        self._attempt_and_swallow("podcasts.example.org")

        result.addSuccess(self)

        self.assertEqual(len(result.errors), 1)
        failed_test, message = result.errors[0]
        self.assertIs(failed_test, self)
        self.assertIn("podcasts.example.org", message)
        self.assertIn("blocked by the test runner", message)

    def test_a_test_without_attempts_stays_a_success(self):
        result = self._result()

        result.addSuccess(self)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])

    def test_a_failing_test_also_reports_the_attempt(self):
        result = self._result()
        self._attempt_and_swallow("feeds.example.org")

        try:
            raise AssertionError("boom")
        except AssertionError:
            result.addFailure(self, sys.exc_info())

        self.assertEqual(len(result.failures), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("feeds.example.org", result.errors[0][1])


def swallow(host):
    try:
        network_guard.check(host, "look up")
    except Exception:
        pass  # what the app's broad `except Exception` blocks do


class GuardedRunTests(SimpleTestCase):
    """Whole unittest runs: no terminal result may leave an attempt unreported."""

    def setUp(self):
        super().setUp()
        network_guard.take_attempts()
        self.addCleanup(network_guard.take_attempts)

    def run_suite(self, *cases, resultclass=GuardedTextTestResult):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in cases
        )
        runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=resultclass)
        return runner.run(suite)

    def blamed(self, result):
        return [(str(test), message) for test, message in result.errors]

    def test_an_expected_failure_that_reached_the_network_is_an_error(self):
        class Case(unittest.TestCase):
            @unittest.expectedFailure
            def test_it(self):
                swallow("expected.example.org")
                self.fail("expected")

        result = self.run_suite(Case)

        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.expectedFailures), 1)
        self.assertIn("expected.example.org", self.blamed(result)[0][1])

    def test_a_skipped_test_that_reached_the_network_is_an_error(self):
        class Case(unittest.TestCase):
            def test_it(self):
                swallow("skipped.example.org")
                self.skipTest("not today")

        result = self.run_suite(Case)

        self.assertFalse(result.wasSuccessful())
        self.assertIn("skipped.example.org", self.blamed(result)[0][1])

    def test_an_unexpected_success_that_reached_the_network_is_also_reported(self):
        class Case(unittest.TestCase):
            @unittest.expectedFailure
            def test_it(self):
                swallow("unexpected.example.org")

        result = self.run_suite(Case)

        self.assertFalse(result.wasSuccessful())
        self.assertIn("unexpected.example.org", self.blamed(result)[0][1])

    def test_a_subtest_that_reached_the_network_is_an_error(self):
        class Case(unittest.TestCase):
            def test_it(self):
                with self.subTest("one"):
                    swallow("subtest.example.org")

        result = self.run_suite(Case)

        self.assertFalse(result.wasSuccessful())
        self.assertIn("subtest.example.org", self.blamed(result)[0][1])

    def test_a_skipped_class_after_an_attempt_in_set_up_class_is_reported(self):
        class Skipped(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                swallow("fixture.example.org")
                raise unittest.SkipTest("no")

            def test_it(self):
                pass

        class Innocent(unittest.TestCase):
            def test_nothing(self):
                pass

        result = self.run_suite(Skipped, Innocent)

        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.skipped), 1)
        blamed = self.blamed(result)
        self.assertEqual(len(blamed), 1)
        self.assertIn("fixture.example.org", blamed[0][1])
        self.assertNotIn("test_nothing", blamed[0][0])

    def test_an_attempt_in_set_up_class_is_not_blamed_on_the_first_test(self):
        class Case(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                swallow("setup.example.org")

            def test_innocent(self):
                pass

        result = self.run_suite(Case)

        self.assertFalse(result.wasSuccessful())
        blamed = self.blamed(result)
        self.assertEqual(len(blamed), 1)
        self.assertIn("setup.example.org", blamed[0][1])
        self.assertTrue(blamed[0][0].startswith("network guard:"), blamed[0][0])
        self.assertIn("before", blamed[0][0])

    def test_an_attempt_in_the_last_tear_down_class_is_reported(self):
        class Case(unittest.TestCase):
            @classmethod
            def tearDownClass(cls):
                swallow("teardown.example.org")

            def test_innocent(self):
                pass

        result = self.run_suite(Case)

        self.assertFalse(result.wasSuccessful())
        blamed = self.blamed(result)
        self.assertEqual(len(blamed), 1)
        self.assertIn("teardown.example.org", blamed[0][1])
        self.assertTrue(blamed[0][0].startswith("network guard:"), blamed[0][0])
        self.assertIn("after the last test", blamed[0][0])

    def test_a_clean_run_stays_green(self):
        class Case(unittest.TestCase):
            def test_it(self):
                pass

        self.assertTrue(self.run_suite(Case).wasSuccessful())

    def test_a_parallel_worker_reports_a_trailing_attempt_to_the_parent(self):
        class Case(unittest.TestCase):
            @classmethod
            def tearDownClass(cls):
                swallow("worker.example.org")

            def test_innocent(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Case)
        tests = list(suite)  # a suite drops its tests as it runs them
        worker_result = GuardedRemoteTestRunner().run(suite)
        self.assertFalse(worker_result.wasSuccessful())

        # Replay the worker's events the way ParallelTestSuite does in the parent.
        parent = GuardedTextTestResult(stream=io.StringIO(), descriptions=False, verbosity=0)
        parallel = GuardedParallelTestSuite([suite], 1)
        for event in worker_result.events:
            parallel.handle_event(parent, tests, event)

        self.assertFalse(parent.wasSuccessful())
        blamed = self.blamed(parent)
        self.assertEqual(len(blamed), 1)
        self.assertTrue(blamed[0][0].startswith("network guard:"), blamed[0][0])
        self.assertIn("after the last test", blamed[0][0])


class ResultClassTests(SimpleTestCase):
    def resultclass(self, **options):
        return PrefetchDisabledTestRunner(enable_faulthandler=False, **options).get_resultclass()

    def test_the_default_result_is_guarded(self):
        self.assertIs(self.resultclass(), GuardedTextTestResult)

    def test_debug_sql_keeps_its_result_class_and_is_guarded(self):
        resultclass = self.resultclass(debug_sql=True)

        self.assertTrue(issubclass(resultclass, NetworkGuardResultMixin))
        self.assertTrue(issubclass(resultclass, DebugSQLTextTestResult))

    def test_pdb_keeps_its_result_class_and_is_guarded(self):
        resultclass = self.resultclass(pdb=True)

        self.assertTrue(issubclass(resultclass, NetworkGuardResultMixin))
        self.assertTrue(issubclass(resultclass, PDBDebugResult))

    def test_guarding_twice_changes_nothing(self):
        guarded = guarded_result_class(DebugSQLTextTestResult)

        self.assertIs(guarded_result_class(guarded), guarded)
        self.assertIs(guarded_result_class(DebugSQLTextTestResult), guarded)

    def test_a_debug_sql_run_fails_a_swallowed_attempt(self):
        class Case(unittest.TestCase):
            def test_it(self):
                swallow("debug.example.org")

        network_guard.take_attempts()
        self.addCleanup(network_guard.take_attempts)
        runner = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=guarded_result_class(DebugSQLTextTestResult)
        )

        result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Case))

        self.assertFalse(result.wasSuccessful())
        self.assertIn("debug.example.org", result.errors[0][1])


class RunnerSettingsTests(SimpleTestCase):
    def test_runner_forces_the_background_and_refresh_switches_off(self):
        self.assertFalse(settings.VIDEO_STUDY_TRANSCRIPT_PREFETCH)
        self.assertFalse(settings.VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH)
