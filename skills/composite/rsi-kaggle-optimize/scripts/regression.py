#!/usr/bin/env python3
"""Offline preflight only; never uploads, submits, scores or verifies remote privacy."""
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
import sys


def reproduction_level(report):
    """Describe declarations without turning model-written flags into facts."""
    if report.get('execution_scope') == 'smoke' or report.get('full_hidden_runtime_verified') is False:
        level = 'smoke_only'
    elif report.get('mode') == 'public_output_reuse':
        level = 'public_output_reuse'
    elif report.get('mode') == 'source_execution' and report.get('execution_scope') == 'full':
        level = 'full_execution_claimed'
    else:
        level = 'source_only'
    return {'declared_level':level,'full_execution_verified':False,'official_score_match_verified':False}


def compare_public(plan):
    """Final reference only. The System must authenticate supplied score evidence."""
    result = {'status':'unverified','current_open_sota_beaten':False}
    try:
        current, submitted = plan['final_reference'], plan['official_submission']
        stamp = lambda value: datetime.fromisoformat(value.replace('Z','+00:00'))
        fetched, started = stamp(current['fetched_at']), stamp(plan['comparison_started_at'])
        if (not fetched.tzinfo or not started.tzinfo or fetched < started
                or current['freshness'] != 'live' or not submitted.get('submission_id')):
            raise ValueError('Fresh final reference and official submission identity are required')
        for key in ('competition','metric','direction'):
            if not plan.get(key) or current.get(key) != plan[key] or submitted.get(key) != plan[key]:
                raise ValueError('Competition, metric and direction must match')
        if current.get('board') != 'public' or submitted.get('board') != 'public':
            raise ValueError('Scores must belong to the same Public board')
        score, reference = submitted['score'], current['candidates'][0]['public_score']
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (score,reference)):
            raise ValueError('Scores must be finite numbers; missing scores are not zero')
        if plan['direction'] not in ('higher','lower'):
            raise ValueError('Unknown direction')
        ahead = score > reference if plan['direction']=='higher' else score < reference
        result.update(status='numeric_comparison',numeric_relation='matched' if score==reference else 'ahead' if ahead else 'behind',
                      reference_score=reference,submission_score=score,reference_fetched_at=current['fetched_at'],
                      reason='Numeric comparison of supplied records only; System must verify phase, score provenance and coverage before a current SOTA claim')
    except (KeyError,TypeError,ValueError,IndexError,AttributeError) as exc:
        result['reason'] = str(exc)
    return result


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check(plan, root):
    errors, rejected, valid = [], {}, []
    direction = plan.get('direction')
    if direction not in ('higher', 'lower'):
        errors.append('direction must be higher or lower')
    for key in ('competition', 'metric', 'protocol'):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            errors.append(f'{key} is required')
    uploads = plan.get('uploads')
    if not isinstance(uploads, list):
        errors.append('uploads must list every planned new/updated remote resource (or [])')
    else:
        for i, item in enumerate(uploads):
            if not isinstance(item, dict) or not item.get('resource') or item.get('visibility') != 'private':
                errors.append(f'uploads[{i}] must explicitly be private')
                continue
            if item.get('kind') == 'kaggle-notebook':
                try:
                    metadata = json.loads((root / item['metadata']).read_text())
                    if metadata.get('is_private') is not True:
                        raise ValueError('is_private must be boolean true')
                    if metadata.get('is_public') not in (None, False) or metadata.get('visibility', 'private') != 'private':
                        raise ValueError('conflicting public visibility')
                except (KeyError, OSError, ValueError, TypeError) as e:
                    errors.append(f'uploads[{i}] invalid metadata: {e}')
            elif item.get('kind') != 'other-private-resource' or not item.get('config_evidence'):
                errors.append(f'uploads[{i}] needs a supported kind and private configuration evidence')
            else:
                if not (root / item['config_evidence']).is_file():
                    errors.append(f'uploads[{i}] configuration evidence missing')
    candidates = plan.get('candidates')
    if not isinstance(candidates, list) or not candidates:
        errors.append('nonempty candidates required')
        candidates = []
    ids = set()
    for c in candidates:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id']:
            errors.append('every candidate needs an id')
            continue
        cid = c['id']
        if cid in ids:
            errors.append(f'duplicate candidate id: {cid}')
        ids.add(cid)
        try:
            code_hash = digest(root / c['code'])
            artifact_hash = digest(root / c['artifact'])
            report = json.loads((root / c['evaluation']).read_text())
            expected = dict(candidate_id=cid, code_sha256=code_hash, artifact_sha256=artifact_hash,
                            competition=plan['competition'], metric=plan['metric'], protocol=plan['protocol'],
                            direction=direction, kind='validation', status='complete')
            if any(report.get(k) != v for k, v in expected.items()):
                raise ValueError('evaluation identity/protocol/status mismatch')
            if report.get('artifact_valid') is not True:
                raise ValueError('artifact check did not pass')
            score = report.get('score')
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError('score must be a finite number; zero is valid')
            valid.append((cid, score))
        except (OSError, KeyError, ValueError, TypeError) as e:
            rejected[cid] = str(e)
    baseline = next((v for v in valid if v[0] == plan.get('baseline_id')), None)
    selected = None
    if baseline is None:
        errors.append('baseline has no valid bound evaluation; repair baseline first')
    elif not errors:
        selected = baseline
        for candidate in valid:
            if (candidate[1] > selected[1] if direction == 'higher' else candidate[1] < selected[1]):
                selected = candidate
    return {'passed': not errors, 'errors': errors, 'rejected_candidates': rejected,
            'public_comparison':compare_public(plan),
            'reproduction':reproduction_level(plan.get('reference_report',{})),
            'selected_id': selected[0] if selected else None,
            'decision': ('baseline_fallback' if selected == baseline else 'validation_improvement') if selected else 'blocked',
            'public_improvement_proven': False, 'remote_privacy_verified': False,
            'scope': 'offline preflight; System must verify actual requests, remote visibility and submission scores'}


def main():
    try:
        path = Path(sys.argv[1]).resolve()
        plan = json.loads(path.read_text())
        result = check(plan, path.parent)
    except (IndexError, OSError, ValueError, TypeError, AttributeError) as e:
        result = {'passed': False, 'errors': [str(e)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
