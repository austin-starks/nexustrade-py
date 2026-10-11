"""Replay retained construction/review bytes without service calls or financial edits.

The workspace must include the actual execution receipt and its frozen inputs.
Transcript files must be hash-verified by an index captured with the session.
Optional explicit scope projection exercises the new native component binding;
it is labelled as replay metadata, never presented as the original submission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

from nexustrade import report_analysis as analysis


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(workspace: Path, transcripts: Path, audit: Path) -> dict:
    captured = json.loads(audit.read_text())['construction']
    receipt = json.loads((workspace / analysis.EXECUTION_PATH).read_text())
    for path, item in captured['files'].items():
        if sha(workspace / Path(path).relative_to('/work')) != item['sha256']:
            raise ValueError(f'Retained artifact changed: {path}')
    for path, expected in receipt['inputSha256'].items():
        if sha(workspace / Path(path).relative_to('/work')) != expected:
            raise ValueError(f'Frozen input changed: {path}')
    index = json.loads(transcripts.read_text())
    transcript_rows = []
    for item in index:
        local = Path(item['local'])
        if sha(local) != item['sha256'] or local.stat().st_size != item['bytes']:
            raise ValueError(f'Transcript changed: {local.name}')
        for line in local.read_text().splitlines():
            value = json.loads(line)
            if value.get('type') == 'message':
                transcript_rows.append(value)
    # Report actual calls, rather than inferring semantic behavior from prose.
    calls = []
    completion_count = 0
    for row in sorted(transcript_rows, key=lambda row: row.get('timestamp', '')):
        message = row.get('message', {})
        for part in message.get('content', []):
            if not isinstance(part, dict) or part.get('type') != 'toolCall':
                continue
            args = part.get('arguments', {})
            if part.get('name') == 'complete_artifact_review':
                completion_count += 1
            if part.get('name') in {'task', 'complete_artifact_review'}:
                calls.append({'at': row.get('timestamp'), 'tool': part['name'],
                              'role': args.get('subagent_type'), 'description': args.get('description')})
    if completion_count == 0:
        raise ValueError('No actual native reviewer completion found in retained transcripts')
    with tempfile.TemporaryDirectory(prefix='retained-analysis-review-') as temporary:
        root = Path(temporary)
        wanted = {*captured['files'], *receipt['inputSha256']}
        for path in wanted:
            relative = Path(path).relative_to('/work')
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(workspace / relative, target)
        with patch.dict(os.environ, {'NEXUSTRADE_WORK_DIR':str(root)}):
            model = json.loads((root / 'out/calculation_model.json').read_text())
            before = captured['analysisStatus']['True']
            legacy = analysis.analysis_status()
            if not legacy['executionCurrent']:
                raise ValueError('Real retained inputs do not reproduce current execution')
            checkpoint = analysis.construction_checkpoint(analysis.attach_analysis_work({'calculationModel':model}))
            if checkpoint['nextTasks'][0]['operation'] != 'repair_review_findings':
                raise AssertionError('Actual incomplete checkpoint still loses/prioritizes extension over the native rejection')
            # New protocol scope is selected from the exact declared component,
            # not derived from review prose or a company-specific field name.
            plan = analysis._plan()
            queue_path = root / '.nexustrade/analysis/review-findings.json'
            queue = json.loads(queue_path.read_text())
            original_findings = [{key:row[key] for key in ('findingId','path','issue','evidence')} for row in queue['findings']]
            for finding in queue['findings']:
                finding['componentIds'] = [row['id'] for row in plan['components']]
                finding['analysisTargets'] = plan['components']
            queue_path.write_text(json.dumps(queue))
            scoped = analysis.analysis_status()
            if scoped['components'][0]['status'] != 'review_rejected':
                raise AssertionError('Captured scoped review still labels rejected input computed')
            repeated = analysis.construction_checkpoint(analysis.attach_analysis_work({'calculationModel':model}))
            if repeated != analysis.construction_checkpoint(analysis.attach_analysis_work({'calculationModel':model})):
                raise AssertionError('Repeated checkpoint is not stable')
            if [{key:row[key] for key in ('findingId','path','issue','evidence')} for row in queue['findings']] != original_findings:
                raise AssertionError('Replay altered reviewer judgment')
            if sha(root / 'out/calculation_model.json') != receipt['modelSha256']:
                raise AssertionError('Replay altered economic values')
            return {'status':'PASS_RETAINED_REPAIR_ROUTING', 'modelSha256':receipt['modelSha256'],
                    'producerSha256':receipt['inputSha256'][plan['producerPath']],
                    'transcriptFilesVerified':len(index),'nativeCompletions':completion_count,
                    'actualCalls':calls,
                    'before':{'componentStatus':before['components'][0]['status'],
                              'nextOperations':[row['operation'] for row in before['nextTasks']]},
                    'afterLegacy':{'nextOperations':[row['operation'] for row in checkpoint['nextTasks']]},
                    'afterExplicitScope':{'componentStatus':scoped['components'][0]['status'],
                                          'nextOperations':[row['operation'] for row in repeated['nextTasks']]},
                    'scopeProvenance':'Explicit replay projection from the retained plan; not a field present in the original reviewer submission',
                    'financialModelUnchanged':True, 'paidModelCalls':0,
                    'autonomousCompletedFinancialAnalysisProven':False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--transcript-index',type=Path,required=True)
    parser.add_argument('--artifact-audit',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    result = replay(args.workspace,args.transcript_index,args.artifact_audit)
    with args.out.open('x') as output:
        output.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key != 'actualCalls'}))


if __name__ == '__main__':
    main()
