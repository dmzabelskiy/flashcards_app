"""Keep credential-bearing proxy settings out of Django error reports."""

import re

from django.views.debug import SafeExceptionReporterFilter


class ProxySafeExceptionReporterFilter(SafeExceptionReporterFilter):
    hidden_settings = re.compile(
        f"(?:{SafeExceptionReporterFilter.hidden_settings.pattern})|PROXY",
        flags=SafeExceptionReporterFilter.hidden_settings.flags,
    )
