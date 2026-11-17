import socket

import httpx
import pytest

from app import webpage
from app.db import add_webpage_source, create_project, list_sources
from app.webpage import WebpageError, fetch_webpage, validate_url


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])


@pytest.fixture
def mock_http(monkeypatch, public_dns):
    real_client = httpx.Client

    def install(handler):
        def client(**kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['follow_redirects'] is False
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(webpage.httpx, 'Client', client)
    return install


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://example.org', 'http://localhost', 'http://sub.localhost',
    'http://host.local', 'https://linkedin.com/feed', 'https://www.linkedin.com',
    'http://user:password@example.org', 'not a URL', 'http://', 'http://example.org:bad',
])
def test_reject_invalid_urls_before_dns(url):
    with pytest.raises(WebpageError):
        validate_url(url)


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.1.2.3', '172.16.0.1', '192.168.1.1',
                                  '169.254.169.254', '0.0.0.0', '::1', 'fc00::1', 'fe80::1',
                                  '::ffff:127.0.0.1', '224.0.0.1', '100.64.0.1'])
def test_reject_unsafe_dns_and_literal_targets(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(0, 0, 0, '', (ip, 80))])
    for host in ['example.org', f'[{ip}]' if ':' in ip else ip]:
        with pytest.raises(WebpageError, match='public'):
            validate_url(f'http://{host}')


def test_reject_mixed_public_private_dns(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(0, 0, 0, '', (ip, 80)) for ip in ['93.184.216.34', '10.0.0.1']])
    with pytest.raises(WebpageError):
        validate_url('https://example.org')


