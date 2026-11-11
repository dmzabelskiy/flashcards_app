"""Private, opt-in off-site backup upload to an S3-compatible bucket.

The transport is the `curl` the Docker image already installs, signing with
`--aws-sigv4 aws:amz:<region>:s3` (https://curl.se/docs/manpage.html#--aws-sigv4).
Nothing here touches the network on import or in tests: callers hand
`upload_file` a `runner` (default `subprocess.run`), and tests substitute it.

Secret handling rules, each enforced by tests:

* Credentials travel only on curl's stdin as a `--config -` file
  (https://curl.se/docs/manpage.html#-K), never in argv, the environment, or a
  log line. `-q` first on the command line stops curl from reading a user
  `.curlrc`; the child gets a minimal environment (no proxies, no HOME).
* Every configured value is checked against a strict allowlist before it can
  reach the config file, so a hostile endpoint, prefix, or secret cannot add
  options, lines, or a second URL. Error messages name the setting, never its
  value.
* HTTPS only (`proto = "=https"`), no redirect following, bounded timeouts.
* Objects are plain private PUTs: no ACL header is ever sent, so the bucket's
  own (private) default applies. Block public access on the bucket as well.
"""

import hashlib
import ipaddress
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 300
MIN_UPLOAD_TIMEOUT_SECONDS = 10
MAX_UPLOAD_TIMEOUT_SECONDS = 3600
MAX_STDERR_CHARS = 300

_REGION_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_ENDPOINT_RE = re.compile(
    r"https://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?/?"
)
_KEY_ID_RE = re.compile(r"[A-Za-z0-9._+=@-]{3,128}")
_PRINTABLE_ASCII_RE = re.compile(r"[\x21-\x7e]+")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_HTTP_2XX_RE = re.compile(r"2[0-9][0-9]")


class RemoteConfigError(ValueError):
    """Invalid off-site configuration. The message never contains a value."""


class UploadError(Exception):
    """An upload did not succeed. The message is already redacted."""


