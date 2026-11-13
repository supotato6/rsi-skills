"""Exercise portable discovery, score provenance and bounded pagination."""
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/atomic/rsi-kaggle-open-sota/scripts/discover.py"
spec = importlib.util.spec_from_file_location("kaggle_open_sota", SCRIPT)
discovery = importlib.util.module_from_spec(spec)
# Do not create bytecode inside the installable skill resource tree.
exec(compile(SCRIPT.read_bytes(), str(SCRIPT), "exec"), discovery.__dict__)


def notebook(ref, score=None, **extra):
    result = {"scriptUrl": f"/code/{ref}", "title": ref, **extra}
    if score is not None:
        result["bestPublicScore"] = score
    return result


class FakePages:
    def __init__(self, pages, is_max=True, leaderboard=None):
        self.pages = pages
        self.requests = []
        self.is_max = is_max
        self.leaderboard_payload = leaderboard or {"publicLeaderboard": []}

    def competition(self, slug):
        return {"id": 123, "competitionName": slug, "title": "Example",
                "evaluationAlgorithm": {"name": "Example metric", "isMax": self.is_max}}

    def notebooks(self, request):
        self.requests.append(request)
        return self.pages[len(self.requests) - 1]

    def leaderboard(self, competition_id):
        return self.leaderboard_payload

    def notebook_view(self, url, version_id=0):
        raise OSError("Notebook version evidence unavailable")


def version_view(ref="alice/example", score=0.8, version_id=900, number=26):
    return {"kernel": {"id": 42, "url": f"/code/{ref}", "bestPublicScore": score},
            "kernelRun": {"id": version_id, "kernelId": 42, "status": "COMPLETE",
                          "kernelVersionNumber": number},
            "currentVersionNumber": number,
            "dataSources": [{"sourceId": 123}],
            "renderableDataSources": [{"dataSourceUrl": "/competitions/example"}],
            "bestSubmissionScore": {"scoreFormatted": str(score), "kernelVersionNumber": 3,
                "kernelSessionUrl": f"/code/{ref}?scriptVersionId=300"}}


class FakeVersions(FakePages):
    def __init__(self, current=None, best=None):
        super().__init__([{"kernels": [notebook("alice/example", 0.8, scriptVersionId=900)]}])
        self.current = current if current is not None else version_view()
        self.best = best if best is not None else version_view(version_id=300, number=3)
        self.version_requests = []

    def notebook_view(self, url, version_id=0):
        self.version_requests.append((url, version_id))
        return self.best if version_id else self.current


