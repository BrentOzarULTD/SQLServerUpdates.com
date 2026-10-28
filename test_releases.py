"""Regression checks for install instructions, migrated KB URLs, and build sorting."""
import csv
import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import build
import poll_feed as poll


class OpenAIRequestTests(unittest.TestCase):
    def api_error(self, code, kind="insufficient_quota", retry_after=None):
        headers = {"x-request-id": "req-test"}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return urllib.error.HTTPError(poll.OPENAI_URL, 429, "Too Many Requests",
            headers, io.BytesIO(json.dumps({"error": {
                "code": code, "type": kind, "message": "secret-test-key"
            }}).encode()))

    def test_quota_error_is_specific_redacted_and_never_retried(self):
        for code in ("insufficient_quota", "credit_balance_exhausted",
                     "project_spend_limit_exceeded", "organization_spend_limit_exceeded",
                     "organization_usage_limit_exceeded"):
            with self.subTest(code=code), \
                    patch.object(poll.urllib.request, "urlopen",
                                 side_effect=self.api_error(code)) as request, \
                    patch.object(poll.time, "sleep") as sleep:
                with self.assertRaises(poll.OpenAIRequestError) as caught:
                    poll.openai_request("secret-test-key", {})
                message = str(caught.exception)
                self.assertIn(f"code={code}", message)
                self.assertIn("request_id=req-test", message)
                self.assertNotIn("secret-test-key", message)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_temporary_rate_limit_retries_and_recovers(self):
        response = io.BytesIO(b'{"choices": []}')
        with patch.object(poll.urllib.request, "urlopen", side_effect=[
                self.api_error("rate_limit_exceeded", "requests", "5"), response]) as request, \
                patch.object(poll.time, "sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(poll.openai_request("secret-test-key", {}), {"choices": []})
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(5)

    def test_retries_are_bounded(self):
        errors = [self.api_error("rate_limit_exceeded", "requests") for _ in range(3)]
        with patch.object(poll.urllib.request, "urlopen", side_effect=errors) as request, \
                patch.object(poll.time, "sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(poll.OpenAIRequestError, "rate limit persists"):
                poll.openai_request("secret-test-key", {})
            self.assertEqual(request.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_non_json_error_still_reports_status(self):
        error = urllib.error.HTTPError(poll.OPENAI_URL, 403, "Forbidden", {},
                                      io.BytesIO(b'not JSON: secret-test-key'))
        with patch.object(poll.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(poll.OpenAIRequestError, "OpenAI HTTP 403") as caught:
                poll.openai_request("secret-test-key", {})
            self.assertNotIn("secret-test-key", str(caught.exception))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.cu = {"label": "CU9", "build": "17.0.5005.3",
                   "kb_url": "https://support.microsoft.com/help/5122048"}
        self.gdr = {"label": "CU9 + GDR", "build": "17.0.5015.4",
                    "kb_url": "https://support.microsoft.com/en-us/help/5126967"}
        self.home = {"home_table": {"rows": [{
            "version": "SQL Server 2025", "build": "17.0.5005.3",
            "latest_update": '<a href="https://example.com/download">Download RTM</a> then '
                             '<a href="https://support.microsoft.com/help/5122048">CU9</a>'}]}}

    def test_gdr_keeps_separate_cu_download(self):
        self.assertTrue(poll.apply_home_update(self.home, "2025", self.gdr["label"],
                        self.gdr["build"], self.gdr["kb_url"], [self.cu]))
        self.assertEqual(self.home["home_table"]["rows"][0]["latest_update"],
                         '<a href="https://example.com/download">Download RTM</a> then '
                         '<a href="https://support.microsoft.com/help/5122048">CU9</a> then '
                         '<a href="https://support.microsoft.com/en-us/help/5126967">GDR</a>')

    def test_next_cu_removes_previous_gdr(self):
        self.test_gdr_keeps_separate_cu_download()
        poll.apply_home_update(self.home, "2025", "CU10", "17.0.5025.1",
                               "https://support.microsoft.com/help/9999999", [self.cu])
        path = self.home["home_table"]["rows"][0]["latest_update"]
        self.assertEqual(path.count("<a "), 2)
        self.assertIn(">CU10</a>", path)
        self.assertNotIn("GDR", path)
        self.assertNotIn("CU9", path)

    def test_same_build_can_repair_combined_link(self):
        row = self.home["home_table"]["rows"][0]
        row["build"] = self.gdr["build"]
        row["latest_update"] = ('<a href="https://example.com/download">Download RTM</a> then '
                                '<a href="https://learn.microsoft.com/en-us/help/5126967">CU9 + GDR</a>')
        self.test_gdr_keeps_separate_cu_download()
        self.assertFalse(poll.apply_home_update(self.home, "2025", self.gdr["label"],
                         self.gdr["build"], self.gdr["kb_url"], [self.cu]))

    def test_rtm_gdr_cannot_replace_higher_cu_build(self):
        before = self.home["home_table"]["rows"][0].copy()
        self.assertFalse(poll.apply_home_update(self.home, "2025", "GDR", "17.0.1145.4",
                         "https://support.microsoft.com/help/5128492", []))
        self.assertEqual(before, self.home["home_table"]["rows"][0])

    def test_missing_base_cu_stops_incomplete_instructions(self):
        with self.assertRaisesRegex(ValueError, "separate CU9 download is missing"):
            poll.home_install_path("", self.gdr["label"], self.gdr["kb_url"], [])

    def test_service_pack_install_step_preserved(self):
        value = ('<a href="https://example.com/download">Download RTM</a> then '
                 '<a href="https://example.com/sp3">SP3</a> then '
                 '<a href="https://example.com/old">GDR</a>')
        result = poll.home_install_path(value, "CU17 + GDR", "https://example.com/gdr",
                  [{"label": "CU17", "kb_url": "https://example.com/cu17"}])
        self.assertIn('>SP3</a> then <a href="https://example.com/cu17">CU17</a>', result)
        self.assertEqual(result.count("<a "), 4)

    def test_support_table_relative_links_and_metadata(self):
        page = ('<h3 id="sql-server-2025">SQL Server 2025</h3><table><tr>'
                '<td>17.0.5015.4</td><td>None</td><td>CU9 + GDR</td>'
                '<td><a href="/en-us/help/5126967">KB5126967</a></td>'
                '<td>October 13, 2026</td></tr></table>')
        updates = poll.parse_latest_updates(page)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["kb_url"], self.gdr["kb_url"])
        self.assertEqual(poll.table_release_metadata(page, self.gdr["build"]),
                         (self.gdr["kb_url"], "2026/10/13"))
        self.assertEqual(poll.microsoft_url("https://learn.microsoft.com/en-us/help/5126967"),
                         self.gdr["kb_url"])

    def test_rendered_table_sorts_numeric_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "updates").mkdir()
            with (data / "updates" / "fixture.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerows([
                    ["Cumulative Update", "Build"], ["RTM GDR", "17.0.1145.4"],
                    ["CU8", "17.0.4075.5"], ["CU9 GDR", "17.0.5015.4"],
                    ["legacy", "&nbsp;"], ["CU9", "17.0.5005.3"],
                    ["numeric not lexical", "17.0.999.10"],
                ])
            with patch.object(build, "DATA", data):
                header, rows = build.load_csv("fixture")
            self.assertEqual([row[1] for row in rows], ["17.0.5015.4", "17.0.5005.3",
                             "17.0.4075.5", "17.0.1145.4", "17.0.999.10", "&nbsp;"])
            self.assertEqual(len(rows), 6)

    def test_table_only_release_updates_home_once_without_a_blog_post(self):
        page = ('<h3 id="sql-server-2025">SQL Server 2025</h3><table><tr>'
                '<td>17.0.5015.4</td><td>None</td><td>CU9 + GDR</td>'
                '<td><a href="/en-us/help/5126967">KB5126967</a></td>'
                '<td>October 13, 2026</td></tr></table>')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            (data / "updates").mkdir(parents=True)
            for slug in poll.VERSION_TO_SLUG.values():
                with (data / "updates" / f"{slug}.csv").open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["Cumulative Update", "Release Date", "Build"])
                    if slug == poll.VERSION_TO_SLUG["2025"]:
                        writer.writerow(['<a href="https://support.microsoft.com/help/5122048">CU9</a>',
                                         "2026/09/15", "17.0.5005.3"])
            (data / "versions.json").write_text(json.dumps(self.home))
            def fetch(url):
                return "<rss><channel/></rss>" if url == poll.FEED_URL else page
            with patch.object(poll, "ROOT", root), patch.object(poll, "DATA", data), \
                    patch.object(poll, "http_get", side_effect=fetch), \
                    patch.dict(poll.os.environ, {"OPENAI_API_KEY": "offline-test"}), \
                    patch.object(poll, "llm_extract") as llm, contextlib.redirect_stdout(io.StringIO()):
                poll.run()
                first = (data / "versions.json").read_text()
                poll.run()
                self.assertEqual(first, (data / "versions.json").read_text())
                llm.assert_not_called()
            row = json.loads(first)["home_table"]["rows"][0]
            self.assertEqual(row["build"], "17.0.5015.4")
            self.assertIn(">CU9</a> then <a", row["latest_update"])
            self.assertIn('https://support.microsoft.com/en-us/help/5126967">GDR</a>', row["latest_update"])
            with (data / "updates" / "sql-server-2025-updates.csv").open(newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)


if __name__ == "__main__":
    unittest.main()
