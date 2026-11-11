import re
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from core import backup_remote as br
from core.backup_remote import RemoteConfig, RemoteConfigError, UploadError

TEMP_ROOT = Path(__file__).resolve().parent.parent / "temp"
TEMP_ROOT.mkdir(exist_ok=True)

KEY_ID = "AKIDEXAMPLE0123456"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
TOKEN = "IQoJb3JpZ2luX2VjEXAMPLETOKEN//+=="
GOOD = dict(
    region="eu-central-1",
    bucket="my-flashcards-backups",
    access_key_id=KEY_ID,
    secret_access_key=SECRET,
)


def make(**overrides):
    values = dict(GOOD)
    values.update(overrides)
    return RemoteConfig.from_values(**values)


def parse_curl_config(text):
    """A reference parser for curl's --config syntax (curl.se/docs/manpage.html#-K).

    One option per physical line, `name = "value"` with \\\\, \\" \\t \\n \\r \\v
    escapes inside double quotes. Written independently of the builder so a
    value that would break out of its line or quote is caught.
    """
    options = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        match = re.fullmatch(r'([a-z0-9.-]+)(?: = (?:"((?:[^"\\]|\\.)*)"|([0-9]+)))?', line)
        assert match, f"not a single well-formed curl option line: {line!r}"
        name, raw, number = match.groups()
        value = number
        if raw is not None:
            value = re.sub(
                r"\\(.)",
                lambda m: {"t": "\t", "n": "\n", "r": "\r", "v": "\v"}.get(m.group(1), m.group(1)),
                raw,
            )
        options.append((name, value))
    return options


