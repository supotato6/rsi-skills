#!/usr/bin/env python3
"""Find public Kaggle notebook scores and verify the code version behind each best score."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import http.cookiejar
import json
import math
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE = "https://www.kaggle.com"
# Kaggle returns an empty HTTP 404 for the incomplete browser UA "Mozilla/5.0".
# Identify this client explicitly on both page and JSON requests.
USER_AGENT = "rsi-kaggle-open-sota/0.2"
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,100}\Z")
CODE_PATH = re.compile(r"/code/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)\Z")
SCORE_EPSILON = Decimal("1e-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def competition_slug(value):
    if SLUG.fullmatch(value):
        return value
    parsed = urlsplit(value)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme not in {"http", "https"} or parsed.netloc not in {"www.kaggle.com", "kaggle.com"}
            or len(parts) < 2 or parts[0] not in {"competitions", "c"} or not SLUG.fullmatch(parts[1])):
        raise ValueError("Expected a Kaggle competition slug, competition URL or leaderboard URL")
    selectors = {"tab", "board", "leaderboard", "leaderboardtype"}
    if "private" in parts[2:] or any(value.casefold() == "private"
            for key, values in parse_qs(parsed.query).items() if key.casefold() in selectors for value in values):
        raise ValueError("This helper retrieves Public Score; a Private leaderboard needs separate evidence")
    return parts[1]


def direction_for(metadata, requested):
    if requested != "auto":
        return requested, "explicit"
    is_max = (metadata.get("evaluationAlgorithm") or {}).get("isMax")
    if not isinstance(is_max, bool):
        raise ValueError("Metric direction unavailable; check Evaluation and pass --direction higher or lower")
    return ("higher" if is_max else "lower"), "evaluationAlgorithm.isMax"


def code_url(slug, direction):
    sort = "scoreDescending" if direction == "higher" else "scoreAscending"
    return f"{BASE}/competitions/{slug}/code?{urlencode({'sortBy': sort})}"


def notebook_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.netloc not in {"www.kaggle.com", "kaggle.com"}
            or not CODE_PATH.fullmatch(parsed.path)):
        raise ValueError("Expected a Kaggle notebook URL")
    return BASE + parsed.path


def list_request(competition_id, direction, page, page_size):
    return {
        "kernelFilterCriteria": {"search": "", "listRequest": {
            "competitionId": competition_id, "page": page, "pageSize": page_size,
            "sortBy": "SCORE_DESCENDING" if direction == "higher" else "SCORE_ASCENDING",
            "group": "EVERYONE", "modelIds": [], "modelInstanceIds": [],
            "excludeKernelIds": [], "tagIds": "", "excludeResultsFilesOutputs": False,
            "wantOutputFiles": False, "excludeNonAccessedDatasources": True}},
        "detailFilterCriteria": {"deletedAccessBehavior": "RETURN_NOTHING",
            "unauthorizedAccessBehavior": "RETURN_NOTHING", "excludeResultsFilesOutputs": False,
            "wantOutputFiles": False, "kernelIds": [], "outputFileTypes": [],
            "includeInvalidDataSources": False},
        "readMask": "pinnedKernels",
    }


class KagglePages:
    def __init__(self, slug, timeout):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.timeout = timeout
        self.headers = {"User-Agent": USER_AGENT, "Origin": BASE,
                        "Referer": f"{BASE}/competitions/{slug}/code"}

    def competition(self, slug):
        self.headers["Referer"] = f"{BASE}/competitions/{slug}/code"
        with self.opener.open(Request(f"{BASE}/competitions/{slug}/code", headers=self.headers),
                              timeout=self.timeout) as response:
            response.read()
        return self.query("competitions.CompetitionService/GetCompetition", {"competitionName": slug})

    def query(self, service, payload):
        headers = {**self.headers, "Content-Type": "application/json", "Accept": "application/json"}
        xsrf = next((c.value for c in self.cookies if c.name == "XSRF-TOKEN"), None)
        if xsrf:
            headers["X-XSRFToken"] = unquote(xsrf)
        request = Request(f"{BASE}/api/i/{service}", data=json.dumps(payload).encode(), headers=headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected website response from {service}")
        return data

    def notebooks(self, request):
        return self.query("kernels.KernelsService/ListKernels", request)

    def leaderboard(self, competition_id):
        return self.query("competitions.LeaderboardService/GetLeaderboard", {
            "competitionId": competition_id, "leaderboardMode": "LEADERBOARD_MODE_DEFAULT"})

    def notebook_view(self, url, version_id=0):
        url = notebook_url(url)
        self.headers["Referer"] = url
        # Direct notebook input needs the same XSRF bootstrap as competition input.
        if not any(c.name == "XSRF-TOKEN" for c in self.cookies):
            with self.opener.open(Request(url, headers={**self.headers, "Referer": url}),
                                  timeout=self.timeout) as response:
                response.read()
        author, slug = CODE_PATH.fullmatch(urlsplit(url).path).groups()
        return self.query("kernels.LegacyKernelsService/GetKernelViewModel", {
            "authorUserName": author, "kernelSlug": slug, "kernelVersionId": version_id})


def candidates(payload, slug, direction):
    if payload and not any(key in payload for key in ("kernels", "pinnedKernels", "totalCount")):
        raise ValueError("Code list response schema changed")
    normal, pinned = payload.get("kernels", []), payload.get("pinnedKernels", [])
    if not isinstance(normal, list) or not isinstance(pinned, list):
        raise ValueError("Expected Code list arrays")
    found = {}
    for item in normal + pinned:
        if not isinstance(item, dict):
            raise ValueError("Code list row schema changed")
        path = item.get("scriptUrl")
        match = CODE_PATH.fullmatch(path) if isinstance(path, str) else None
        if not match:
            continue
        value = item.get("bestPublicScore")
        score = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
        url = BASE + item["scriptUrl"]
        found[url] = {"title": item.get("title") or "/".join(match.groups()),
            "author": match.group(1), "code_url": url, "public_score": score,
            "score_field": "bestPublicScore" if score is not None else None,
            "evidence_url": code_url(slug, direction), "votes": item.get("totalVotes") or 0,
            "current_version_id": item.get("scriptVersionId"), "best_score_version_id": None}
    return list(found.values())


def competition_ids(view):
    sources = view.get("dataSources", [])
    if not isinstance(sources, list) or any(not isinstance(row, dict) for row in sources):
        raise ValueError("Notebook dataSources schema changed")
    return {row.get("sourceId") for row in sources
            if row.get("sourceType") in (None, "DATA_SOURCE_TYPE_COMPETITION")}


def same_display_score(score, formatted):
    try:
        shown = Decimal(str(formatted).replace(",", ""))
        return shown.is_finite() and Decimal(str(score)).quantize(Decimal(1).scaleb(shown.as_tuple().exponent)) == shown
    except InvalidOperation:
        return False


def resolve_version(client, item, competition_id, output, prefix, raw, current=None, current_snapshot=None):
    """Bind the displayed historical best score to a verified, pinned run URL."""
    item.update(current_version_number=None, best_score_version_id=None,
                best_score_version_number=None, best_score_code_url=None)
    evidence = []
    try:
        if current is None:
            current = client.notebook_view(item["code_url"])
        if current_snapshot is None:
            current_snapshot = save_json(output, f"{prefix}-current.json", {"fetched_at": now(), "response": current})
            raw.append(current_snapshot)
        name = current_snapshot["file"]
        evidence.append(name)
        if not isinstance(current, dict):
            raise ValueError("Notebook view schema changed")
        kernel = current.get("kernel") or {}
        run = current.get("kernelRun") or {}
        if not isinstance(kernel, dict) or not isinstance(run, dict):
            raise ValueError("Notebook kernel/run schema changed")
        path = kernel.get("url")
        if (not isinstance(path, str) or notebook_url(BASE + path) != item["code_url"]
                or competition_ids(current) != {competition_id}):
            raise ValueError("Notebook identity or competition is ambiguous/mismatched")
        if kernel.get("bestPublicScore") != item["public_score"]:
            raise ValueError("Notebook bestPublicScore changed or does not match the Code list")
        item["current_version_id"] = run.get("id")
        item["current_version_number"] = current.get("currentVersionNumber")
        best = current.get("bestSubmissionScore") or {}
        if not isinstance(best, dict):
            raise ValueError("Best Score mapping schema changed")
        formatted = best.get("scoreFormatted")
        if formatted is None or not same_display_score(item["public_score"], formatted):
            raise ValueError("Best Score mapping missing or inconsistent with the Public Score")
        link = best.get("kernelSessionUrl", "")
        if not isinstance(link, str):
            raise ValueError("Best Score link unavailable")
        link = BASE + link if link.startswith("/") else link
        if notebook_url(link) != item["code_url"]:
            raise ValueError("Best Score link points to another notebook")
        ids = parse_qs(urlsplit(link).query).get("scriptVersionId", [])
        if len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]*", ids[0]):
            raise ValueError("Best Score link has no unambiguous scriptVersionId")
        version_id, number = int(ids[0]), best.get("kernelVersionNumber")
        if type(number) is not int or number <= 0:
            raise ValueError("Best Score version number unavailable")
        pinned = client.notebook_view(item["code_url"], version_id)
        name = f"{prefix}-best.json"
        raw.append(save_json(output, name, {"fetched_at": now(), "response": pinned}))
        evidence.append(name)
        if not isinstance(pinned, dict):
            raise ValueError("Pinned notebook view schema changed")
        best_run, best_kernel = pinned.get("kernelRun") or {}, pinned.get("kernel") or {}
        if not isinstance(best_run, dict) or not isinstance(best_kernel, dict):
            raise ValueError("Pinned notebook kernel/run schema changed")
        if (best_kernel.get("id") != kernel.get("id") or not kernel.get("id")
                or best_run.get("kernelId") != kernel["id"]
                or best_run.get("id") != version_id or best_run.get("status") != "COMPLETE"
                or pinned.get("currentVersionNumber") != number
                or best_run.get("kernelVersionNumber") != number
                or competition_ids(pinned) != {competition_id}):
            raise ValueError("Pinned Best Score version could not be verified")
        item.update(best_score_version_id=version_id, best_score_version_number=number,
                    best_score_code_url=f"{item['code_url']}?scriptVersionId={version_id}")
        item['dependency_snapshot'] = {'state':'captured', 'completeness':'not_independently_verified',
            'sources':pinned['dataSources'], 'evidence_file':name,
            'evidence_sha256':raw[-1]['sha256']}
        item["version_resolution"] = {"status": "confirmed", "board": "public",
            "score_formatted": formatted, "source": "bestSubmissionScore",
            "score_match_basis": "display_rounding", "evidence_files": evidence}
    except (ValueError, OSError, URLError, HTTPError) as error:
        item["version_resolution"] = {"status": "unconfirmed", "reason": str(error),
                                      "evidence_files": evidence}
        raw.append(save_json(output, f"{prefix}-error.json", {"fetched_at": now(),
                            **item["version_resolution"]}))
    return item


def save_json(folder, name, value):
    content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    (folder / name).write_bytes(content)
    return {"file": name, "sha256": hashlib.sha256(content).hexdigest()}


def leaderboard_snapshot(payload, slug, retrieved_at):
    summary = {"board": "public", "retrieved_at": retrieved_at,
               "time_basis": "response_retrieval_time", "comparison_field": "displayScore",
               "url": f"{BASE}/competitions/{slug}/leaderboard?tab=public"}
    source = payload.get("publicLeaderboard")
    if not isinstance(source, list) or not source:
        return {**summary, "status": "unavailable", "reason": "No publicLeaderboard rows"}, []
    unique, unranked, invalid = {}, 0, 0
    for row in source:
        if not isinstance(row, dict):
            invalid += 1
            continue
        rank = row.get("rank")
        if type(rank) is not int or rank <= 0:
            unranked += 1
            continue
        try:
            score = Decimal(str(row["displayScore"]).replace(",", ""))
            if not score.is_finite() or not row.get("teamId"):
                raise ValueError("Invalid leaderboard row")
        except (KeyError, ValueError, InvalidOperation):
            invalid += 1
            continue
        unique[row["teamId"]] = {"rank": rank, "score": score}
    rows = list(unique.values())
    # A complete ranked table has each official position once, beginning at 1.
    complete = bool(rows) and not invalid and sorted(r["rank"] for r in rows) == list(range(1, len(rows) + 1))
    return {**summary, "status": "complete" if complete else "incomplete",
            "source_rows": len(source), "ranked_teams": len(rows),
            "excluded_unranked_rows": unranked, "invalid_ranked_rows": invalid}, rows


def score_position(value, direction, snapshot, rows):
    if snapshot["status"] != "complete":
        return None
    score = Decimal(str(value))
    better = sum(row["score"] > score if direction == "higher" else row["score"] < score for row in rows)
    equal = [row["rank"] for row in rows if row["score"] == score]
    first = better + 1
    return {"kind": "score_estimate", "as_of": snapshot["retrieved_at"],
            "total_ranked_teams": snapshot["ranked_teams"], "strictly_better_teams": better,
            "same_display_score_teams": len(equal), "score_rank_start": first,
            "existing_same_score_rank_range": {"first": min(equal), "last": max(equal)} if equal else None,
            "new_entry_rank_range": {"best": first, "worst": first + len(equal)},
            "comparison_basis": "displayScore", "leaderboard_url": snapshot["url"]}


def score_boundary(snapshot, rows, direction, competition_id, metric):
    """A current Public leader is a plausibility bound, not a score validator."""
    boundary = {"status": "unavailable", "board": "public", "competition_id": competition_id,
        "direction": direction, "as_of": snapshot["retrieved_at"],
        "leaderboard_url": snapshot.get("url"), "comparison_field": "displayScore",
        "epsilon": str(SCORE_EPSILON), "leaderboard_best": None,
        "metric_version_id": metric.get("latestCompetitionMetricVersionId"),
        "phase_alignment": "not_independently_verified"}
    if snapshot.get("board") != "public" or snapshot["status"] != "complete":
        return {**boundary, "reason": "Complete Public leaderboard unavailable"}
    leaders = [row for row in rows if row["rank"] == 1]
    if len(leaders) != 1:
        return {**boundary, "reason": "Public rank 1 is missing or ambiguous"}
    best = leaders[0]["score"]
    if best != (max if direction == "higher" else min)(row["score"] for row in rows):
        return {**boundary, "reason": "Rank 1 score is inconsistent with the metric direction"}
    return {**boundary, "status": "available", "leaderboard_best": str(best)}


def validate_score_bound(value, boundary):
    if boundary["status"] != "available":
        return {"status": "unchecked", "reason": boundary["reason"]}
    score, best = Decimal(str(value)), Decimal(boundary["leaderboard_best"])
    suspect = score > best + SCORE_EPSILON if boundary["direction"] == "higher" else score < best - SCORE_EPSILON
    return {"status": "suspect" if suspect else "within_bound",
            "reason": "better_than_current_public_leader" if suspect else "not_better_than_current_public_leader"}


def discover(client, slug, requested_direction, top, page_size, max_pages, output, selected_notebook=None):
    started = now()
    metadata = client.competition(slug)
    raw = [save_json(output, "competition.json", {"fetched_at": now(), "response": metadata})]
    competition_id = metadata.get("id")
    if type(competition_id) is not int or competition_id <= 0:
        raise ValueError("Kaggle did not return a competition ID")
    if metadata.get("competitionName", slug).casefold() != slug.casefold():
        raise ValueError("Competition response does not match the requested slug")
    direction, direction_source = direction_for(metadata, requested_direction)
    unique, seen_normal = {}, set()
    stop_reason, page = "page_limit", 0
    if selected_notebook:
        url, view, snapshot_file = selected_notebook
        raw.append(snapshot_file)
        kernel = view.get("kernel") or {}
        payload = {"kernels": [{**kernel, "scriptUrl": urlsplit(url).path,
                               "scriptVersionId": (view.get("kernelRun") or {}).get("id")}]}
        unique = {item["code_url"]: {**item, "evidence_url": url} for item in candidates(payload, slug, direction)}
        stop_reason = "notebook_input"
    for page in range(1, 1 if selected_notebook else max_pages + 1):
        request = list_request(competition_id, direction, page, page_size)
        payload = client.notebooks(request)
        raw.append(save_json(output, f"page-{page:03}.json",
                             {"fetched_at": now(), "request": request, "response": payload}))
        for candidate in candidates(payload, slug, direction):
            unique[candidate["code_url"]] = candidate
        normal = payload.get("kernels", [])
        page_urls = {item.get("scriptUrl") for item in normal if item.get("scriptUrl")}
        if page_urls and page_urls <= seen_normal:
            stop_reason = "repeated_page"
            break
        seen_normal.update(page_urls)
        if len(normal) < page_size:
            stop_reason = "end_of_results"
            break
    scored = [item for item in unique.values() if item["public_score"] is not None]
    scored.sort(key=lambda item: ((-item["public_score"] if direction == "higher" else item["public_score"]),
                                  -item["votes"], item["code_url"]))
    try:
        leaderboard_payload = client.leaderboard(competition_id)
        retrieved_at = now()
        raw.append(save_json(output, "leaderboard.json", {"fetched_at": retrieved_at, "response": leaderboard_payload}))
        snapshot, leaderboard_rows = leaderboard_snapshot(leaderboard_payload, slug, retrieved_at)
    except (ValueError, OSError, URLError, HTTPError) as error:
        snapshot, leaderboard_rows = {"board": "public", "status": "unavailable",
            "retrieved_at": now(), "error": str(error)}, []
        raw.append(save_json(output, "leaderboard-error.json", snapshot))
    metric = metadata.get("evaluationAlgorithm") or {}
    boundary = score_boundary(snapshot, leaderboard_rows, direction, competition_id, metric)
    eligible, anomalous = [], []
    for source_rank, item in enumerate(scored, 1):
        item["source_score_rank"] = source_rank
        item["score_validation"] = validate_score_bound(item["public_score"], boundary)
        if item["score_validation"]["status"] == "suspect":
            anomalous.append({**item, "discovery_rank": None, "leaderboard_position": None,
                "version_resolution": {"status": "not_performed", "reason": "score_anomaly_pending_verification"}})
        else:
            eligible.append(item)
    # Apply --top after quarantine so anomalous leading scores do not crowd out
    # plausible candidates on this or later scanned pages. Keep all anomalies.
    selected = eligible[:top]
    for index, item in enumerate(selected, 1):
        resolve_version(client, item, competition_id, output, f"notebook-{index:03}", raw,
                        selected_notebook[1] if selected_notebook else None,
                        selected_notebook[2] if selected_notebook else None)
    return {"schema_version": 1, "competition": slug, "competition_id": competition_id,
        "title": metadata.get("title"), "metric": metric.get("name"), "board": "public",
        "direction": direction, "direction_source": direction_source,
        "direction_label": "越高越好" if direction == "higher" else "越低越好",
        "started_at": started, "fetched_at": now(), "freshness": "live",
        "list_url": code_url(slug, direction), "code_review": "not_performed",
        "leaderboard": snapshot, "score_boundary": boundary,
        "coverage": {"pages_fetched": page, "page_size": page_size, "max_pages": max_pages,
            "scope": "single_notebook" if selected_notebook else "competition_code_list",
            "stop_reason": stop_reason, "unique_notebooks": len(unique),
            "scored_notebooks": len(scored), "unscored_notebooks": len(unique) - len(scored),
            "anomalous_scored_notebooks": len(anomalous), "eligible_scored_notebooks": len(eligible),
            "unchecked_scored_notebooks": sum(item["score_validation"]["status"] == "unchecked" for item in eligible)},
        "candidates": [{"discovery_rank": rank, **item,
            "leaderboard_position": score_position(item["public_score"], direction, snapshot, leaderboard_rows)
                if boundary["status"] == "available" else None}
            for rank, item in enumerate(selected, 1)],
        "anomalous_candidates": anomalous,
        "raw_files": raw}


def discover_notebook(client, url, requested_direction, output):
    url = notebook_url(url)
    current = client.notebook_view(url)
    snapshot_file = save_json(output, "notebook-input.json", {"fetched_at": now(), "response": current})
    # A notebook can attach multiple competitions; do not guess which board its
    # best score refers to when the page does not disambiguate them.
    slugs = {competition_slug(BASE + row["dataSourceUrl"])
             for row in current.get("renderableDataSources", [])
             if row.get("dataSourceUrl", "").startswith(("/competitions/", "/c/"))}
    if len(slugs) != 1:
        raise ValueError("Notebook must identify exactly one competition; otherwise query the competition explicitly")
    return discover(client, slugs.pop(), requested_direction, 1, 50, 1, output, (url, current, snapshot_file))


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("competition", help="Competition slug, competition/leaderboard URL, or a notebook URL")
    parser.add_argument("--direction", choices=["auto", "higher", "lower"], default="auto")
    parser.add_argument("--top", type=positive_int, default=10)
    parser.add_argument("--page-size", type=positive_int, default=50)
    parser.add_argument("--max-pages", type=positive_int, default=3)
    parser.add_argument("--timeout", type=positive_int, default=30)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        is_notebook = urlsplit(args.competition).path.startswith("/code/")
        slug = None if is_notebook else competition_slug(args.competition)
        output = args.output_dir.expanduser().resolve()
        if output.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("Keep task output outside the skill directory")
        if output.exists() and any(output.iterdir()):
            raise ValueError("Use a new or empty output directory to preserve previous evidence")
        output.mkdir(parents=True, exist_ok=True)
        client = KagglePages(slug or "", args.timeout)
        if is_notebook:
            result = discover_notebook(client, args.competition, args.direction, output)
        else:
            result = discover(client, slug, args.direction, args.top, args.page_size, args.max_pages, output)
        save_json(output, "result.json", result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, URLError, HTTPError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