@dataclass(frozen=True)
class RemoteConfig:
    endpoint: str  # normalised "https://host[:port]", no path
    region: str
    bucket: str
    prefix: str  # "" or "a/b/" (always trailing slash)
    addressing: str  # "path" or "virtual"
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str = field(default="", repr=False)
    timeout_seconds: int = DEFAULT_UPLOAD_TIMEOUT_SECONDS

    @classmethod
    def from_values(
        cls,
        *,
        endpoint="",
        region="",
        bucket="",
        prefix="",
        addressing="",
        access_key_id="",
        secret_access_key="",
        session_token="",
        timeout_seconds="",
    ):
        """Validate raw settings strings, raising RemoteConfigError on any problem."""
        endpoint, region, bucket, prefix = (
            _clean(endpoint),
            _clean(region),
            _clean(bucket),
            _clean(prefix),
        )
        addressing = _clean(addressing).lower()
        access_key_id = _clean(access_key_id)
        secret_access_key = _clean(secret_access_key)
        session_token = _clean(session_token)

        missing = [
            name
            for name, value in (
                ("BACKUP_S3_REGION", region),
                ("BACKUP_S3_BUCKET", bucket),
                ("BACKUP_S3_ACCESS_KEY_ID", access_key_id),
                ("BACKUP_S3_SECRET_ACCESS_KEY", secret_access_key),
            )
            if not value
        ]
        if missing:
            raise RemoteConfigError("missing required setting(s): " + ", ".join(missing))

        if not _REGION_RE.fullmatch(region) or len(region) > 32:
            raise RemoteConfigError("BACKUP_S3_REGION is not a valid region name")
        if not _valid_bucket(bucket):
            raise RemoteConfigError("BACKUP_S3_BUCKET is not a valid bucket name")
        normalised_prefix = _normalise_prefix(prefix)
        if addressing not in {"", "path", "virtual"}:
            raise RemoteConfigError("BACKUP_S3_ADDRESSING must be empty, 'path' or 'virtual'")
        if not _KEY_ID_RE.fullmatch(access_key_id):
            raise RemoteConfigError("BACKUP_S3_ACCESS_KEY_ID has unsupported characters or length")
        if not _PRINTABLE_ASCII_RE.fullmatch(secret_access_key) or not (
            8 <= len(secret_access_key) <= 512
        ):
            raise RemoteConfigError(
                "BACKUP_S3_SECRET_ACCESS_KEY must be 8-512 printable ASCII characters "
                "without spaces"
            )
        if session_token and (
            not _PRINTABLE_ASCII_RE.fullmatch(session_token) or len(session_token) > 4096
        ):
            raise RemoteConfigError(
                "BACKUP_S3_SESSION_TOKEN must be printable ASCII without spaces, "
                "at most 4096 characters"
            )

        timeout = _parse_timeout(timeout_seconds)

        if endpoint:
            normalised_endpoint = _normalise_endpoint(endpoint)
            custom_endpoint = True
        else:
            suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
            normalised_endpoint = f"https://s3.{region}.{suffix}"
            custom_endpoint = False
        if not addressing:
            # Virtual-hosted style needs a dot-free bucket to match the
            # endpoint's wildcard certificate; custom endpoints default to path.
            addressing = "virtual" if not custom_endpoint and "." not in bucket else "path"
        if addressing == "virtual" and "." in bucket:
            raise RemoteConfigError(
                "BACKUP_S3_ADDRESSING=virtual cannot be used with a bucket name containing dots"
            )

        return cls(
            endpoint=normalised_endpoint,
            region=region,
            bucket=bucket,
            prefix=normalised_prefix,
            addressing=addressing,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            timeout_seconds=timeout,
        )

    def object_key(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def object_url(self, key: str) -> str:
        scheme_end = len("https://")
        host = self.endpoint[scheme_end:]
        if self.addressing == "virtual":
            return f"https://{self.bucket}.{host}/{key}"
        return f"{self.endpoint}/{self.bucket}/{key}"

    def secrets(self) -> tuple[str, ...]:
        return tuple(
            s for s in (self.access_key_id, self.secret_access_key, self.session_token) if s
        )


def _clean(value) -> str:
    # Only surrounding whitespace from a hand-edited env var is forgiven; any
    # whitespace or control character inside a value fails its allowlist.
    return str(value if value is not None else "").strip()


def _valid_bucket(bucket: str) -> bool:
    if not _BUCKET_RE.fullmatch(bucket):
        return False
    if ".." in bucket or ".-" in bucket or "-." in bucket:
        return False
    try:
        ipaddress.ip_address(bucket)
    except ValueError:
        return True
    return False


def _normalise_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    if len(prefix) > 200 or not _PREFIX_RE.fullmatch(prefix):
        raise RemoteConfigError("BACKUP_S3_PREFIX has unsupported characters or length")
    trimmed = prefix[:-1] if prefix.endswith("/") else prefix
    segments = trimmed.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise RemoteConfigError("BACKUP_S3_PREFIX must not contain empty, '.' or '..' segments")
    return trimmed + "/"


def _normalise_endpoint(endpoint: str) -> str:
    match = _ENDPOINT_RE.fullmatch(endpoint)
    if not match:
        raise RemoteConfigError(
            "BACKUP_S3_ENDPOINT must be a bare https://host[:port] URL "
            "(no credentials, path, query or fragment)"
        )
    host, port = match.group(1), match.group(2)
    if len(host) > 253 or any(not 1 <= len(label) <= 63 for label in host.split(".")):
        raise RemoteConfigError("BACKUP_S3_ENDPOINT host name is not valid")
    if any(label.startswith("-") or label.endswith("-") for label in host.split(".")):
        raise RemoteConfigError("BACKUP_S3_ENDPOINT host name is not valid")
    if port is not None and not 1 <= int(port) <= 65535:
        raise RemoteConfigError("BACKUP_S3_ENDPOINT port is not valid")
    return f"https://{host.lower()}" + (f":{int(port)}" if port is not None else "")


def _parse_timeout(raw) -> int:
    text = _clean(raw)
    if not text:
        return DEFAULT_UPLOAD_TIMEOUT_SECONDS
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not MIN_UPLOAD_TIMEOUT_SECONDS <= value <= MAX_UPLOAD_TIMEOUT_SECONDS:
        raise RemoteConfigError(
            "BACKUP_UPLOAD_TIMEOUT_SECONDS must be an integer from "
            f"{MIN_UPLOAD_TIMEOUT_SECONDS} to {MAX_UPLOAD_TIMEOUT_SECONDS}"
        )
    return value


def curl_quote(value: str) -> str:
    """Quote one curl config-file parameter (curl --config escape rules).

    Rejects control characters and non-ASCII rather than escaping them, so no
    value can ever span lines or smuggle a second option.
    """
    if not isinstance(value, str) or not value:
        raise UploadError("refusing to write an empty value into the curl config")
    for char in value:
        code = ord(char)
        if code < 0x20 or code == 0x7F or code > 0x7E:
            raise UploadError("refusing to write a control or non-ASCII character into curl config")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_curl_config(config: RemoteConfig, path: Path, url: str, sha256_hex: str) -> str:
    """Return the curl config file text (it contains the secrets: stdin only)."""
    if not _SHA256_RE.fullmatch(sha256_hex):
        raise UploadError("invalid payload digest")
    if not path.is_absolute():
        raise UploadError("upload path must be absolute")
    lines = [
        "silent",
        "show-error",
        "fail",
        "globoff",
        "tlsv1.2",
        'proto = "=https"',
        'proto-redir = "=https"',
        f"connect-timeout = {CONNECT_TIMEOUT_SECONDS}",
        f"max-time = {int(config.timeout_seconds)}",
        'request = "PUT"',
        f"upload-file = {curl_quote(str(path))}",
        f"url = {curl_quote(url)}",
        f"aws-sigv4 = {curl_quote(f'aws:amz:{config.region}:s3')}",
        f"user = {curl_quote(config.access_key_id + ':' + config.secret_access_key)}",
        f"header = {curl_quote('x-amz-content-sha256: ' + sha256_hex)}",
    ]
    if config.session_token:
        lines.append(f"header = {curl_quote('x-amz-security-token: ' + config.session_token)}")
    lines += [
        'output = "/dev/null"',
        f"write-out = {curl_quote('%{http_code}')}",
    ]
    return "\n".join(lines) + "\n"


def redact(text, secrets=()) -> str:
    """Make arbitrary error text safe to log or store.

    Removes every known secret value, anything that looks like a signature or
    auth header, control characters, and bounds the length.
    """
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(
        r"(?i)(authorization|x-amz-security-token|x-amz-signature)\S*\s*[:=]?\s*\S+",
        r"\1 [redacted]",
        text,
    )
    text = re.sub(r"(?i)(Credential|Signature|AWSAccessKeyId)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7e\s]", "?", text)).strip()
    if len(text) > MAX_STDERR_CHARS:
        text = text[:MAX_STDERR_CHARS] + "..."
    return text


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class UploadResult:
    key: str
    sha256: str
    size: int