class RemoteConfigValidationTests(SimpleTestCase):
    def test_aws_defaults(self):
        config = make()
        self.assertEqual(config.endpoint, "https://s3.eu-central-1.amazonaws.com")
        self.assertEqual(config.addressing, "virtual")
        self.assertEqual(config.prefix, "")
        self.assertEqual(
            config.object_url(config.object_key("db-20261001-000000.sqlite3")),
            "https://my-flashcards-backups.s3.eu-central-1.amazonaws.com/db-20261001-000000.sqlite3",
        )

    def test_china_partition_endpoint(self):
        self.assertEqual(
            make(region="cn-north-1").endpoint, "https://s3.cn-north-1.amazonaws.com.cn"
        )

    def test_custom_endpoint_uses_path_style_and_prefix(self):
        config = make(endpoint="https://Minio.Example.com:9000/", prefix="flash/prod/")
        self.assertEqual(config.endpoint, "https://minio.example.com:9000")
        self.assertEqual(config.prefix, "flash/prod/")
        self.assertEqual(
            config.object_url(config.object_key("db-1.sqlite3")),
            "https://minio.example.com:9000/my-flashcards-backups/flash/prod/db-1.sqlite3",
        )

    def test_dotted_bucket_on_aws_falls_back_to_path_style_and_rejects_forced_virtual(self):
        self.assertEqual(make(bucket="my.dotted.bucket").addressing, "path")
        with self.assertRaises(RemoteConfigError):
            make(bucket="my.dotted.bucket", addressing="virtual")

    def test_missing_required_settings_are_named_without_values(self):
        with self.assertRaises(RemoteConfigError) as ctx:
            RemoteConfig.from_values(bucket="my-flashcards-backups", secret_access_key=SECRET)
        message = str(ctx.exception)
        for name in ("BACKUP_S3_REGION", "BACKUP_S3_ACCESS_KEY_ID"):
            self.assertIn(name, message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("my-flashcards-backups", message)

    def test_hostile_endpoints_are_rejected(self):
        hostile = [
            "http://s3.example.com",  # no TLS
            "ftp://s3.example.com",
            "https://user:pass@s3.example.com",  # credentials in the URL
            "https://evil.example.com@s3.example.com",
            "https://s3.example.com/path",
            "https://s3.example.com/?x=1",
            "https://s3.example.com#frag",
            "https://s3.example.com:99999",
            "https://s3.example.com:0",
            "https://s3.example .com",
            'https://s3.example.com\nurl = "file:///etc/passwd"',
            'https://s3.example.com"\nheader = "x: y',
            "https://s3.example.com\r\nproxy = http://evil",
            "https://s3.example.com\x00",
            "https://-bad.example.com",
            "https://bad-.example.com",
            "https://a..b.example.com",
            "https://[::1]",
            "https://s3.example.com\\@evil.com",
            "https://s3.examp\u00e9le.com",
            "//s3.example.com",
            "s3.example.com",
            "https://",
            "https://" + "a" * 64 + ".com",
        ]
        for endpoint in hostile:
            with self.subTest(endpoint=endpoint), self.assertRaises(RemoteConfigError) as ctx:
                make(endpoint=endpoint)
            self.assertNotIn("passwd", str(ctx.exception))
            self.assertNotIn("pass@", str(ctx.exception))

    def test_hostile_buckets_regions_and_prefixes_are_rejected(self):
        for bucket in (
            "UPPER",
            "ab",
            "-leading",
            "trailing-",
            "has space",
            "a..b",
            "a.-b",
            "192.168.0.1",
            "b/ucket",
            'b"ucket',
            "bucket\nurl=x",
            "b" * 64,
        ):
            with self.subTest(bucket=bucket), self.assertRaises(RemoteConfigError):
                make(bucket=bucket)
        for region in ("EU", "eu_central", "eu central", "eu\nx", "", "a" * 33, "../x", "-x"):
            with self.subTest(region=region), self.assertRaises(RemoteConfigError):
                make(region=region)
        for prefix in (
            "../escape",
            "a/../b",
            "a//b",
            "/abs",
            "a/./b",
            "has space/",
            'q"uote',
            "new\nline/",
            "uni\u00e9/",
            "a" * 201,
        ):
            with self.subTest(prefix=prefix), self.assertRaises(RemoteConfigError):
                make(prefix=prefix)

    def test_hostile_credentials_are_rejected_and_never_echoed(self):
        bad_secrets = [
            "short",
            "has space in it 12345",
            "new\nline-12345678",
            "tab\tsecret-12345678",
            "nul\x00secret-12345678",
            "unic\u00f6de-secret-12345678",
            "x" * 513,
        ]
        for secret in bad_secrets:
            with self.subTest(secret=secret), self.assertRaises(RemoteConfigError) as ctx:
                make(secret_access_key=secret)
            self.assertNotIn(secret.strip(), str(ctx.exception))
        for key_id in ("ab", "id:with:colon", "id with space", "id\nx", 'id"quote', "x" * 129):
            with self.subTest(key_id=key_id), self.assertRaises(RemoteConfigError):
                make(access_key_id=key_id)
        for token in ("tok\nen", "to ken", "tok\x00en", "t\u00f6ken", "x" * 4097):
            with self.subTest(token=token), self.assertRaises(RemoteConfigError):
                make(session_token=token)

    def test_timeout_is_bounded(self):
        self.assertEqual(make().timeout_seconds, 300)
        self.assertEqual(make(timeout_seconds="45").timeout_seconds, 45)
        for value in ("0", "5", "99999", "soon", "-1", "1.5"):
            with self.subTest(value=value), self.assertRaises(RemoteConfigError):
                make(timeout_seconds=value)

    def test_repr_and_str_never_contain_secrets(self):
        config = make(session_token=TOKEN)
        text = repr(config) + str(config)
        for secret in (KEY_ID, SECRET, TOKEN):
            self.assertNotIn(secret, text)


class CurlConfigTests(SimpleTestCase):
    sha = "ab" * 32

    def build(self, config, path="/data/backups/db-20261001-000000.sqlite3"):
        url = config.object_url(config.object_key("db-20261001-000000.sqlite3"))
        return br.build_curl_config(config, Path(path), url, self.sha)

    def test_config_is_a_https_only_signed_private_put(self):
        text = self.build(make(prefix="p"))
        options = dict(parse_curl_config(text))
        self.assertEqual(options["proto"], "=https")
        self.assertEqual(options["proto-redir"], "=https")
        self.assertEqual(options["request"], "PUT")
        self.assertEqual(options["aws-sigv4"], "aws:amz:eu-central-1:s3")
        self.assertEqual(options["user"], f"{KEY_ID}:{SECRET}")
        self.assertEqual(options["upload-file"], "/data/backups/db-20261001-000000.sqlite3")
        names = [name for name, _ in parse_curl_config(text)]
        # No redirect following, no ACL, no insecure TLS, no netrc/proxy escape hatches.
        for forbidden in ("location", "location-trusted", "insecure", "proxy", "netrc", "next"):
            self.assertNotIn(forbidden, names)
        headers = [value for name, value in parse_curl_config(text) if name == "header"]
        self.assertEqual(headers, [f"x-amz-content-sha256: {self.sha}"])
        self.assertFalse(any("acl" in h.lower() for h in headers))
        self.assertEqual(names.count("url"), 1)
        self.assertIn("fail", names)
        self.assertIn("globoff", names)
        self.assertIn("max-time", names)

    def test_session_token_is_sent_as_a_signed_header(self):
        text = self.build(make(session_token=TOKEN))
        headers = [value for name, value in parse_curl_config(text) if name == "header"]
        self.assertIn(f"x-amz-security-token: {TOKEN}", headers)

    def test_quotes_and_backslashes_in_a_secret_are_escaped_not_interpreted(self):
        tricky = 'pa"ss\\word"\\" url = "https://evil.example"'.replace(" ", "")
        config = make(secret_access_key=tricky)
        text = self.build(config)
        parsed = parse_curl_config(text)
        self.assertEqual(dict(parsed)["user"], f"{KEY_ID}:{tricky}")
        self.assertEqual([n for n, _ in parsed].count("url"), 1)
        self.assertNotIn("evil.example", dict(parsed)["url"])

    def test_quoting_refuses_control_and_non_ascii_characters(self):
        for value in ("a\nb", "a\rb", "a\x00b", "a\x7fb", "a\u00e9b", ""):
            with self.subTest(value=value), self.assertRaises(UploadError):
                br.curl_quote(value)

    def test_odd_but_legal_backup_dir_path_cannot_break_out(self):
        text = self.build(make(), path='/data/odd "dir"\\x/db-20261001-000000.sqlite3')
        self.assertEqual(
            dict(parse_curl_config(text))["upload-file"],
            '/data/odd "dir"\\x/db-20261001-000000.sqlite3',
        )
        with self.assertRaises(UploadError):
            self.build(make(), path="/data/new\nline/db-1.sqlite3")
        with self.assertRaises(UploadError):
            self.build(make(), path="-")  # curl would read stdin
        with self.assertRaises(UploadError):
            self.build(make(), path="relative/db.sqlite3")


def fake_run(returncode=0, stdout=b"200", stderr=b"", capture=None):
    def runner(argv, **kwargs):
        if capture is not None:
            capture.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return runner


class UploadTransportTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(holder.cleanup)
        self.path = Path(holder.name) / "db-20261001-000000.sqlite3"
        self.path.write_bytes(b"snapshot bytes" * 100)
        patcher = mock.patch("shutil.which", return_value="/usr/bin/curl")
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, config=None, runner=None):
        config = config or make(session_token=TOKEN)
        return br.upload_file(config, self.path, config.object_key(self.path.name), runner=runner)

    def test_credentials_travel_only_on_stdin(self):
        calls = []
        result = self.upload(runner=fake_run(capture=calls))
        ((argv, kwargs),) = calls
        self.assertEqual(argv, ["/usr/bin/curl", "-q", "--config", "-"])
        for secret in (KEY_ID, SECRET, TOKEN):
            self.assertNotIn(secret, " ".join(argv))
            self.assertNotIn(secret, " ".join(f"{k}={v}" for k, v in kwargs["env"].items()))
            self.assertIn(secret.encode(), kwargs["input"])
        self.assertNotIn("shell", kwargs)
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(result.key, self.path.name)
        self.assertEqual(result.size, self.path.stat().st_size)

    def test_child_environment_is_minimal_and_ignores_user_curlrc_and_proxies(self):
        calls = []
        with mock.patch.dict(
            "os.environ",
            {
                "HTTPS_PROXY": "http://proxy.invalid",
                "ALL_PROXY": "http://proxy.invalid",
                "CURL_HOME": "/home/x",
                "HOME": "/home/x",
                "BACKUP_S3_SECRET_ACCESS_KEY": SECRET,
                "DJANGO_SECRET_KEY": "django-secret-key-value",
            },
        ):
            self.upload(runner=fake_run(capture=calls))
        ((argv, kwargs),) = calls
        self.assertEqual(argv[1], "-q")  # must be first so ~/.curlrc is not read
        self.assertEqual(set(kwargs["env"]), {"PATH", "LC_ALL"})
        self.assertEqual(kwargs["cwd"], "/")

    def test_timeouts_are_bounded(self):
        calls = []
        self.upload(make(timeout_seconds="60"), runner=fake_run(capture=calls))
        ((argv, kwargs),) = calls
        self.assertEqual(kwargs["timeout"], 90)
        options = dict(parse_curl_config(kwargs["input"].decode()))
        self.assertEqual(options["max-time"], "60")
        self.assertEqual(options["connect-timeout"], str(br.CONNECT_TIMEOUT_SECONDS))

    def test_payload_digest_matches_the_file(self):
        import hashlib

        calls = []
        result = self.upload(runner=fake_run(capture=calls))
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(result.sha256, expected)
        self.assertIn(f"x-amz-content-sha256: {expected}".encode(), calls[0][1]["input"])

    def test_curl_failure_message_is_redacted(self):
        leak = (
            f"curl: (22) denied for {KEY_ID} with {SECRET} token {TOKEN} "
            "Authorization: AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE0123456/20261001/x/s3/aws4_request, "
            "Signature=deadbeefdeadbeef"
        )
        with self.assertRaises(UploadError) as ctx:
            self.upload(runner=fake_run(returncode=22, stdout=b"", stderr=leak.encode()))
        message = str(ctx.exception)
        for secret in (KEY_ID, SECRET, TOKEN, "deadbeefdeadbeef", "AWS4-HMAC-SHA256"):
            self.assertNotIn(secret, message)
        self.assertIn("status 22", message)

    def test_huge_or_binary_stderr_is_bounded_and_printable(self):
        noise = b"\x00\x1b[31m" + b"A" * 5000 + b"\n\x07"
        with self.assertRaises(UploadError) as ctx:
            self.upload(runner=fake_run(returncode=35, stdout=b"", stderr=noise))
        message = str(ctx.exception)
        self.assertLess(len(message), br.MAX_STDERR_CHARS + 100)
        self.assertTrue(all(32 <= ord(c) < 127 for c in message))

    def test_timeout_missing_curl_and_os_errors_become_upload_errors(self):
        def timing_out(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 30)

        def exploding(argv, **kwargs):
            raise PermissionError(13, "nope")

        with self.assertRaises(UploadError):
            self.upload(runner=timing_out)
        with self.assertRaises(UploadError):
            self.upload(runner=exploding)
        with mock.patch("shutil.which", return_value=None), self.assertRaises(UploadError):
            self.upload(runner=fake_run())

    def test_only_a_2xx_status_counts_as_success(self):
        for status in (b"", b"000", b"301", b"403", b"500", b"200 OK", b"<html>"):
            with self.subTest(status=status), self.assertRaises(UploadError):
                self.upload(runner=fake_run(stdout=status))
        self.assertEqual(self.upload(runner=fake_run(stdout=b"200\n")).key, self.path.name)

    def test_missing_local_file_is_an_upload_error_not_a_crash(self):
        self.path.unlink()
        with self.assertRaises(UploadError):
            self.upload(runner=fake_run())


class RedactTests(SimpleTestCase):
    def test_known_secrets_and_signature_shapes_are_removed(self):
        text = (
            "key=SUPERSECRET Credential=AKIA123/2026/x Signature=abc123 x-amz-security-token: TOK"
        )
        out = br.redact(text, ["SUPERSECRET", "TOK"])
        self.assertNotIn("SUPERSECRET", out)
        self.assertNotIn("abc123", out)
        self.assertNotIn("AKIA123", out)

    def test_empty_secret_does_not_blank_the_message(self):
        self.assertEqual(br.redact("plain message", [""]), "plain message")
