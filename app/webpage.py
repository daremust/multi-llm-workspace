"""Bounded public-page fetching; every connection is pinned to a validated IP."""

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 2_000_000


class WebpageError(ValueError):
    """A URL could not safely produce readable source text."""


@dataclass(frozen=True)
class Webpage:
    original_url: str
    final_url: str
    title: str
    content: str


def validate_url(url: str) -> tuple[httpx.URL, str]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if (parsed.scheme not in {'http', 'https'} or not host
                or parsed.username is not None or parsed.password is not None):
            raise ValueError
        if host == 'localhost' or host.endswith(('.localhost', '.local')):
            raise WebpageError('Local destinations are not allowed.')
        if host == 'linkedin.com' or host.endswith('.linkedin.com'):
            raise WebpageError('LinkedIn fetching is not supported. Paste the content instead.')
        normalized = httpx.URL(url)
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not ips or any(not ip.is_global or ip.is_multicast or ip.is_unspecified
                          or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
                          for ip in ips):
            raise WebpageError('Only public internet destinations are allowed.')
        return normalized, str(ips[0])
    except WebpageError:
        raise
    except (ValueError, OSError, httpx.InvalidURL) as exc:
        raise WebpageError('Enter a valid, resolvable public HTTP/HTTPS URL.') from exc


class ReadableHTML(HTMLParser):
    """Extract visible text without scripts, styles, navigation, or forms."""

    ignored = {'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'form', 'template'}
    blocks = {'p', 'div', 'article', 'section', 'main', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'tr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip: list[str] = []
        self.in_title = False
        self.title: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.skip.append(tag)
        if tag == 'title':
            self.in_title = True
        if not self.skip and tag in self.blocks:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == 'title':
            self.in_title = False
        if tag in self.blocks:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            if self.in_title:
                self.title.append(data)
            else:
                self.parts.append(data)


def fetch_webpage(url: str) -> Webpage:
    original = url.strip()
    current = original
    try:
        # No environment proxy: it could bypass destination validation.
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            for _ in range(6):
                target, ip = validate_url(current)
                pinned = target.copy_with(host=ip)
                with client.stream('GET', pinned,
                                   headers={'Host': target.netloc.decode('ascii'), 'User-Agent': 'Multi-LLM-Workspace/0.1'},
                                   extensions={'sni_hostname': target.host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get('location')
                        if not location:
                            raise WebpageError('The webpage returned a redirect without a destination.')
                        current = urljoin(str(target), location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if content_type not in {'text/html', 'application/xhtml+xml', 'text/plain'}:
                        raise WebpageError('The URL must return an HTML webpage or plain text.')
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BYTES:
                            raise WebpageError('The webpage is too large (maximum 2 MB).')
                    text = chunks.decode(response.encoding or 'utf-8', errors='replace')
                    title = ''
                    if content_type != 'text/plain':
                        parser = ReadableHTML()
                        parser.feed(text)
                        title = ' '.join(''.join(parser.title).split())
                        text = '\n'.join(line for part in ''.join(parser.parts).splitlines()
                                         if (line := ' '.join(part.split())))
                    if not text.strip():
                        raise WebpageError('No readable text was found. Paste the source text instead.')
                    return Webpage(original, str(target), title, text)
            raise WebpageError('The webpage redirected too many times.')
    except (httpx.HTTPError, UnicodeError, LookupError) as exc:
        raise WebpageError('Could not fetch the webpage. Check the URL or paste the text instead.') from exc