class KaggleOpenSota(unittest.TestCase):
    def test_buildings_v15_and_traffic_v3_best_mapping(self):
        # Synthetic API envelopes, using the regression cases' version identities.
        for score,number,run_id,is_max in [(.01126,15,364583447,False),(.87212,3,361713245,True)]:
            current=version_view(score=score,number=26)
            best=version_view(score=score,number=number,version_id=run_id)
            best['dataSources'].append({'sourceType':'DATA_SOURCE_TYPE_DATASET_VERSION',
                'sourceId':98765,'datasetId':456,'mountSlug':'synthetic-weights'})
            for view in (current,best):
                view['bestSubmissionScore'].update(scoreFormatted=str(score),kernelVersionNumber=number,
                    kernelSessionUrl=f'/code/alice/example?scriptVersionId={run_id}')
            client=FakeVersions(current,best)
            client.pages=[{'kernels':[notebook('alice/example',score,scriptVersionId=900)]}]
            client.is_max=is_max
            with tempfile.TemporaryDirectory() as directory:
                result=discovery.discover(client,'example','auto',1,50,1,Path(directory))
            candidate=result['candidates'][0]
            self.assertEqual(candidate['best_score_version_number'],number)
            self.assertEqual(candidate['best_score_version_id'],run_id)
            self.assertEqual(candidate['current_version_number'],26)
            self.assertEqual(candidate['version_resolution']['status'],'confirmed')
            self.assertEqual(candidate['dependency_snapshot']['sources'][1]['sourceId'],98765)
            self.assertEqual(candidate['dependency_snapshot']['completeness'],'not_independently_verified')

    def test_public_leader_boundary_quarantines_anomalies_and_refills_top(self):
        class Tracking(FakePages):
            def notebook_view(self, url, version_id=0):
                self.reviewed.append(url)
                return super().notebook_view(url, version_id)

        board = {"publicLeaderboard": [
            {"teamId": 1, "rank": 1, "displayScore": "0.85"},
            {"teamId": 2, "rank": 2, "displayScore": "0.80"}]}
        pages = [{"kernels": [notebook("alice/huge", 1645.5), notebook("bob/large", 1019.8)]},
                 {"kernels": [notebook("carol/suspect", 0.90), notebook("dave/normal", 0.80)]},
                 {"kernels": [notebook("eve/normal", 0.66)]}]
        with tempfile.TemporaryDirectory() as temporary:
            client = Tracking(pages, leaderboard=board)
            client.reviewed = []
            report = discovery.discover(client, "example", "auto", 2, 2, 3, Path(temporary))
            self.assertEqual([r["public_score"] for r in report["candidates"]], [0.80, 0.66])
            self.assertEqual([r["public_score"] for r in report["anomalous_candidates"]], [1645.5, 1019.8, 0.90])
            self.assertEqual(len(client.reviewed), 2)
            self.assertEqual([r["discovery_rank"] for r in report["candidates"]], [1, 2])
            self.assertEqual(report["coverage"]["scored_notebooks"], 5)
            self.assertEqual(report["coverage"]["anomalous_scored_notebooks"], 3)
            self.assertEqual(report["score_boundary"]["leaderboard_best"], "0.85")
            self.assertEqual(report["score_boundary"]["as_of"], report["leaderboard"]["retrieved_at"])
            self.assertEqual(report["score_boundary"]["competition_id"], 123)
            for row in report["anomalous_candidates"]:
                self.assertEqual(row["score_validation"]["status"], "suspect")
                self.assertIsNone(row["leaderboard_position"])
                self.assertIsNone(row["discovery_rank"])
                self.assertTrue(row["code_url"].startswith("https://www.kaggle.com/code/"))
            raw = json.loads((Path(temporary) / "page-001.json").read_text())
            self.assertEqual(raw["response"]["kernels"][0]["bestPublicScore"], 1645.5)

    def test_lower_metric_and_absolute_tolerance(self):
        for is_max, best, keep, reject in [
                (True, "0.85", [0.85, 0.85000001, 0.8], 0.85000002),
                (False, "0.15", [0.15, 0.14999999, 0.2], 0.14999998),
                (True, "0", [0, 0.00000001, -0.1], 0.00000002)]:
            with self.subTest(is_max=is_max, best=best), tempfile.TemporaryDirectory() as temporary:
                board = {"publicLeaderboard": [{"teamId": 1, "rank": 1, "displayScore": best}]}
                page = {"kernels": [notebook(f"alice/v{i}", score) for i, score in enumerate(keep + [reject])]}
                report = discovery.discover(FakePages([page], is_max=is_max, leaderboard=board),
                                            "example", "auto", 10, 50, 1, Path(temporary))
                self.assertEqual(sorted(r["public_score"] for r in report["candidates"]), sorted(keep))
                self.assertEqual([r["public_score"] for r in report["anomalous_candidates"]], [reject])
                self.assertTrue(all(r["score_validation"]["status"] == "within_bound" for r in report["candidates"]))

    def test_missing_or_inconsistent_boundary_does_not_label_scores_valid(self):
        for board in [{"privateLeaderboard": [{"teamId": 1, "rank": 1, "displayScore": "0.8"}]},
                      {"publicLeaderboard": [{"teamId": 2, "rank": 2, "displayScore": "0.8"}]},
                      {"publicLeaderboard": [{"teamId": 1, "rank": 1, "displayScore": "0.5"},
                                             {"teamId": 2, "rank": 2, "displayScore": "0.8"}]}]:
            with self.subTest(board=board), tempfile.TemporaryDirectory() as temporary:
                report = discovery.discover(FakePages([{"kernels": [notebook("alice/high", 1000)]}], leaderboard=board),
                                            "example", "auto", 1, 50, 1, Path(temporary))
                self.assertEqual(report["score_boundary"]["status"], "unavailable")
                self.assertEqual(report["candidates"][0]["score_validation"]["status"], "unchecked")
                self.assertIsNone(report["candidates"][0]["leaderboard_position"])
                self.assertEqual(report["anomalous_candidates"], [])

    def test_all_anomalies_never_fall_back_to_the_highest_suspect(self):
        board = {"publicLeaderboard": [{"teamId": 1, "rank": 1, "displayScore": "0.8"}]}
        with tempfile.TemporaryDirectory() as temporary:
            report = discovery.discover(FakePages([{"kernels": [notebook("alice/high", 1000)]}], leaderboard=board),
                                        "example", "auto", 1, 50, 1, Path(temporary))
            self.assertEqual(report["candidates"], [])
            self.assertEqual(len(report["anomalous_candidates"]), 1)

    def test_single_notebook_cannot_bypass_score_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeVersions()
            client.leaderboard_payload = {"publicLeaderboard": [{"teamId": 1, "rank": 1, "displayScore": "0.7"}]}
            report = discovery.discover_notebook(client, "https://www.kaggle.com/code/alice/example", "auto", Path(temporary))
            self.assertEqual(report["candidates"], [])
            self.assertEqual(report["anomalous_candidates"][0]["public_score"], 0.8)
            self.assertEqual(len(client.version_requests), 1)
            self.assertIn("notebook-input.json", [row["file"] for row in report["raw_files"]])

    def test_best_score_pins_older_version_and_retains_current_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeVersions()
            report = discovery.discover(client, "example", "auto", 1, 50, 1, Path(temporary))
            item = report["candidates"][0]
            self.assertEqual(item["current_version_id"], 900)
            self.assertEqual(item["current_version_number"], 26)
            self.assertEqual(item["best_score_version_id"], 300)
            self.assertEqual(item["best_score_version_number"], 3)
            self.assertEqual(item["best_score_code_url"], "https://www.kaggle.com/code/alice/example?scriptVersionId=300")
            self.assertEqual(item["version_resolution"]["status"], "confirmed")
            self.assertEqual(client.version_requests[-1][1], 300)
            self.assertTrue((Path(temporary) / "notebook-001-best.json").exists())

    def test_version_resolution_rejects_wrong_score_competition_and_link(self):
        for case in ["score", "competition", "multiple_competitions", "link", "other_notebook",
                     "run", "number", "status", "missing", "malformed", "pinned_competition"]:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                client = FakeVersions()
                if case == "score":
                    client.current["bestSubmissionScore"]["scoreFormatted"] = "0.7"
                elif case == "competition":
                    client.current["dataSources"] = [{"sourceId": 456}]
                elif case == "multiple_competitions":
                    client.current["dataSources"].append({"sourceId": 456})
                elif case == "link":
                    client.current["bestSubmissionScore"]["kernelSessionUrl"] = "https://evil.test/code/alice/example?scriptVersionId=300"
                elif case == "other_notebook":
                    client.current["bestSubmissionScore"]["kernelSessionUrl"] = "/code/bob/example?scriptVersionId=300"
                elif case == "run":
                    client.best["kernelRun"]["id"] = 900
                elif case == "number":
                    client.best["currentVersionNumber"] = 26
                elif case == "status":
                    client.best["kernelRun"]["status"] = "ERROR"
                elif case == "malformed":
                    client.current["bestSubmissionScore"] = "changed schema"
                elif case == "pinned_competition":
                    client.best["dataSources"] = [{"sourceId": 456}]
                else:
                    del client.current["bestSubmissionScore"]
                report = discovery.discover(client, "example", "auto", 1, 50, 1, Path(temporary))
                item = report["candidates"][0]
                self.assertEqual(item["public_score"], 0.8)
                self.assertIsNone(item["best_score_code_url"])
                self.assertIsNone(item["best_score_version_id"])
                self.assertEqual(item["version_resolution"]["status"], "unconfirmed")

    def test_zero_score_and_formatted_rounding_are_valid_version_evidence(self):
        for score, formatted in [(0, "0.00000"), (0.8123456, "0.81235")]:
            with self.subTest(score=score), tempfile.TemporaryDirectory() as temporary:
                client = FakeVersions(current=version_view(score=score), best=version_view(score=score, version_id=300, number=3))
                client.current["bestSubmissionScore"]["scoreFormatted"] = formatted
                client.pages[0]["kernels"][0]["bestPublicScore"] = score
                report = discovery.discover(client, "example", "auto", 1, 50, 1, Path(temporary))
                self.assertEqual(report["candidates"][0]["best_score_version_id"], 300)

    def test_notebook_input_derives_competition_without_scanning_code_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeVersions()
            report = discovery.discover_notebook(client, "https://www.kaggle.com/code/alice/example?scriptVersionId=900",
                                                  "auto", Path(temporary))
            self.assertEqual(report["competition"], "example")
            self.assertEqual(report["coverage"]["scope"], "single_notebook")
            self.assertEqual(report["coverage"]["pages_fetched"], 0)
            self.assertEqual(client.requests, [])
            self.assertEqual(report["candidates"][0]["best_score_version_number"], 3)
            self.assertEqual(report["candidates"][0]["evidence_url"], "https://www.kaggle.com/code/alice/example")
            self.assertIn("notebook-input.json", report["candidates"][0]["version_resolution"]["evidence_files"])
            self.assertIn("notebook-input.json", [row["file"] for row in report["raw_files"]])

    def test_unscored_notebook_retains_input_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeVersions()
            del client.current["kernel"]["bestPublicScore"]
            report = discovery.discover_notebook(client, "https://www.kaggle.com/code/alice/example", "auto", Path(temporary))
            self.assertEqual(report["candidates"], [])
            self.assertEqual(report["coverage"]["unscored_notebooks"], 1)
            self.assertIn("notebook-input.json", [row["file"] for row in report["raw_files"]])

    def test_notebook_input_does_not_guess_between_competitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeVersions()
            client.current["renderableDataSources"].append({"dataSourceUrl": "/competitions/another"})
            with self.assertRaises(ValueError):
                discovery.discover_notebook(client, "https://www.kaggle.com/code/alice/example", "auto", Path(temporary))
            self.assertEqual(client.requests, [])
            self.assertTrue((Path(temporary) / "notebook-input.json").is_file())

    def test_best_score_mapping_handles_lower_metric_and_current_best(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeVersions()
            client.is_max = False
            client.current["bestSubmissionScore"].update(kernelVersionNumber=26,
                kernelSessionUrl="/code/alice/example?scriptVersionId=900")
            client.best = client.current
            report = discovery.discover(client, "example", "auto", 1, 50, 1, Path(temporary))
            self.assertEqual(report["direction"], "lower")
            self.assertEqual(report["candidates"][0]["best_score_version_id"], 900)

    def test_http_session_identifies_client_and_preserves_xsrf_cookie(self):
        requests = []

        class Pages(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, payload, *, cookie=False):
                requests.append((self.command, self.path, dict(self.headers)))
                # Kaggle currently returns an empty 404 for this incomplete UA.
                if self.headers.get('User-Agent') == 'Mozilla/5.0':
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                if cookie:
                    self.send_header('Set-Cookie', 'XSRF-TOKEN=fixture%2Btoken; Path=/')
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                self.respond({}, cookie=True)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path.endswith('/GetCompetition'):
                    self.respond({'id': 123, 'competitionName': body['competitionName']})
                else:
                    self.respond({'kernels': []})

        server = HTTPServer(('127.0.0.1', 0), Pages)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(discovery, 'BASE', f'http://127.0.0.1:{server.server_port}'):
                client = discovery.KagglePages('example', 2)
                self.assertEqual(client.competition('example')['id'], 123)
                self.assertEqual(client.notebooks(discovery.list_request(123, 'lower', 1, 50)), {'kernels': []})
            self.assertEqual(len(requests), 3)
            for method, path, headers in requests:
                self.assertIn('rsi-kaggle-open-sota/', headers['User-Agent'])
                if method == 'POST':
                    self.assertEqual(headers['X-Xsrftoken'], 'fixture+token')
                    self.assertIn('XSRF-TOKEN=fixture%2Btoken', headers['Cookie'])
        finally:
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()

    def test_rank_uses_public_snapshot_time_and_excludes_unranked_teams(self):
        payload = {"publicLeaderboard": [
            {"teamId": 1, "rank": 1, "displayScore": "0.700"},
            {"teamId": 2, "rank": 2, "displayScore": "0.636"},
            {"teamId": 3, "rank": 3, "displayScore": "0.636"},
            {"teamId": 4, "rank": 4, "displayScore": "0.500"},
            {"teamId": 5, "displayScore": "0.999"},
        ]}
        pages = [{"kernels": [notebook("alice/best", 0.636)]}]
        with tempfile.TemporaryDirectory() as temporary:
            report = discovery.discover(FakePages(pages, leaderboard=payload), "example", "auto", 1, 50, 1, Path(temporary))
            self.assertEqual(report["direction_label"], "越高越好")
            self.assertEqual(report["leaderboard"]["ranked_teams"], 4)
            self.assertEqual(report["leaderboard"]["excluded_unranked_rows"], 1)
            position = report["candidates"][0]["leaderboard_position"]
            self.assertEqual(position["as_of"], report["leaderboard"]["retrieved_at"])
            self.assertEqual(position["kind"], "score_estimate")
            self.assertEqual(position["new_entry_rank_range"], {"best": 2, "worst": 4})
            self.assertEqual(position["existing_same_score_rank_range"], {"first": 2, "last": 3})
            self.assertTrue((Path(temporary) / "leaderboard.json").is_file())

    def test_lower_rank_projection_and_incomplete_snapshot(self):
        payload = {"publicLeaderboard": [{"teamId": i, "rank": i, "displayScore": score}
            for i, score in enumerate(["0.1", "0.2", "0.2", "0.9"], 1)]}
        snapshot, rows = discovery.leaderboard_snapshot(payload, "example", "2026-10-24T02:00:00+08:00")
        position = discovery.score_position(0.2, "lower", snapshot, rows)
        self.assertEqual(position["strictly_better_teams"], 1)
        self.assertEqual(position["new_entry_rank_range"], {"best": 2, "worst": 4})
        payload["publicLeaderboard"].pop(1)
        snapshot, rows = discovery.leaderboard_snapshot(payload, "example", "2026-10-24T02:00:00+08:00")
        self.assertEqual(snapshot["status"], "incomplete")
        self.assertIsNone(discovery.score_position(0.2, "lower", snapshot, rows))

    def test_unavailable_leaderboard_retains_scores_without_inventing_rank(self):
        class MissingLeaderboard(FakePages):
            def leaderboard(self, competition_id):
                raise OSError("Leaderboard unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            client = MissingLeaderboard([{"kernels": [notebook("alice/a", 0.636)]}])
            report = discovery.discover(client, "example", "auto", 1, 50, 1, Path(temporary))
            self.assertEqual(report["candidates"][0]["public_score"], 0.636)
            self.assertIsNone(report["candidates"][0]["leaderboard_position"])
            self.assertEqual(report["leaderboard"]["status"], "unavailable")

    def test_inputs_and_unknown_direction(self):
        self.assertEqual(discovery.competition_slug("https://www.kaggle.com/competitions/example/leaderboard?tab=public"), "example")
        self.assertEqual(discovery.competition_slug("https://kaggle.com/c/example/code"), "example")
        for invalid in ("https://evil.test/competitions/example", "https://kaggle.com.evil.test/competitions/example", "../example",
                        "https://www.kaggle.com/competitions/example/leaderboard?tab=private"):
            with self.assertRaises(ValueError):
                discovery.competition_slug(invalid)
        with self.assertRaises(ValueError):
            discovery.direction_for({}, "auto")
        self.assertEqual(discovery.direction_for({}, "lower"), ("lower", "explicit"))

    def test_paginated_scores_pins_zero_and_best_version_provenance(self):
        pinned = notebook("alice/starter", 0.322, totalVotes=999)
        pages = [
            {"totalCount": 0, "kernels": [notebook("bob/best", 0.636, scriptVersionId=999),
                notebook("carol/second", 0.628)], "pinnedKernels": [pinned]},
            {"kernels": [notebook("dave/zero", 0),
                notebook("eve/no-score", title="LB 0.999", totalVotes=2000)], "pinnedKernels": [pinned]},
            {"kernels": []},
        ]
        client = FakePages(pages)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = discovery.discover(client, "example", "auto", 10, 2, 4, output)
            self.assertEqual([r["public_score"] for r in report["candidates"]], [0.636, 0.628, 0.322, 0])
            self.assertEqual(report["coverage"]["unique_notebooks"], 5)
            self.assertEqual(report["coverage"]["unscored_notebooks"], 1)
            self.assertEqual(report["coverage"]["stop_reason"], "end_of_results")
            self.assertEqual(report["candidates"][0]["current_version_id"], 999)
            self.assertIsNone(report["candidates"][0]["best_score_version_id"])
            self.assertNotIn("scriptVersionId", report["candidates"][0]["evidence_url"])
            self.assertEqual([r["kernelFilterCriteria"]["listRequest"]["page"] for r in client.requests], [1, 2, 3])
            snapshot = json.loads((output / "page-001.json").read_text())
            self.assertEqual(snapshot["response"]["totalCount"], 0)
            self.assertEqual(report["raw_files"][1]["sha256"], discovery.hashlib.sha256((output / "page-001.json").read_bytes()).hexdigest())

    def test_lower_scores_and_bounded_coverage(self):
        page = {"kernels": [notebook("alice/a", 8), notebook("bob/b", 2)]}
        with tempfile.TemporaryDirectory() as temporary:
            report = discovery.discover(FakePages([page], is_max=False), "example", "auto", 1, 2, 1, Path(temporary))
            self.assertEqual(report["candidates"][0]["public_score"], 2)
            self.assertEqual(report["coverage"]["stop_reason"], "page_limit")
            client = FakePages([page, page], is_max=False)
            report = discovery.discover(client, "example", "auto", 10, 2, 4, Path(temporary))
            self.assertEqual(report["coverage"]["stop_reason"], "repeated_page")
            self.assertEqual(client.requests[0]["kernelFilterCriteria"]["listRequest"]["sortBy"], "SCORE_ASCENDING")

    def test_installed_helper_is_self_contained(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "runtime skills"
            subprocess.run([sys.executable, str(ROOT / "scripts/skills.py"), "install", "--target", str(target)],
                           check=True, capture_output=True, text=True)
            helper = target / "rsi-kaggle-open-sota/scripts/discover.py"
            result = subprocess.run([sys.executable, str(helper), "--help"], cwd=temporary, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--output-dir", result.stdout)


if __name__ == "__main__":
    unittest.main()
