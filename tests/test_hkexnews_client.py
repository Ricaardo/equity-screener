from __future__ import annotations

from unittest import TestCase

from ah_screener.sources.hkexnews_client import (
    normalize_hkex_url,
    parse_hkex_jsonp,
    parse_hkex_title_response,
)


class HkexnewsClientTest(TestCase):
    def test_parses_stock_search_jsonp(self) -> None:
        payload = parse_hkex_jsonp(
            'callback({"more":"1","stockInfo":[{"stockId":7609,"code":"00700","name":"TENCENT"}]});'
        )

        self.assertEqual(payload["stockInfo"][0]["stockId"], 7609)
        self.assertEqual(payload["stockInfo"][0]["code"], "00700")

    def test_parses_title_search_result_and_normalizes_url(self) -> None:
        rows = parse_hkex_title_response(
            {
                "result": (
                    '[{"NEWS_ID":"1","TITLE":"ANNUAL REPORT","LONG_TEXT":"Announcements",'
                    '"FILE_LINK":"/listedco/listconews/sehk/2026/0101/demo.pdf"}]'
                )
            }
        )

        self.assertEqual(rows[0]["TITLE"], "ANNUAL REPORT")
        self.assertEqual(
            normalize_hkex_url(rows[0]["FILE_LINK"]),
            "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0101/demo.pdf",
        )
