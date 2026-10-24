import unittest
from unittest.mock import patch

import pandas as pd

from ai_agent import AnalysisResult, analyze_worklogs


class TimesheetAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worklogs = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-10-19"),
                    "resource": "Alex",
                    "project": "Website",
                    "description": "Implemented responsive navigation",
                    "hours": 6.0,
                    "billable_hours": 5.0,
                    "is_leave": False,
                },
                {
                    "date": pd.Timestamp("2026-10-20"),
                    "resource": "Sam",
                    "project": "Website",
                    "description": "No description provided",
                    "hours": 4.0,
                    "billable_hours": 2.0,
                    "is_leave": False,
                },
                {
                    "date": pd.Timestamp("2026-10-21"),
                    "resource": "Alex",
                    "project": "TIMEOFF",
                    "description": "Public Holiday",
                    "hours": 8.0,
                    "billable_hours": 0.0,
                    "is_leave": True,
                },
            ]
        )

    def test_local_analysis_reports_observed_billing_and_description_gaps(self) -> None:
        result = analyze_worklogs(self.worklogs)

        self.assertEqual(result.status, "Local analysis")
        self.assertEqual(len(result.findings), 3)
        self.assertIn("7.0 of 10.0 active work hours", result.findings[0]["evidence"])
        self.assertIn("1 worklog entries", result.findings[2]["evidence"])
        self.assertNotIn("TIMEOFF", result.findings[1]["title"])

    @patch(
        "ai_agent._generate_analysis_with_gemini",
        return_value=[
            {
                "type": "Allocation",
                "title": "Website work is concentrated",
                "evidence": "10 hours on Website.",
                "analysis": "One project accounts for most active work.",
                "recommendation": "Compare with project capacity.",
            }
        ],
    )
    def test_uses_gemini_findings_when_configured(self, generate: object) -> None:
        result = analyze_worklogs(self.worklogs, api_key="test-key")

        self.assertEqual(result.status, "Gemini AI")
        self.assertEqual(len(result.findings), 1)
        generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
