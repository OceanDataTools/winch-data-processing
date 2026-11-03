"""Servers that can't be reached, as one-line errors (as nav-qa-processing's).

At sea the connection comes and goes, so a server that can't be reached is expected, not a bug:
`reaching` turns the failure into a `NetworkError` naming the service and its host, which the
command line prints without a traceback.
"""

import http.client
import ssl
import urllib.error
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager

# What urllib raises when it can't connect (URLError wraps DNS and refused connections) or the
# connection breaks partway through a response
UNREACHABLE = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    ssl.SSLError,
    http.client.HTTPException,
)


class NetworkError(OSError):
    """A server couldn't be reached. An OSError, like the URLError it replaces"""


@contextmanager
def reaching(service: str, url: str) -> Iterator[None]:
    """Raises NetworkError, naming service and url's host, when url can't be reached. HTTP
    errors (404, 500, ...) pass through unchanged: the server answered
    """
    try:
        yield
    except urllib.error.HTTPError:
        raise
    except UNREACHABLE as error:
        reason = getattr(error, "reason", error)
        detail = getattr(reason, "strerror", None) or str(reason) or type(reason).__name__
        host = urllib.parse.urlsplit(url).hostname or url
        raise NetworkError(
            f"couldn't reach {service} ({host}): {detail}. Check the network connection and "
            "try again"
        ) from error
