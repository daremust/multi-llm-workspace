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
