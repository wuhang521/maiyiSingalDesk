import http.client
import json
import socket
import time
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from app import channel_website as website


class WebsiteSourceTests(unittest.TestCase):
    def test_extracts_matching_links_in_document_order_and_limits_unique_items(self):
        html = """
        <aside><a href="/noise">外侧内容</a></aside>
        <ul id="news-list">
          <li><a href="/notice?id=17&access_token=secret">新版<b>指南</b></a>
          <li><a href="/notice?id=17">新版指南</a>
          <li><a href="javascript:alert(1)">更新说明</a>
          <li><a href="/notice/3">最新文章</a>
        </ul>
        """
        with patch.object(website, "_download_page", return_value=html):
            items = website.fetch_website_items("https://example.com/news", "#news-list > li a", 2)
        self.assertEqual(items, [
            website.WebsiteItem("新版指南", "新版指南", "https://example.com/notice?id=17"),
            website.WebsiteItem("更新说明", "更新说明", "https://example.com/news"),
        ])

    def test_selects_specific_element_and_ignores_non_visible_content(self):
        html = """
        <main>
          <article id="report" class="news top">
            <h2>监管公告</h2><p>今天更新。</p>
            <script>取不到我</script><style>.news {color: red;}</style>
            <p hidden>不公开内容</p><a href="/report">原文</a>
          </article>
          <section><article class="news top"><h2>其他结果</h2></article></section>
        </main>
        """
        with patch.object(website, "_download_page", return_value=html):
            items = website.fetch_website_items("https://example.com/reports", "main > article.news.top", 10)
        self.assertEqual(items, [
            website.WebsiteItem("监管公告", "监管公告 今天更新。 原文", "https://example.com/report"),
        ])

    def test_invalid_source_and_selector_are_rejected_without_dns(self):
        invalid = [
            "file:///etc/passwd", "http://127.0.0.1/", "http://169.254.169.254/",
            "http://[::ffff:127.0.0.1]/", "http://[64:ff9b::a00:1]/", "http://168.63.129.16/",
            "http://127.1/", "http://0x7f.0.0.1/", "http://printer.local/", "https://example.com:8443/",
            "https://user:pass@example.com/", "https://example.com/a?page=2;accessToken=private",
            "https://example.com/?%61pi_key=private",
        ]
        with patch.object(website.socket, "getaddrinfo") as dns:
            for url in invalid:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    website.validate_website_config(url, "main")
            for selector in ["", "main, article", "a[href]", "p:nth-child(1)", "main >> a"]:
                with self.subTest(selector=selector), self.assertRaises(ValueError):
                    website.validate_website_config("https://example.com/?page=2", selector)
            website.validate_website_config("https://例子.公司/news?page=2", "main > .notice")
            dns.assert_not_called()

    def test_mixed_dns_answers_are_rejected_before_connection(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.5", 443)),
        ]
        with patch.object(website.socket, "getaddrinfo", return_value=answers):
            with patch.object(website.socket, "socket") as connect:
                with self.assertRaisesRegex(website.WebsiteFetchError, "非公网"):
                    website.fetch_website_items("https://example.com/", "main", 1)
                connect.assert_not_called()

    def test_connect_uses_validated_sockaddr_and_preserves_tls_hostname(self):
        target = website._target("https://example.com/news")
        sockaddr = ("93.184.215.14", 443)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", sockaddr)]
        raw = Mock()
        raw.getpeername.return_value = sockaddr
        tls = Mock()
        with patch.object(website.socket, "socket", return_value=raw), patch.object(
            website.ssl, "create_default_context", return_value=tls,
        ):
            connection = website._PinnedConnection(target, addresses, time.monotonic() + 10)
            connection.connect()
        raw.connect.assert_called_once_with(sockaddr)
        tls.wrap_socket.assert_called_once_with(raw, server_hostname="example.com")

    def test_redirect_and_size_rejected_and_no_followup_get(self):
        class FakeSocket:
            def __init__(self, raw):
                self.raw = raw

            def makefile(self, *_):
                return BytesIO(self.raw)

        target = website._target("https://example.com/消息?page=2")
        responses = [
            (b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\nContent-Length: 0\r\n\r\n", "跳转"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 50\r\n\r\n" + b"x" * 50, "超过"),
            (b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n" + b"x" * 50, "超过"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 15\r\n\r\nx", "不完整|未传输完整"),
        ]
        for raw, error in responses:
            with self.subTest(error=error):
                connection = Mock()
                response = http.client.HTTPResponse(FakeSocket(raw))
                response.begin()
                connection.getresponse.return_value = response
                with patch.object(website, "_resolve_public", return_value=[]), patch.object(
                    website, "_PinnedConnection", return_value=connection,
                ), patch.object(website, "MAX_BYTES", 30):
                    with self.assertRaisesRegex(website.WebsiteFetchError, error):
                        website._download_page(target, time.monotonic() + 2)
                connection.request.assert_called_once()
                connection.close.assert_called_once()
                self.assertEqual(connection.request.call_args.args[1], "/%E6%B6%88%E6%81%AF?page=2")

    def test_gbk_decoding_and_empty_match(self):
        html = "<html><meta charset=gb2312><p>监管公告</p></html>"
        self.assertEqual(website._decode_page(html.encode("gbk"), "text/html"), html)
        with patch.object(website, "_download_page", return_value=html):
            with self.assertRaisesRegex(website.WebsiteFetchError, "没有找到"):
                website.fetch_website_items("https://example.com/", "#not-found", 1)

    def test_rss_extracts_public_links_and_plain_descriptions(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>Skip private destination</title><link>http://127.0.0.1/</link></item>
          <item><title>Food safety &amp; recalls</title><link>/alerts/2026?token=private</link>
            <description><![CDATA[<p>Official notice.</p><script>hide me</script>]]></description></item>
          <item><title>Food safety &amp; recalls</title><link>/alerts/2026</link>
            <description>Official notice.</description></item>
          <item><title>Medical device notice</title><link>/alerts/2027</link></item>
        </channel></rss>"""
        with patch.object(website, "_download_feed", return_value=feed):
            items = website.fetch_website_items("https://example.com/feed.rss", "@rss", 2)
        self.assertEqual(items, [
            website.WebsiteItem("Food safety & recalls", "Official notice.", "https://example.com/alerts/2026"),
            website.WebsiteItem("Medical device notice", "Medical device notice", "https://example.com/alerts/2027"),
        ])

    def test_atom_uses_alternate_link_and_resolves_xml_base(self):
        feed = """<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.com/notices/">
          <entry><title>公共卫生更新</title><link rel="self" href="item.atom" />
            <link href="item-1"/><summary>正式发布</summary></entry>
        </feed>"""
        with patch.object(website, "_download_feed", return_value=feed):
            items = website.fetch_website_items("https://example.com/feeds/news.atom", "@rss", 1)
        self.assertEqual(items, [website.WebsiteItem("公共卫生更新", "正式发布", "https://example.com/notices/item-1")])

    def test_rss_rejects_xml_entities_and_non_feed_documents(self):
        invalid = [
            ("<!DOCTYPE rss [<!ENTITY leak SYSTEM 'file:///etc/passwd'>]><rss/>", "XML 声明"),
            ("<html><a href='/article'>not a feed</a></html>", "RSS 或 Atom"),
            ("<rss><channel>", "无法解析"),
            ("<rss><channel/></rss>", "没有可读取"),
        ]
        for feed, error in invalid:
            with self.subTest(error=error), patch.object(website, "_download_feed", return_value=feed):
                with self.assertRaisesRegex(website.WebsiteFetchError, error):
                    website.fetch_website_items("https://example.com/feed.rss", "@rss", 2)

    def test_fsa_adapter_requires_exact_official_https_url(self):
        with patch.object(website.socket, "getaddrinfo") as dns:
            website.validate_website_config("https://data.food.gov.uk/food-alerts/id.json?_limit=8", "@fsa-alerts")
            for url in ["https://example.com/food-alerts/id.json", "http://data.food.gov.uk/food-alerts/id.json",
                        "https://data.food.gov.uk/food-alerts/id.html"]:
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, "官方 HTTPS"):
                    website.validate_website_config(url, "@fsa-alerts")
            dns.assert_not_called()

    def test_fsa_reads_official_alert_title_link_and_risk(self):
        records = {"items": [
            {"title": "Off-origin link", "alertURL": "https://example.com/alert"},
            {"title": "Food product recalled", "notation": "FSA-AA-00-2026", "created": "2026-11-10",
             "modified": "2026-11-10T20:44:14.168Z", "alertURL": "https://alerts.food.gov.uk/news-alerts/alert/fsa-aa-00-2026",
             "problem": [{"riskStatement": "Contains undeclared soya."}]},
        ]}
        with patch.object(website, "_download_notices", return_value=json.dumps(records)):
            items = website.fetch_website_items("https://data.food.gov.uk/food-alerts/id.json?_limit=8", "@fsa-alerts", 5)
        self.assertEqual(items, [website.WebsiteItem(
            "Food product recalled", "FSA-AA-00-2026 2026-11-10 2026-11-10T20:44:14.168Z Contains undeclared soya.",
            "https://alerts.food.gov.uk/news-alerts/alert/fsa-aa-00-2026",
        )])

    def test_openfda_merges_recall_categories_and_limits_large_responses(self):
        numbers = {"food": "H-0184-2027", "drug": "D-0103-2027", "device": "Z-0285-2027"}

        def response(target, _deadline):
            kind = target.path.split("/")[1]
            if kind == "device" and target.query.endswith("limit=10"):
                raise website.WebsiteResponseTooLarge("large device report")
            return json.dumps({"results": [{
                "recall_number": numbers[kind], "report_date": "20261104", "classification": "Class II",
                "product_description": f"{kind} product", "reason_for_recall": "Safety notice",
            }]})

        with patch.object(website, "_download_notices", side_effect=response) as download:
            items = website.fetch_website_items("https://api.fda.gov/", "@openfda", 3)
        self.assertEqual(download.call_count, 4)
        self.assertEqual(len(items), 3)
        for item, (kind, number) in zip(items, numbers.items()):
            self.assertIn(number, item.title)
            self.assertIn("2026-11-04 Class II Safety notice", item.text)
            self.assertEqual(item.url, f"https://api.fda.gov/{kind}/enforcement.json?search=recall_number.exact%3A%22{number}%22&limit=1")
        with self.assertRaisesRegex(ValueError, "官方 HTTPS"):
            website.validate_website_config("https://example.com/", "@openfda")

    def test_cdc_fsis_keeps_only_verified_usda_origins(self):
        urls = {
            1: "https://www.fda.gov/safety/recall",
            2: "http://www.fsis.usda.gov/recalls-alerts/meat-alert?permalink=tracking",
            3: "https://www.fsis.usda.gov.example.com/recalls-alerts/other",
            4: "https://www.fsis.usda.gov/recalls-alerts/meat-alert",
        }
        links = "".join(f"<item><link>https://tools.cdc.gov/api/embed/downloader/download.asp?m=379374&amp;c={i}"
                        "</link></item>" for i in urls)

        def record(target, *_):
            item_id = int(target.path.rsplit("/", 1)[-1].removesuffix(".json"))
            return json.dumps({"results": [{"id": item_id, "sourceUrl": urls[item_id],
                                           "name": "Meat alert", "description": "<p>Notice</p>"}]})

        with patch.object(website, "_download_feed", return_value=f"<rss><channel>{links}</channel></rss>"), patch.object(
            website, "_download_document", side_effect=record,
        ):
            items = website.fetch_website_items(website._CDC_FSIS_FEED, "@cdc-fsis", 8)
        self.assertEqual(items, [website.WebsiteItem("Meat alert", "Notice", "https://www.fsis.usda.gov/recalls-alerts/meat-alert")])
        with self.assertRaisesRegex(ValueError, "CDC 官方"):
            website.validate_website_config("https://tools.cdc.gov/different.rss", "@cdc-fsis")
