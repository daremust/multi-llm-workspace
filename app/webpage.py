"""Bounded public-page fetching; every connection is pinned to a validated IP."""

import ipaddress
import socket
from dataclasses import dataclass, field
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


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    children: list = field(default_factory=list)


class ReadableHTML(HTMLParser):
    """Conservative structural extraction, without CSS rendering or network access."""

    ignored = {'script', 'style', 'noscript', 'svg', 'nav', 'form', 'template'}
    void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    boilerplate = {'related-posts', 'related-articles', 'social-share', 'share-buttons',
                   'sharing-tools', 'cookie-banner', 'cookie-consent', 'site-header',
                   'site-footer', 'site-navigation', 'comments', 'comment-list',
                   # Verified post controls; deliberately exclude neither bylines nor generic authors/tags.
                   'entry-meta-social', 'entry-meta-social-links-list', 'sticky-share-buttons',
                   'post--rate', 'post--comments-count', 'entry-content-author', 'entry-content-comments'}
    blocks = {'p', 'div', 'article', 'section', 'main', 'header', 'footer', 'aside',
              'blockquote', 'figure', 'figcaption', 'caption', 'ul', 'ol', 'dl', 'dt', 'dd',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'tr', 'hr'}

    def __init__(self, base_url: str = ''):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.root = _Element('document', {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        # Recover common omitted paragraph/list-item end tags without swallowing siblings.
        if tag in {'p', 'li'} and self.stack[-1].tag == tag:
            self.stack.pop()
        node = _Element(tag, {key: value or '' for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in self.void:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data.replace('\x00', '\ufffd'))

    def _excluded(self, node, in_article):
        attrs = node.attrs
        markers = set((attrs.get('class', '') + ' ' + attrs.get('id', '')).lower().split())
        classes = set(attrs.get('class', '').lower().split())
        taxonomy = {'card--post-attributes-secondary', 'tags'} <= classes
        role = attrs.get('role', '').lower()
        style = ''.join(attrs.get('style', '').lower().split())
        return (node.tag in self.ignored or 'hidden' in attrs or attrs.get('aria-hidden', '').lower() == 'true'
                or 'display:none' in style or 'visibility:hidden' in style
                or bool(markers & self.boilerplate) or taxonomy or role == 'navigation'
                or (not in_article and (node.tag == 'footer' or role in {'banner', 'contentinfo'})))

    def _events(self, root, *, include_metadata=True):
        # Iterative traversal also tolerates deeply nested, imperfect HTML.
        pending = [(root, False, False)]
        while pending:
            node, closing, in_article = pending.pop()
            if isinstance(node, str):
                yield 'text', node
                continue
            if closing:
                yield 'end', node
                continue
            in_article = in_article or node.tag in {'article', 'main'} or node.attrs.get('role') == 'main'
            if self._excluded(node, in_article) or (not include_metadata and node.tag in {'head', 'title'}):
                continue
            yield 'start', node
            pending.append((node, True, in_article))
            pending.extend((child, False, in_article) for child in reversed(node.children))

    def _link(self, href):
        if not href or href.startswith('#'):
            return ''
        try:
            url = urljoin(self.base_url, href)
            parsed = urlsplit(url)
            if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
                return url
        except ValueError:
            pass
        return ''

    def _render(self, root, *, include_metadata=False):
        parts = []
        pre = 0
        protected = {}
        pre_start = 0
        links = []
        lists = []
        for event, node in self._events(root, include_metadata=include_metadata):
            if event == 'text':
                parts.append(node if pre else ' '.join(node.split()) if not node.isspace() else ' ')
                # Preserve word separation around inline elements.
                if not pre and node and not node.isspace():
                    parts[-1] = (' ' if node[0].isspace() else '') + parts[-1] + (' ' if node[-1].isspace() else '')
                continue
            tag = node.tag
            if event == 'start':
                if tag in self.blocks or tag in {'pre', 'li', 'br'}:
                    parts.append('\n')
                if tag == 'pre':
                    if not pre:
                        pre_start = len(parts)
                    pre += 1
                if tag in {'ul', 'ol'}:
                    lists.append([tag, 0])
                if tag == 'li':
                    if lists:
                        lists[-1][1] += 1
                    parts.append((f'{lists[-1][1]}. ' if lists and lists[-1][0] == 'ol' else '- '))
                if tag in {'td', 'th'}:
                    parts.append(' | ')
                if tag == 'a':
                    links.append((len(parts), self._link(node.attrs.get('href', ''))))
            else:
                if tag == 'a' and links:
                    start, url = links.pop()
                    if url and ''.join(parts[start:]).strip() != url:
                        parts.append(f' ({url})')
                if tag in {'ul', 'ol'} and lists:
                    lists.pop()
                if tag == 'pre':
                    pre -= 1
                    if not pre:
                        # A private placeholder keeps code whitespace out of prose normalization.
                        marker = f'\x00PRE{len(protected)}\x00'
                        protected[marker] = ''.join(parts[pre_start:]).strip('\n')
                        parts[pre_start:] = [marker]
                if tag in self.blocks or tag in {'pre', 'li'}:
                    parts.append('\n')
        # Ordinary HTML whitespace was normalized before assembly. Keep code indentation.
        lines = [line.strip() for line in ''.join(parts).splitlines() if line.strip()]
        return '\n'.join(protected.get(line, line) for line in lines)

    def extract(self) -> tuple[str, str]:
        title = ''
        articles, mains, bodies = [], [], []
        for event, node in self._events(self.root):
            if event != 'start':
                continue
            if node.tag == 'title' and not title:
                title = ' '.join(self._render(node, include_metadata=True).split())
            if node.tag == 'article':
                # Avoid selecting a tiny related-card article over the page's main content.
                link_depth = 0
                size = 0
                for kind, child in self._events(node):
                    if kind == 'text':
                        if not link_depth:
                            size += len(' '.join(child.split()))
                    elif child.tag == 'a':
                        link_depth += 1 if kind == 'start' else -1
                if size >= 200:
                    articles.append((size, node))
            if node.tag == 'main' or node.attrs.get('role') == 'main':
                mains.append(node)
            if node.tag == 'body':
                bodies.append(node)
        if articles:
            selected = max(articles, key=lambda item: item[0])[1]
        else:
            selected = next((node for node in mains if self._render(node).strip()), None)
            selected = selected or next(iter(bodies), self.root)
        # Document title is metadata, never duplicate article body text.
        return title, self._render(selected)


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
                        parser = ReadableHTML(str(target))
                        parser.feed(text)
                        title, text = parser.extract()
                    if not text.strip():
                        raise WebpageError('No readable text was found. Paste the source text instead.')
                    return Webpage(original, str(target), title, text)
            raise WebpageError('The webpage redirected too many times.')
    except (httpx.HTTPError, UnicodeError, LookupError) as exc:
        raise WebpageError('Could not fetch the webpage. Check the URL or paste the text instead.') from exc