def upload_file(config: RemoteConfig, path: Path, key: str, *, runner=subprocess.run):
    """PUT `path` to `key` in the bucket with curl. Raises UploadError on any failure."""
    path = Path(path)
    try:
        path = path.resolve(strict=True)
        size = path.stat().st_size
        digest = file_sha256(path)
    except OSError as exc:
        raise UploadError(f"cannot read backup file: {type(exc).__name__}") from None

    secrets = config.secrets()
    curl = shutil.which("curl")
    if curl is None:
        raise UploadError("curl is not installed")
    body = build_curl_config(config, path, config.object_url(key), digest)

    # Minimal environment: no HOME/CURL_HOME (no curlrc), no *_proxy variables
    # and none of this process's secrets leak into the child.
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LC_ALL": "C"}
    argv = [curl, "-q", "--config", "-"]
    try:
        proc = runner(
            argv,
            input=body.encode("ascii"),
            capture_output=True,
            timeout=config.timeout_seconds + 30,
            env=env,
            cwd="/",
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise UploadError("upload timed out") from None
    except OSError as exc:
        raise UploadError(f"could not run curl: {type(exc).__name__}") from None

    if proc.returncode != 0:
        detail = redact(proc.stderr.decode("utf-8", "replace") if proc.stderr else "", secrets)
        message = f"curl exited with status {proc.returncode}"
        raise UploadError(f"{message}: {detail}" if detail else message)
    status = (proc.stdout or b"").decode("ascii", "replace").strip()
    if not _HTTP_2XX_RE.fullmatch(status):
        raise UploadError(f"unexpected HTTP status {redact(status, secrets)[:20]!r}")
    return UploadResult(key=key, sha256=digest, size=size)