def test_extract_and_persist_with_pinned_destination(database_path, mock_http):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.host == '93.184.216.34'
        assert request.headers['host'] == 'example.org'
        assert request.extensions['sni_hostname'] == 'example.org'
        if request.url.path == '/start':
            return httpx.Response(302, headers={'location': '/article'})
        return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'}, text='''
            <html><head><title> Useful &amp; clear </title><style>hide</style></head>
            <body><nav>Menu</nav><main><h1>Heading</h1><p>Hello <b>world</b> &amp; friends.</p>
            <script>secret()</script></main></body></html>''')
    mock_http(handler)
    project = create_project(database_path, 'Web')
    source = add_webpage_source(database_path, project.id, 'https://example.org/start')
    assert source.original_url == 'https://example.org/start'
    assert source.final_url == 'https://example.org/article'
    assert source.title == 'Useful & clear'
    assert source.content == 'Heading\nHello world & friends.'
    assert list_sources(database_path, project.id) == [source]
    assert len(requests) == 2


@pytest.mark.parametrize('response', [
    httpx.Response(500), httpx.Response(404),
    httpx.Response(200, headers={'content-type': 'text/html'}, text='<script>only code</script>'),
    httpx.Response(200, headers={'content-type': 'text/html'}, text='<title>Only a title</title>'),
    httpx.Response(200, headers={'content-type': 'application/pdf'}, content=b'PDF'),
    httpx.Response(200, headers={'content-type': 'text/plain'}, text=' '),
    httpx.Response(200, headers={'content-type': 'text/plain'}, content=b'x' * (webpage.MAX_BYTES + 1)),
    httpx.Response(302),
])
def test_failed_extraction_never_saved(database_path, mock_http, response):
    mock_http(lambda request: response)
    project = create_project(database_path, 'Failed')
    with pytest.raises(WebpageError):
        add_webpage_source(database_path, project.id, 'https://example.org')
    assert list_sources(database_path, project.id) == []


@pytest.mark.parametrize('target', ['http://localhost/admin', 'file:///etc/passwd', 'https://linkedin.com', 'http://private.example'])
def test_redirect_targets_revalidated(mock_http, monkeypatch, target):
    calls = []
    def dns(host, *args, **kwargs):
        return [(0, 0, 0, '', ('10.0.0.1' if host == 'private.example' else '93.184.216.34', 80))]
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': target})
    mock_http(handler)
    with pytest.raises(WebpageError):
        fetch_webpage('https://example.org')
    assert len(calls) == 1


def test_timeout_and_redirect_limit(mock_http):
    def timeout(request):
        raise httpx.ReadTimeout('timeout')
    mock_http(timeout)
    with pytest.raises(WebpageError, match='Could not fetch'):
        fetch_webpage('https://example.org')
    mock_http(lambda request: httpx.Response(302, headers={'location': '/again'}))
    with pytest.raises(WebpageError, match='too many'):
        fetch_webpage('https://example.org')


def test_plain_text(mock_http):
    mock_http(lambda request: httpx.Response(200, headers={'content-type': 'text/plain'}, text='Original\ntext'))
    assert fetch_webpage('http://example.org').content == 'Original\ntext'


def test_article_structure_links_and_boilerplate():
    from pathlib import Path
    parser = webpage.ReadableHTML('https://example.org/blog/post/')
    parser.feed((Path(__file__).parent / 'fixtures/webpage/article.html').read_text())
    title, text = parser.extract()
    assert title == 'Technical article & notes'
    for expected in ['Local development — café', 'By an engineer', 'Setup', '1. Install tools',
                     '- Keep Unicode: α × 2', '2. Run tests',
                     'if ready:\n    run("α")\n\n    verify()', '| Device | Memory', '| GPU | 128 GB',
                     'Figure 1. Test configuration.', 'Technical note:', 'Article footnote:',
                     'the guide (https://example.org/blog/guide?q=1#setup)']:
        assert expected in text
    assert text.count('Repeat this important warning.') == 2
    assert text.count('https://example.org/api') == 1
    for excluded in ['Menu links', 'Site header', 'Wrong card', 'Unrelated title', 'Share this',
                     'Hidden clone', 'Hidden copy', 'Hidden styling', 'Visitor comments', 'Site footer', 'javascript:']:
        assert excluded not in text
    assert 'Setup section' in text and 'Unsafe link' in text


def test_body_fallback_preserves_short_prose_and_code():
    from pathlib import Path
    parser = webpage.ReadableHTML()
    parser.feed((Path(__file__).parent / 'fixtures/webpage/fallback.html').read_text())
    title, text = parser.extract()
    assert title == 'Fallback'
    assert 'A short page' in text and 'Navigation is a legitimate topic' in text
    assert 'Related posts are discussed' in text
    assert '  indented()\n\n  done()' in text
    assert 'Menu' not in text and 'Site footer' not in text


@pytest.mark.parametrize('html,expected', [
    ('<body>Outside<main><article><a href="/card">Tiny card</a></article><p>Main text</p></main></body>', 'Main text'),
    ('<body>Outside<div role="main"><h1>Short main</h1></div></body>', 'Short main'),
    ('<body><main hidden>Hidden</main><p>Fallback text</p></body>', 'Fallback text'),
    ('<body><main><p>One<p>Two<br/>Three</main><nav>Menu</nav></body>', 'One\nTwo\nThree'),
    ('<body><article><p>' + 'Substantive prose. ' * 20 + '</p></article><article>Small card</article></body>', 'Substantive prose.'),
    ('<body><main><div>' * 500 + 'Deep content', 'Deep content'),
])
def test_region_selection_and_imperfect_html(html, expected):
    parser = webpage.ReadableHTML('https://example.org/page')
    parser.feed(html)
    _, text = parser.extract()
    assert expected in text
    assert 'Outside' not in text and 'Hidden' not in text and 'Menu' not in text


def test_article_links_resolve_against_redirect_target(mock_http):
    def handler(request):
        if request.url.path == '/start':
            return httpx.Response(302, headers={'location': '/docs/page/'})
        return httpx.Response(200, headers={'content-type': 'text/html'},
                              text='<main><p>See <a href="../guide">guide</a>.</p></main>')
    mock_http(handler)
    assert fetch_webpage('https://example.org/start').content == 'See guide (https://example.org/docs/guide).'


@pytest.mark.parametrize('close_taxonomy', [False, True])
def test_verified_post_wrappers_preserve_article_and_attribution(close_taxonomy):
    from pathlib import Path
    html = (Path(__file__).parent / 'fixtures/webpage/nvidia-post.html').read_text()
    if close_taxonomy:
        # Also test biography/comments as siblings: taxonomy removal must not mask missing rules.
        html = html.replace('    <div class="post-authors-list">', '  </div>\n    <div class="post-authors-list">')
    parser = webpage.ReadableHTML('https://example.org/blog/post/')
    parser.feed(html)
    title, text = parser.extract()
    assert title == 'Development on one machine | Technical Blog'
    for expected in ['Development on one machine', 'Code, test, and deploy locally', 'Oct 16, 2026',
                     'By An Engineer (https://example.org/blog/author/engineer/)',
                     'Technical content: 128 GB, FP4, CUDA, and α.', 'Setup', '- Install tools', '- Run tests',
                     'setup instructions (https://example.org/docs/setup)', 'Figure 1. Test configuration.',
                     'Footnote: measurements depend on configuration.', 'AI-Generated Summary',
                     'AI-generated content may summarize information incompletely. Verify important information.',
                     'Learn more (https://example.org/terms/)', 'Like other models',
                     'Tags and Comments are also programming concepts.', 'https://www.linkedin.com/company/example/']:
        assert expected in text
    for unwanted in ['Discuss (0)', 'twitter.com/intent', 'reddit.com/submit', 'Metadata-only category',
                     'metadata-only', 'About the Authors', 'Biography-only prose', 'View all posts', 'Comment-only prose']:
        assert unwanted not in text
    assert not {'Like', 'Tags', 'Comments'} & set(text.splitlines())


@pytest.mark.parametrize('marker', ['entry-meta-social', 'entry-meta-social-links-list', 'sticky-share-buttons',
                                   'post--rate', 'post--comments-count', 'entry-content-author', 'entry-content-comments'])
def test_verified_marker_exclusion_in_body_fallback(marker):
    parser = webpage.ReadableHTML('https://example.org/')
    parser.feed(f'<body><p>Article text</p><div class="other {marker}">Control text</div></body>')
    assert parser.extract()[1] == 'Article text'


@pytest.mark.parametrize('attrs,removed', [
    ('class="card--post-attributes-secondary tags"', True),
    ('class="tags other card--post-attributes-secondary"', True),
    ('class="tags"', False), ('class="card--post-attributes-secondary"', False),
    ('class="card--post-attributes-secondary" id="tags"', False),
    ('class="entry-content-author-guide"', False), ('class="post-authors author"', False),
])
def test_taxonomy_requires_both_exact_classes(attrs, removed):
    parser = webpage.ReadableHTML()
    parser.feed(f'<body><p>Article text</p><div {attrs}>Preserved unless taxonomy</div></body>')
    assert ('Preserved unless taxonomy' not in parser.extract()[1]) == removed
