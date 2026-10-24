import json
from datetime import date
from io import BytesIO
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pandas as pd

import tempo_client


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class TempoClientTests(unittest.TestCase):
    def test_fetches_all_pages_with_date_range_and_bearer_auth(self) -> None:
        original_page_size = tempo_client.PAGE_SIZE
        tempo_client.PAGE_SIZE = 2
        requests = []
        payloads = [
            {"results": [{"tempoWorklogId": 1}, {"tempoWorklogId": 2}]},
            {"results": [{"tempoWorklogId": 3}]},
        ]

        def fake_urlopen(request: object, timeout: int) -> FakeResponse:
            requests.append(request)
            return FakeResponse(payloads[len(requests) - 1])

        try:
            with patch("tempo_client.urlopen", side_effect=fake_urlopen):
                records = tempo_client.fetch_tempo_worklogs(
                    "test-token",
                    date(2026, 10, 19),
                    date(2026, 10, 23),
                    worker_id="712020:worker-id",
                )
        finally:
            tempo_client.PAGE_SIZE = original_page_size

        self.assertEqual([record["tempoWorklogId"] for record in records], [1, 2, 3])
        self.assertEqual(
            requests[0].get_header("Authorization"),
            "Bearer test-token",
        )
        self.assertEqual(
            parse_qs(urlparse(requests[1].full_url).query)["offset"],
            ["2"],
        )
        query = parse_qs(urlparse(requests[0].full_url).query)
        self.assertEqual(query["from"], ["2026-10-19"])
        self.assertEqual(query["to"], ["2026-10-23"])
        self.assertIn(
            "/4/worklogs/user/712020%3Aworker-id",
            requests[0].full_url,
        )

    def test_normalizes_flattened_tempo_worklogs(self) -> None:
        records = [
            {
                "tempoWorklogId": 1,
                "startDate": "2026-10-23",
                "author": {"displayName": "Avery Chen", "accountId": "avery-id"},
                "issue": {
                    "key": "WEB-123",
                    "summary": "Responsive layout",
                    "project": {"key": "WEB", "name": "Website"},
                },
                "description": "Completed responsive testing",
                "timeSpentSeconds": 14400,
                "billableSeconds": 10800,
            },
            {
                "tempoWorklogId": 2,
                "startDate": "2026-10-23",
                "author": {"displayName": "Avery Chen", "accountId": "avery-id"},
                "issue": {"key": "TIMEOFF-1", "project": {"key": "TIMEOFF"}},
                "description": "Public Holiday - Keka",
                "timeSpentSeconds": 28800,
                "billableSeconds": 0,
            },
        ]
        logs = tempo_client.normalize_tempo_records(records)

        self.assertEqual(logs["resource"].tolist(), ["Avery Chen", "Avery Chen"])
        self.assertEqual(logs["project"].tolist(), ["WEB", "TIMEOFF"])
        self.assertEqual(logs["hours"].tolist(), [4.0, 8.0])
        self.assertEqual(logs["billable_hours"].tolist(), [3.0, 0.0])
        self.assertTrue(logs["is_leave"].tolist()[1])
        self.assertEqual(logs["leave_type"].tolist(), ["", "Public Holiday"])

    def test_raw_csv_flattens_nested_api_fields(self) -> None:
        csv_bytes = tempo_client.tempo_records_to_csv(
            [{"author": {"displayName": "Avery"}, "timeSpentSeconds": 3600}]
        )
        raw = pd.read_csv(BytesIO(csv_bytes))
        self.assertIn("author.displayName", raw.columns)
        self.assertEqual(raw.loc[0, "timeSpentSeconds"], 3600)

    def test_normalizes_tempo_api_records_that_only_have_ids(self) -> None:
        logs = tempo_client.normalize_tempo_records(
            [
                {
                    "author": {"accountId": "712020:worker-id"},
                    "issue": {"id": 4321, "self": "https://api.tempo.io/4/issues/4321"},
                    "startDate": "2026-10-23",
                    "description": "Work item details",
                    "timeSpentSeconds": 7200,
                    "billableSeconds": 3600,
                }
            ]
        )

        self.assertEqual(logs.loc[0, "resource"], "Tempo user 712020:worker-id")
        self.assertEqual(logs.loc[0, "project"], "Issue ID 4321")
        self.assertEqual(logs.loc[0, "work_item"], "Issue ID 4321")
        self.assertEqual(logs.loc[0, "hours"], 2.0)
        self.assertEqual(logs.loc[0, "billable_hours"], 1.0)

    def test_rejects_invalid_range_before_network_request(self) -> None:
        with patch("tempo_client.urlopen") as mocked_urlopen:
            with self.assertRaisesRegex(ValueError, "start date"):
                tempo_client.fetch_tempo_worklogs(
                    "test-token", date(2026, 10, 24), date(2026, 10, 23)
                )
        mocked_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
