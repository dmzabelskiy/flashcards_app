"""The test runner: keeps the suite off the network and off background work.

The network guard patches Python's socket and resolver entry points inside the
test processes only; subprocesses (a spawned interpreter, `curl`) are outside
its reach and are not policed.
"""

import ipaddress
import os
import socket
import sys
import threading
import unittest

from django.conf import settings
from django.test.runner import (
    DiscoverRunner,
    ParallelTestSuite,
    RemoteTestResult,
    RemoteTestRunner,
)

_real_socket_connect = socket.socket.connect
_real_socket_connect_ex = socket.socket.connect_ex
_real_socket_sendto = socket.socket.sendto
_real_socket_sendmsg = getattr(socket.socket, "sendmsg", None)  # not on every platform
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex
_real_gethostbyaddr = socket.gethostbyaddr

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


class NetworkAccessError(OSError):
    """Raised when code under test tries to reach a non-loopback host."""


class NetworkGuard:
    """Block and record outbound connections, datagrams and lookups for non-loopback hosts.

    The application wraps its fetches in broad `except` blocks, so raising
    alone would let a test pass while it still tried to reach a third party.
    Every attempt is therefore also recorded, and the result classes below
    fail the test during which one was recorded. Loopback addresses and Unix
    sockets stay allowed.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._attempts = []
        self._installed = False

    @staticmethod
    def is_loopback_host(host):
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host is None or host == "":
            # getaddrinfo(None, port) resolves the loopback or wildcard address.
            return True
        host = str(host).strip().strip("[]").split("%", 1)[0].lower().rstrip(".")
        if host in _LOOPBACK_NAMES or host.endswith(".localhost"):
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        return address.is_loopback or address.is_unspecified

    def check(self, host, action="connect to"):
        """Allow a loopback host; otherwise record the attempt and raise."""
        if self.is_loopback_host(host):
            return
        description = f"{action} {host!r}"
        with self._lock:
            self._attempts.append(description)
        raise NetworkAccessError(
            f"Tests must not reach the network: attempted to {description}. "
            "Seed the data or mock the fetch."
        )

    def check_address(self, address, action="connect to"):
        # AF_INET and AF_INET6 addresses are tuples; AF_UNIX uses a path.
        if isinstance(address, tuple) and address:
            self.check(address[0], action)

    def take_attempts(self):
        """Return the attempts recorded since the last call and forget them."""
        with self._lock:
            attempts, self._attempts = self._attempts, []
        return attempts

    def install(self):
        if self._installed:
            return
        guard = self

        def guarded_connect(sock, address):
            if sock.family != socket.AF_UNIX:
                guard.check_address(address)
            return _real_socket_connect(sock, address)

        def guarded_connect_ex(sock, address):
            if sock.family != socket.AF_UNIX:
                guard.check_address(address)
            return _real_socket_connect_ex(sock, address)

        def guarded_sendto(sock, data, *args, **kwargs):
            # sendto(data, address) or sendto(data, flags, address)
            if sock.family != socket.AF_UNIX:
                guard.check_address(kwargs.get("address", args[-1] if args else None), "send to")
            return _real_socket_sendto(sock, data, *args, **kwargs)

        def guarded_sendmsg(sock, buffers, *args, **kwargs):
            # sendmsg(buffers, ancdata=(), flags=0, address=None); no address
            # means the socket is connected, and connect was checked.
            if sock.family != socket.AF_UNIX:
                guard.check_address(
                    kwargs.get("address", args[2] if len(args) > 2 else None), "send to"
                )
            return _real_socket_sendmsg(sock, buffers, *args, **kwargs)

        def guarded_getaddrinfo(host, *args, **kwargs):
            guard.check(host, "look up")
            return _real_getaddrinfo(host, *args, **kwargs)

        def guarded_gethostbyname(host):
            guard.check(host, "look up")
            return _real_gethostbyname(host)

        def guarded_gethostbyname_ex(host):
            guard.check(host, "look up")
            return _real_gethostbyname_ex(host)

        def guarded_gethostbyaddr(host):
            guard.check(host, "look up")
            return _real_gethostbyaddr(host)

        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex
        socket.socket.sendto = guarded_sendto
        if _real_socket_sendmsg is not None:
            socket.socket.sendmsg = guarded_sendmsg
        socket.getaddrinfo = guarded_getaddrinfo
        socket.gethostbyname = guarded_gethostbyname
        socket.gethostbyname_ex = guarded_gethostbyname_ex
        socket.gethostbyaddr = guarded_gethostbyaddr
        self._installed = True

    def uninstall(self):
        if not self._installed:
            return
        socket.socket.connect = _real_socket_connect
        socket.socket.connect_ex = _real_socket_connect_ex
        socket.socket.sendto = _real_socket_sendto
        if _real_socket_sendmsg is not None:
            socket.socket.sendmsg = _real_socket_sendmsg
        socket.getaddrinfo = _real_getaddrinfo
        socket.gethostbyname = _real_gethostbyname
        socket.gethostbyname_ex = _real_gethostbyname_ex
        socket.gethostbyaddr = _real_gethostbyaddr
        self._installed = False

    @property
    def installed(self):
        return self._installed


network_guard = NetworkGuard()


class NetworkGuardResultMixin:
    """Fail whatever was running when the network guard recorded an attempt.

    This has to be a result class, not a TestCase hook: it must cover every
    test, and `--parallel` runs tests through RemoteTestResult in the worker
    processes (see GuardedParallelTestSuite). An attempt made while a test or
    a fixture is reported on is blamed on it, whatever the outcome (success,
    failure, error, skip, expected failure, sub-test). An attempt made outside
    any test (a class or module fixture, a thread that outlived its test) is
    reported as its own error before the next test starts, or when the run
    ends, so it is never blamed on an innocent test.
    """

    # RemoteTestResult pickles errors, and a traceback cannot be pickled
    # without tblib; other results want one (pdb's post-mortem needs it).
    keep_traceback = True

    def _guard_exc_info(self, attempts):
        message = "Test reached the network (blocked by the test runner): " + "; ".join(attempts)
        error = NetworkAccessError(message)
        if self.keep_traceback:
            try:
                raise error
            except NetworkAccessError:
                return sys.exc_info()
        return (NetworkAccessError, error, None)

    def _add_guard_error(self, test, err):
        super().addError(test, err)

    def _report_attempts(self, test):
        # Name the host even when the test already ended another way.
        attempts = network_guard.take_attempts()
        if attempts:
            self._add_guard_error(test, self._guard_exc_info(attempts))

    def report_stray_attempts(self, description):
        """Report attempts made outside any test as an error of their own."""
        self._report_attempts(unittest.suite._ErrorHolder(f"network guard: {description}"))

    def startTest(self, test):
        self.report_stray_attempts(f"code that ran outside any test, before {test.id()}")
        super().startTest(test)

    def stopTestRun(self):
        self.report_stray_attempts("code that ran outside any test, after the last test")
        super().stopTestRun()

    def addSuccess(self, test):
        attempts = network_guard.take_attempts()
        if attempts:
            self._add_guard_error(test, self._guard_exc_info(attempts))
        else:
            super().addSuccess(test)

    def addFailure(self, test, err):
        self._report_attempts(test)
        super().addFailure(test, err)

    def addError(self, test, err):
        self._report_attempts(test)
        super().addError(test, err)

    def addSkip(self, test, reason):
        self._report_attempts(test)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._report_attempts(test)
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._report_attempts(test)
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        attempts = network_guard.take_attempts()
        if attempts:
            super().addSubTest(test, subtest, self._guard_exc_info(attempts))
        super().addSubTest(test, subtest, err)


class GuardedTextTestResult(NetworkGuardResultMixin, unittest.TextTestResult):
    pass


_guarded_result_classes = {unittest.TextTestResult: GuardedTextTestResult}


def guarded_result_class(base):
    """`base` with the guard mixin in front, so debug result classes stay guarded."""
    if issubclass(base, NetworkGuardResultMixin):
        return base
    if base not in _guarded_result_classes:
        _guarded_result_classes[base] = type(
            f"Guarded{base.__name__}",
            (NetworkGuardResultMixin, base),
            {"__module__": __name__},
        )
    return _guarded_result_classes[base]


class GuardedRemoteTestResult(NetworkGuardResultMixin, RemoteTestResult):
    keep_traceback = False

    def _add_guard_error(self, test, err):
        if isinstance(test, unittest.suite._ErrorHolder):
            # Not a test: the parent replays this event against a holder of
            # that name instead of the last test that happened to run.
            self.check_picklable(test, err)
            self.events.append(("addError", -1, test.id(), err))
        else:
            super()._add_guard_error(test, err)


class GuardedRemoteTestRunner(RemoteTestRunner):
    resultclass = GuardedRemoteTestResult

    def run(self, test):
        result = super().run(test)
        # The worker's runner never calls stopTestRun; attempts left after
        # the last test (tearDownClass, a late thread) are reported here.
        result.report_stray_attempts("code that ran outside any test, after the last test")
        return result


def _worker_process_setup(*args):
    # Runs in each spawned `--parallel` worker before django.setup(). The env
    # vars set by the parent runner are inherited, so settings need no help.
    network_guard.install()


class GuardedParallelTestSuite(ParallelTestSuite):
    runner_class = GuardedRemoteTestRunner
    process_setup = _worker_process_setup


class PrefetchDisabledTestRunner(DiscoverRunner):
    """Keep the test suite off the network and off background work.

    Forces the Kurzgesagt transcript prefetch and implicit catalog refreshes
    off and installs the network guard. The dashboard is rendered by most of
    the suite, so the gates have to live here rather than in individual tests
    that opt in. Sets the env vars, not just the settings attributes, because
    `--parallel` spawns worker processes (the default start method on macOS)
    that re-import config.settings from scratch and would otherwise re-read
    the env vars' production defaults. The guard is installed in the parent
    here and in each worker by GuardedParallelTestSuite.
    """

    parallel_test_suite = GuardedParallelTestSuite

    def setup_test_environment(self, **kwargs):
        os.environ["VIDEO_STUDY_TRANSCRIPT_PREFETCH"] = "false"
        os.environ["VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH"] = "false"
        super().setup_test_environment(**kwargs)
        settings.VIDEO_STUDY_TRANSCRIPT_PREFETCH = False
        settings.VIDEO_STUDY_IMPLICIT_CATALOG_REFRESH = False
        network_guard.install()

    def teardown_test_environment(self, **kwargs):
        network_guard.uninstall()
        super().teardown_test_environment(**kwargs)

    def get_resultclass(self):
        # Django picks its own result class for --debug-sql and --pdb; keep it
        # and add the guard to it.
        return guarded_result_class(super().get_resultclass() or unittest.TextTestResult)
