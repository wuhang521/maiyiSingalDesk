"""No live providers/private policy: real HTTPX MockTransport and fake policy."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
import ssl
import socket
import stat
from types import SimpleNamespace
from xml.sax.saxutils import escape

import httpx
import pytest

from app import health_collector as service

NOW = datetime(2026, 10, 30, 2, 30, tzinfo=timezone.utc)
DAY = NOW.date()


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    link: str
    published: str
    query: str


class Insufficient(Exception):
    pass


def policy(*, require_two=False, reject_all=False):
    renders = []
    def dedupe(items):
        unique = {}
        for item in items:
            unique.setdefault((item.title, item.link), item)
        return list(unique.values())
    def render(items, target_date, *, min_items, max_items):
        renders.append({'items': list(items), 'date': target_date, 'min': min_items, 'max': max_items})
        if require_two and len(items) < 2:
            raise Insufficient()
        return '\n\n'.join(f'### {index}. {item.title}\n来源：{item.source}\n链接：{item.link}\n主要内容：合成业务摘要'
                           for index, item in enumerate(items[:max_items], 1))
    return SimpleNamespace(NewsItem=NewsItem, NotEnoughNewsError=Insufficient,
        dedupe_news_items=dedupe, is_relevant_health_business_item=lambda item: not reject_all and '业务' in item.title,
        generate_health_business_markdown=render, renders=renders)


def item(index=1, *, hours=1, title=None, link=None, published=None):
    return {'title': title or f'合成健康业务{index}',
            'link': link or f'https://example.test/business-{index}',
            'published': published if published is not None else format_datetime(NOW - timedelta(hours=hours))}


def rss(items=()):
    records = []
    for value in items:
        records.append('<item>' + ''.join(f'<{tag}>{escape(value.get(key, ""))}</{tag}>'
            for tag, key in [('title', 'title'), ('link', 'link'), ('pubDate', 'published')]) + '</item>')
    return ('<?xml version="1.0" encoding="UTF-8"?><rss><channel>' + ''.join(records) + '</channel></rss>').encode()


def client_for(replies, *, private_defaults=False):
    requests = []
    def handle(request):
        requests.append(request)
        response = replies[len(requests) - 1]
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, bytes):
            return httpx.Response(200, content=response)
        return response
    options = {'transport': httpx.MockTransport(handle), 'trust_env': False}
    if private_defaults:
        options.update(auth=httpx.BasicAuth('synthetic-only', 'synthetic-only'),
                       cookies={'synthetic_cookie': 'local-only'}, headers={'X-Private-Test': 'local-only'})
    return httpx.Client(**options), requests


def collect(tmp_path, replies, *, business=None, maximum=100, private_defaults=False):
    client, requests = client_for(replies, private_defaults=private_defaults)
    try:
        result = service.collect_markdown(tmp_path, DAY, maximum, client=client, now=NOW,
                                          business=business or policy())
        return result, requests
    finally:
        client.close()


@pytest.mark.parametrize('maximum', [1, 7, 100])
def test_business_policy_keeps_min_one_and_configured_maximum(tmp_path, maximum):
    business = policy()
    result, requests = collect(tmp_path, [rss([item(index) for index in range(1, 111)]), rss()],
                               business=business, maximum=maximum)
    assert result.summary['article_count'] == maximum
    assert result.summary['window_hours'] == 24
    assert business.renders[0]['min'] == 1 and business.renders[0]['max'] == maximum
    assert len(requests) == 2


@pytest.mark.parametrize('maximum', [0, 101])
def test_invalid_maximum_is_rejected_before_network(tmp_path, maximum):
    client, requests = client_for([])
    with client, pytest.raises(service.CollectorFailure, match='invalid_max_items'):
        service.collect_markdown(tmp_path, DAY, maximum, client=client, now=NOW, business=policy())
    assert requests == []


@pytest.mark.parametrize('response,error', [(rss(), None), (b'not xml', 'bad_xml'),
    (httpx.Response(302, headers={'Location': 'https://example.test/redirect'}), 'redirect'),
    (httpx.Response(503, content=b'synthetic upstream unavailable'), 'http_status'),
    (httpx.ReadTimeout('synthetic timeout'), 'timeout')])
def test_unusable_sources_report_bounded_metadata_without_articles(tmp_path, response, error):
    with pytest.raises(service.CollectorFailure) as failure:
        collect(tmp_path, [response, response])
    assert failure.value.code == 'no_qualified_news'
    assert failure.value.summary['article_count'] == 0
    if error:
        assert all(provider['error'] == error for provider in failure.value.summary['providers'])
    serialized = json.dumps(failure.value.summary)
    assert 'https://' not in serialized and 'not xml' not in serialized


def test_tls_failure_is_classified_and_other_source_remains_usable(tmp_path):
    error = httpx.ConnectError('synthetic TLS failure')
    error.__cause__ = ssl.SSLError('synthetic certificate validation')
    result, _ = collect(tmp_path, [error, rss([item()])])
    assert result.summary['article_count'] == 1
    assert result.summary['providers'][0]['error'] == 'tls'
    assert result.summary['providers'][1]['status'] == 'parsed'


def test_24h_window_is_preferred_and_applied_before_deduplication(tmp_path):
    business = policy()
    old, fresh = item(hours=40), item(hours=2)
    result, _ = collect(tmp_path, [rss([old]), rss([fresh])], business=business)
    assert result.summary['window_hours'] == 24 and len(business.renders) == 1
    assert business.renders[0]['items'][0].published == fresh['published']
    assert result.summary['windows'][0]['input_count'] == 1


def test_render_insufficient_24h_falls_back_to_72h(tmp_path):
    business = policy(require_two=True)
    result, _ = collect(tmp_path, [rss([item(1), item(2, hours=40)]), rss()], business=business)
    assert result.summary['window_hours'] == 72 and result.summary['article_count'] == 2
    assert result.summary['windows'][0]['render_insufficient'] is True
    assert [len(value['items']) for value in business.renders] == [1, 2]


def test_all_business_irrelevant_items_fail_without_fabricated_markdown(tmp_path):
    business = policy(reject_all=True)
    with pytest.raises(service.CollectorFailure) as failure:
        collect(tmp_path, [rss([item()]), rss()], business=business)
    assert failure.value.code == 'no_qualified_news' and not business.renders
    assert all(window['relevant_count'] == 0 for window in failure.value.summary['windows'])


def test_future_missing_naive_old_and_non_https_or_authenticated_links_are_excluded(tmp_path):
    invalid = [item(2, hours=-1), item(3, published=''), item(4, published='Fri, 30 Oct 2026 01:30:00'),
               item(5, published='not-a-date'), item(6, hours=73), item(7, link='http://example.test/unsafe'),
               item(8, link='https://synthetic:local-only@example.test/private')]
    result, _ = collect(tmp_path, [rss([item(), *invalid]), rss()])
    provider = result.summary['providers'][0]
    assert result.summary['article_count'] == 1
    assert provider['future_date'] == provider['missing_fields'] == provider['older_than_72h'] == 1
    assert provider['invalid_date'] == provider['invalid_link'] == 2


@pytest.mark.parametrize('link', [
    'https://localhost/article', 'https://nested.localhost/article', 'https://host.local/article',
    'https://singlelabel/article', 'https://127.0.0.1/article', 'https://[::1]/article',
    'https://10.0.0.1/article', 'https://172.16.0.1/article', 'https://192.168.0.1/article',
    'https://169.254.1.1/article', 'https://100.64.0.1/article', 'https://[fc00::1]/article',
    'https://[fe80::1]/article', 'https://224.0.0.1/article',
    'https://example.invalid:abc/article', 'https://example.invalid:0/article',
    'https://example.invalid:80/article', 'https://example.invalid:8443/article',
    'https://example.invalid:65536/article',
    'https://@example.invalid/article', 'https://ｌｏｃａｌｈｏｓｔ.local/article',
    'https://a。local/article', 'https://[2606:4700:4700::1111%lo0]/article',
    'https://[::ffff:192.168.0.1]/article', 'https://2130706433/article',
    'https://0x7f.0.0.1/article',
])
def test_local_private_multicast_and_non_https_ports_are_rejected_without_dns(tmp_path, monkeypatch, link):
    def no_dns(*args, **kwargs):
        raise AssertionError('No DNS request is permitted in this synthetic contract')
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
    result, _ = collect(tmp_path, [rss([item(), item(2, link=link)]), rss()])
    assert result.summary['article_count'] == 1
    assert result.summary['providers'][0]['invalid_link'] == 1
    assert link not in result.markdown


@pytest.mark.parametrize('link', ['https://example.invalid/article', 'https://example.invalid:443/article',
                                 'https://8.8.8.8/article', 'https://[2606:4700:4700::1111]/article'])
def test_public_fqdn_and_global_ip_https_links_are_allowed_without_dns(tmp_path, monkeypatch, link):
    def no_dns(*args, **kwargs):
        raise AssertionError('No DNS request is permitted in this synthetic contract')
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
    result, _ = collect(tmp_path, [rss([item(link=link)]), rss()])
    assert result.summary['article_count'] == 1 and link in result.markdown
    serialized = json.dumps(result.summary, ensure_ascii=False)
    assert link not in serialized and '合成健康业务' not in serialized and '合成业务摘要' not in serialized


@pytest.mark.parametrize('declaration', ['<!DOCTYPE rss [<!ENTITY synthetic "expanded">]>', '<!ENTITY synthetic "expanded">'])
def test_dtd_and_entity_declarations_are_rejected(tmp_path, declaration):
    body = declaration.encode() + rss([item()])
    with pytest.raises(service.CollectorFailure) as failure:
        collect(tmp_path, [body, rss()])
    assert failure.value.summary['providers'][0]['error'] == 'xml_declaration'


def test_utf16_internal_entity_cannot_bypass_dtd_rejection(tmp_path):
    text = rss([item()]).decode('utf-8').replace('encoding="UTF-8"', 'encoding="UTF-16"')
    text = text.replace('<rss>', '<!DOCTYPE rss [<!ENTITY synthetic "合成健康业务实体">]><rss>')
    text = text.replace('合成健康业务1', '&synthetic;')
    with pytest.raises(service.CollectorFailure) as failure:
        collect(tmp_path, [text.encode('utf-16'), rss()])
    assert failure.value.code == 'no_qualified_news'
    assert failure.value.summary['providers'][0]['error'] == 'xml_declaration'


def test_provider_item_limit_is_enforced_before_business_policy(tmp_path):
    business = policy()
    with pytest.raises(service.CollectorFailure) as failure:
        collect(tmp_path, [rss([item(index) for index in range(service.MAX_PROVIDER_ITEMS + 1)]), rss()], business=business)
    assert failure.value.summary['providers'][0]['error'] == 'item_limit'
    assert not business.renders


@pytest.mark.parametrize('advertised', [True, False])
def test_provider_response_byte_limit_rejects_large_body(tmp_path, monkeypatch, advertised):
    monkeypatch.setattr(service, 'MAX_PROVIDER_BYTES', 100)
    body = rss([item()])
    response = httpx.Response(200, content=body, headers={'Content-Length': str(len(body))}) if advertised else body
    if not advertised:
        # HTTPX synthesizes Content-Length for bytes; remove it to exercise
        # the streamed count rather than just the preflight header guard.
        response = httpx.Response(200, content=body)
        del response.headers['Content-Length']
    with pytest.raises(service.CollectorFailure) as failure:
        collect(tmp_path, [response, rss()])
    first = failure.value.summary['providers'][0]
    assert first['error'] == 'response_size'
    assert first['response_bytes'] == (0 if advertised else len(body))


def test_total_response_budget_keeps_first_usable_source_without_parsing_second(tmp_path, monkeypatch):
    first, second = rss([item()]), rss([item(2)])
    monkeypatch.setattr(service, 'MAX_TOTAL_BYTES', len(first) + len(second) - 1)
    result, _ = collect(tmp_path, [first, second])
    assert result.summary['article_count'] == 1
    assert result.summary['providers'][1]['error'] == 'response_size'


def test_borrowed_client_auth_cookies_headers_never_reach_public_sources(tmp_path):
    result, requests = collect(tmp_path, [rss([item()]), rss()], private_defaults=True)
    assert result.summary['article_count'] == 1
    for request, source in zip(requests, service.SOURCES):
        assert str(request.url) == source.url and request.method == 'GET'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert 'x-private-test' not in request.headers
        assert request.headers['Accept-Encoding'] == 'identity'
        assert request.extensions['timeout'] == service.REQUEST_TIMEOUT.as_dict()


def test_owned_client_uses_strict_tls_no_proxy_or_redirect_and_is_closed(tmp_path, monkeypatch):
    client, requests = client_for([rss([item()]), rss()])
    options = []
    monkeypatch.setattr(service.httpx, 'Client', lambda **kwargs: options.append(kwargs) or client)
    result = service.collect_markdown(tmp_path, DAY, 1, now=NOW, business=policy())
    assert result.summary['article_count'] == 1 and client.is_closed
    assert options[0]['verify'] is True and options[0]['trust_env'] is False
    assert options[0]['follow_redirects'] is False


def test_write_outputs_are_atomic_and_leave_no_temporary_on_replace_failure(tmp_path, monkeypatch):
    output = tmp_path / 'private-output'
    output.mkdir(mode=0o700)
    target = output / (DAY.isoformat() + '.md')
    target.write_bytes(b'previous synthetic bytes')
    result = service.CollectionResult('new synthetic markdown', {'status': 'prepared', 'article_count': 1})
    def interrupted(*args):
        raise OSError('synthetic replace failure')
    monkeypatch.setattr(service.os, 'replace', interrupted)
    with pytest.raises(OSError):
        service.write_outputs(output, DAY, result)
    assert target.read_bytes() == b'previous synthetic bytes'
    assert sorted(path.name for path in output.iterdir()) == [target.name]


@pytest.mark.skipif(os.name != 'posix', reason='POSIX mode/UID contract; Windows ACL is a separate acceptance')
def test_private_output_0700_and_regular_files_0600(tmp_path):
    output = tmp_path / 'private-output'
    output.mkdir(mode=0o700)
    result = service.CollectionResult('synthetic markdown', {'status': 'prepared'})
    service.write_outputs(output, DAY, result)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    output.chmod(0o750)
    with pytest.raises(service.CollectorFailure, match='private_output_required'):
        service.write_outputs(output, DAY, result)


@pytest.mark.parametrize('success', [True, False])
def test_cli_prints_only_safe_summary_not_upstream_diagnostics(tmp_path, monkeypatch, capsys, success):
    output = tmp_path / 'private-output'
    output.mkdir(mode=0o700)
    def collector(*args):
        print('synthetic private diagnostic https://example.test/not-for-output')
        if not success:
            raise service.CollectorFailure('no_qualified_news')
        return service.CollectionResult('synthetic nonpublic body', {'status': 'prepared', 'article_count': 1})
    monkeypatch.setattr(service, 'collect_markdown', collector)
    code = service.main(['--collector-root', str(tmp_path), '--date', DAY.isoformat(), '--output-dir', str(output)])
    captured = capsys.readouterr()
    assert code == (0 if success else 1) and captured.err == ''
    parsed = json.loads(captured.out)
    assert parsed['status'] == ('prepared' if success else 'failed')
    assert all(value not in captured.out for value in ('https://', 'diagnostic', 'nonpublic body'))


def test_cli_environment_scrub_retains_only_explicit_runtime_names(monkeypatch):
    # Replace the mapping, never read or remove the host's real environment.
    synthetic = {'PATH': '/synthetic/runtime', 'LANG': 'C.UTF-8', 'LC_ALL': 'C',
                 'HTTPS_PROXY': 'https://synthetic.proxy.invalid', 'HOME': '/synthetic/home',
                 'NTFY_TOKEN': 'synthetic-only', 'TASK_API_KEY': 'synthetic-only',
                 'DATABASE_URL': 'sqlite://synthetic-only'}
    monkeypatch.setattr(service.os, 'environ', synthetic)
    service._scrub_cli_environment()
    assert synthetic == {'PATH': '/synthetic/runtime', 'LANG': 'C.UTF-8', 'LC_ALL': 'C'}
