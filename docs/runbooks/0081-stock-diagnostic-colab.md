# Runbook 0081 - companyTradesAtStockExchange real-weight targeted diagnostic (Colab)

Copy-paste cells. **No Python file needs editing in Colab.** The only thing you
set by hand is `SOURCE_SHA` in CELL 3.

What this runs: 100 TRAIN `companyTradesAtStockExchange` rows, with exactly one
experimental feature on (`stock_listing_entity_prompt`). No TEST. No other
relation. No other Class-B feature. `stock_support_dominance` is deliberately
**off**, so the background is exactly SAFE_CORE — the baseline the promotion gate
is written against.

Expected wall clock: the authoritative TRAIN run averaged ~24 s/row, so budget
**40-60 minutes** on an A100 plus model download time.

Cell order is deliberate: config, dataset and model *metadata* checks first,
then the **real** `run_cover.py` pre-flight (CELL 9b), and only then weights.
Audit 0080's first real diagnostic attempt exposed the TRAIN-diagnostic
execution-gate problem only after the 28.7B weights had loaded. Audit 0081
inherits the fix: CELL 9b runs the runner's own preflight before any Stock
weights are downloaded.

Both defects found while running audit 0080 for real are guarded here from the
start: the TRAIN-diagnostic execution gate (CELL 9b, run before weights) and the
provenance field (CELL 12 asserts `manifest.git_revision`, never
`cover_kbc_version`).

Inference writes to local Colab SSD and is copied to Drive **after** the run:
streaming high-frequency telemetry straight to a mounted Drive is slow and can
truncate.

---

## CELL 1 - mount Drive

```python
from google.colab import drive
drive.mount('/content/drive')

import os, datetime
RUN_UTC = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
DRIVE_ROOT = '/content/drive/MyDrive/AKBC/Diagnostics'
os.makedirs(DRIVE_ROOT, exist_ok=True)
print('drive root :', DRIVE_ROOT)
print('run stamp  :', RUN_UTC)
```

---

## CELL 2 - clone

```python
%cd /content
!rm -rf FactElicit-AKBC
!git clone https://github.com/vquclinh/FactElicit-AKBC.git
%cd /content/FactElicit-AKBC
```

---

## CELL 3 - checkout the exact frozen SHA

**Set `SOURCE_SHA` to the pre-run commit reported by audit 0081.** The run is
invalid if it is made from any other tree.

```python
SOURCE_SHA = "PASTE_THE_PRE_RUN_COMMIT_SHA_HERE"   # <-- the only manual edit

!git checkout --detach {SOURCE_SHA}
!git rev-parse HEAD
```

---

## CELL 4 - verify the tree is clean and is that SHA

```python
import subprocess
head = subprocess.run(['git','rev-parse','HEAD'], capture_output=True, text=True).stdout.strip()
status = subprocess.run(['git','status','--porcelain'], capture_output=True, text=True).stdout.strip()
assert head == SOURCE_SHA, f'HEAD {head} != {SOURCE_SHA}'
assert status == '', f'working tree is dirty:\n{status}'
print('source SHA :', head)
print('tree       : clean')
```

---

## CELL 5 - dependencies

Installs what the run needs and nothing else. `transformers` is **not**
downgraded, and `flash-linear-attention` is **not** installed - the warning it
silences is cosmetic and the package has caused version conflicts before.

```python
!pip -q install -U "transformers>=4.44" accelerate bitsandbytes sentencepiece safetensors
!pip -q install mistral-common
!pip -q install pyyaml pandas
print('dependencies installed')
```

---

## CELL 6 - environment snapshot

```python
import torch, transformers, accelerate, json, platform, sys
try:
    import bitsandbytes; bnb = bitsandbytes.__version__
except Exception as exc:
    bnb = f'unavailable: {exc}'

env = {
    'python': sys.version.split()[0],
    'platform': platform.platform(),
    'torch': torch.__version__,
    'cuda_available': torch.cuda.is_available(),
    'cuda_device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    'cuda_capability': str(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,
    'vram_gb': round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1) if torch.cuda.is_available() else None,
    'transformers': transformers.__version__,
    'accelerate': accelerate.__version__,
    'bitsandbytes': bnb,
}
print(json.dumps(env, indent=2))
assert torch.cuda.is_available(), 'no GPU - stop here'
```

---

## CELL 7 - verify TRAIN identity

```python
import hashlib, json, collections
blob = open('benchmark/data/train.jsonl','rb').read()
digest = hashlib.sha256(blob).hexdigest()
rows = [json.loads(l) for l in blob.decode().splitlines() if l.strip()]
counts = collections.Counter(r['Relation'] for r in rows)

print('train sha256 :', digest)
print('train rows   :', len(rows))
print('companyTradesAtStockExchange  :', counts['companyTradesAtStockExchange'])
assert digest == 'ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e'
assert len(rows) == 477 and counts['companyTradesAtStockExchange'] == 100
print('TRAIN identity OK')
```

---

## CELL 8 - verify the model contract

```python
import yaml
cfg = yaml.safe_load(open('configs/experiments/v3_1_diag_stock.yaml'))
expected = {
    'enumerator': ('mistralai/Mistral-Small-3.2-24B-Instruct-2506',
                   '95a6d26c4bfb886c58daf9d3f7332c857cb27b43'),
    'verifier':   ('Qwen/Qwen3.5-4B',
                   '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'),
}
total = 0
for role, (model_id, revision) in expected.items():
    block = cfg['model_profile'][role]
    assert block['model_id'] == model_id, (role, block['model_id'])
    assert block['revision'] == revision, (role, block['revision'])
    total += block['published_total_parameters']
    print(f'{role:10s} {block["model_id"]} @ {block["revision"][:12]}  '
          f'{block["published_total_parameters"]:,}')
print('total parameters:', f'{total:,}', '<= 32,000,000,000 ->', total <= 32_000_000_000)
assert total <= 32_000_000_000
```

---

## CELL 9 - zero-model prechecks

Config isolation, prompt wiring and the audit-0078 orchestration fix, all before
any weights load.

```python
import sys; sys.path.insert(0, 'src')
from cover_kbc.v3_1.config import V31Config
from cover_kbc.controller_calibration.readiness import evaluate_test_readiness
from cover_kbc.pipeline import CoverPipeline, PipelineConfig, ExecutionMode
from cover_kbc.contracts.registry import CONTRACTS
from cover_kbc.elicitation.engine import ElicitationEngine, prompt_hash
from cover_kbc.elicitation.library import views_for
from cover_kbc.models.offline import ScriptedRuntime
from cover_kbc.v3_1.live_prompts import RelationInstructions, LIVE_PROMPT_VERSION

v31 = V31Config.from_mapping(cfg['pipeline']['selection']['v3_1'])
assert cfg['experiment']['split'] == 'train'
assert cfg['experiment']['relation_filter'] == ['companyTradesAtStockExchange']
assert cfg['experiment']['expected_rows'] == 100
assert 'test_dataset' not in cfg, 'this profile must not be able to read TEST'
assert v31.aggressive.enabled_features == ('stock_listing_entity_prompt',), \
    v31.aggressive.enabled_features
assert v31.calibration_status == 'CALIBRATION_REVIEW_REQUIRED'
assert evaluate_test_readiness(cfg, base_dir='configs/experiments',
                               split='test').state.value != 'FULL_TEST_READY'
print('config isolation      : OK  (one Class-B feature, TRAIN only, not TEST ready)')

# audit-0078 orchestration fix
p = CoverPipeline.__new__(CoverPipeline)
p.config = PipelineConfig(mode=ExecutionMode.INTERLEAVED)
assert {r.value for r in p._phase_b_roles()} == {'enumerator', 'verifier', 'none'}
p.config = PipelineConfig(mode=ExecutionMode.STAGED)
assert {r.value for r in p._phase_b_roles()} == {'verifier', 'none'}
print('audit-0078 fix        : OK  (interleaved Phase B admits the enumerator)')

# the stock listing-entity instruction actually reaches the rendered request
def render(block):
    rt = ScriptedRuntime(fallback=lambda r: 'UNKNOWN', model_id='offline',
                         family='m', role='enumerator')
    eng = ElicitationEngine(rt, relation_instructions=RelationInstructions.from_config(block))
    view = views_for('companyTradesAtStockExchange', CONTRACTS['companyTradesAtStockExchange'].mandatory_views)[0]
    return eng.system_prompt_for(view)

off, on = render(None), render(v31)
print('live prompt version   :', LIVE_PROMPT_VERSION)
print('system prompt OFF hash:', prompt_hash(off), f'({len(off)} chars)')
print('system prompt ON  hash:', prompt_hash(on),  f'({len(on)} chars)')
assert off != on, 'INVALID EXPERIMENT: the rendered prompt is identical OFF vs ON'
assert prompt_hash(off) == '2fb9188dbeda44f3', prompt_hash(off)
assert prompt_hash(on)  == '849a83f0c40440d1', prompt_hash(on)
print('prompt wiring         : OK  (hashes match the audit-0081 contract)')
```

---

## CELL 9b - REAL runner pre-flight, before any weights download

This is the Audit-0080 safeguard inherited by this Stock run. It calls
`run_cover.py`'s **own** gate resolution and readiness evaluation - not a copy
of the rules - so a config guard fails here in seconds instead of after weights
have downloaded.

```python
import importlib.util, sys
sys.path.insert(0, 'scripts')
spec = importlib.util.spec_from_file_location('run_cover_preflight', 'scripts/run_cover.py')
rc = importlib.util.module_from_spec(spec); sys.modules[spec.name] = rc
spec.loader.exec_module(rc)

from pathlib import Path
CONFIG = Path('configs/experiments/v3_1_diag_stock.yaml')

# 1. which readiness gate governs this run
gate, required = rc.resolve_production_gate(cfg, 'train', CONFIG)
print('gate     :', getattr(gate, '__name__', gate))
print('requires :', required.value)
assert gate is rc.TRAIN_DIAGNOSTIC_GATE[0], 'not routed to the TRAIN diagnostic gate'

# 2. does it pass, with no model loaded
readiness, required = rc.evaluate_production_readiness(cfg, 'train', CONFIG)
print('readiness:', readiness.state.value)
for blocker in readiness.blockers:
    print('  BLOCKER:', blocker)
assert readiness.state is required, 'STOP: fix the config before loading weights'

# 3. the relation filter the runner will actually apply
wanted = rc._resolve_relation_filter(None, cfg['experiment'], 'train')
from cover_kbc.data.loader import load_dataset
rows = [q for q in load_dataset('train').queries() if q.relation in wanted]
print('filter   :', sorted(wanted), '->', len(rows), 'rows')
assert sorted(wanted) == ['companyTradesAtStockExchange'] and len(rows) == 100

print('\nPRE-FLIGHT PASSED - safe to load weights')
```

If this cell fails, **stop and fix the config**. Nothing below it is worth the
download time.

---

## CELL 10 - launch the stock-only run

```python
import os, datetime, re, subprocess, sys
OUT = f'/content/stock_diag_{SOURCE_SHA[:12]}_{RUN_UTC}'   # local SSD, not Drive
LOG = f'{DRIVE_ROOT}/stock_{SOURCE_SHA[:12]}_{RUN_UTC}.log'
os.makedirs(os.path.dirname(LOG), exist_ok=True)
print('output dir :', OUT)
print('log        :', LOG)

cmd = [
    sys.executable, '-u', 'scripts/run_cover.py',
    '--config', 'configs/experiments/v3_1_diag_stock.yaml',
    '--output-dir', OUT,
    '--no-eval',
]
print('command    :', ' '.join(cmd))

relation_filter_rows = None
pattern = re.compile(
    r"relation filter \['companyTradesAtStockExchange'\]: (\d+) rows")

with open(LOG, 'w', encoding='utf-8') as log:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end='')
        log.write(line)
        log.flush()
        match = pattern.search(line)
        if match:
            relation_filter_rows = int(match.group(1))
    return_code = proc.wait()

print('run_cover return code:', return_code)
assert return_code == 0, f'run_cover.py failed with code {return_code}; see {LOG}'
assert relation_filter_rows is not None, (
    'expected stock relation-filter line was not observed')
assert relation_filter_rows == 100, (
    f'stock relation filter selected {relation_filter_rows} rows, expected 100')
print('PROCESS-LEVEL GATE: PASS')
```

The process-level gate above must print
`relation filter ['companyTradesAtStockExchange']: 100 rows` and
`PROCESS-LEVEL GATE: PASS`. If it prints any other row count, **stop** - do not
analyse it.

---

## CELL 11 - copy artifacts to Drive

```python
import shutil, os
DEST = f'{DRIVE_ROOT}/stock_{SOURCE_SHA[:12]}_{RUN_UTC}'
shutil.copytree(OUT, DEST, dirs_exist_ok=True)
print('copied to :', DEST)
for name in sorted(os.listdir(DEST)):
    size = os.path.getsize(os.path.join(DEST, name))
    print(f'  {name:42s} {size:>12,} bytes')
```

---

## CELL 12 - validate exactly 100 companyTradesAtStockExchange rows

```python
import json, collections, os
preds = [json.loads(l) for l in open(f'{OUT}/predictions.jsonl') if l.strip()]
rels = collections.Counter(p['Relation'] for p in preds)
print('prediction rows :', len(preds))
print('relations       :', dict(rels))
assert len(preds) == 100, f'{len(preds)} rows, expected 100'
assert set(rels) == {'companyTradesAtStockExchange'}, rels

errors_path = f'{OUT}/errors.json'
errors = json.load(open(errors_path)) if os.path.exists(errors_path) else []
print('query errors    :', len(errors))
for e in errors[:5]:
    print('   ', e['SubjectEntity'], '|', e['error'][:110])

manifest = json.load(open(f'{OUT}/manifest.json'))
# Source provenance is `git_revision`. `cover_kbc_version` is the package
# version string ('0.1.0') and is the same on every commit - comparing it to a
# SHA is a check that can never pass. Audit 0080 found this during analysis of
# a valid diagnostic run.
print('git_revision     :', manifest.get('git_revision'))
print('cover_kbc_version:', manifest.get('cover_kbc_version'), '(package version, NOT provenance)')
assert manifest.get('git_revision') == SOURCE_SHA, (
    f"manifest git_revision {manifest.get('git_revision')} != {SOURCE_SHA}")

acct = json.load(open(f'{OUT}/run_accounting.json'))
print('accounting      :', json.dumps({k: acct[k] for k in (
    'total_queries','prediction_rows','successful_queries','failed_queries',
    'unresolved_invariant_errors','pipeline_error_rows')}, indent=2))

assert acct['total_queries'] == 100
assert acct['prediction_rows'] == 100
assert acct['failed_queries'] == 0, 'STOP: contaminated run, do not analyse'
assert acct['unresolved_invariant_errors'] == 0, 'STOP: orchestration invariant fired'
print('RUN ACCOUNTING GATE: PASS')
```

---

## CELL 12b - locate the authoritative baseline

The baseline is a gitignored artifact, so a fresh clone does not have it. This
cell first checks the exact authoritative Drive location used successfully in
Audit 0080, then checks only a small set of expected backup locations. It
requires exactly one copy whose `predictions.jsonl` has the pinned SHA256. It
does not rerun the baseline.

Authoritative Drive source:
`/content/drive/MyDrive/AKBC/Submissions-AKBC/v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z/collection/cover_kbc_v3_train_collection_train-collect_20260809T232616Z`

```python
import hashlib, shutil
from pathlib import Path

BASELINE_SHA256 = '36d2b079e0a732fcce7e1f2655f96dc3d7606382d54825918dbedc8098472816'
BASELINE_RUN_NAME = 'cover_kbc_v3_train_collection_train-collect_20260809T232616Z'
BASELINE = ('outputs/v3_train_collect_v2_coverage/collection/'
            'cover_kbc_v3_train_collection_train-collect_20260809T232616Z')
LOCAL_BASELINE = Path(BASELINE)
AUTHORITATIVE_DRIVE_BASELINE = Path(
    '/content/drive/MyDrive/AKBC/Submissions-AKBC/'
    'v3_train_collect_v2_coverage_a11d75d2_20260809T232041Z/'
    'collection') / BASELINE_RUN_NAME
EXPECTED_BASELINE_CANDIDATES = [
    AUTHORITATIVE_DRIVE_BASELINE,                 # checked first
    Path(DRIVE_ROOT) / 'baseline' / BASELINE_RUN_NAME,
    Path('/content/drive/MyDrive/AKBC/Diagnostics/baseline') / BASELINE_RUN_NAME,
]

def baseline_predictions_sha(run_dir):
    path = Path(run_dir) / 'predictions.jsonl'
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

observed = []
seen = set()
print('checking authoritative Drive baseline first:', AUTHORITATIVE_DRIVE_BASELINE)
for candidate in EXPECTED_BASELINE_CANDIDATES:
    candidate = Path(candidate)
    key = str(candidate)
    if key in seen:
        continue
    seen.add(key)
    digest = baseline_predictions_sha(candidate)
    if digest is None:
        print('baseline candidate missing :', candidate)
        continue
    status = 'MATCH' if digest == BASELINE_SHA256 else 'MISMATCH'
    print(f'baseline candidate {status}: {candidate}')
    print(f'  predictions sha256: {digest}')
    observed.append((candidate, digest))

matches = [path for path, digest in observed if digest == BASELINE_SHA256]
mismatches = [(path, digest) for path, digest in observed
              if digest != BASELINE_SHA256]
if mismatches:
    print('ignored mismatching baseline candidate(s):')
    for path, digest in mismatches:
        print(f'  {path}  {digest}')

assert len(matches) == 1, (
    'expected exactly one authoritative baseline with pinned predictions SHA; '
    f'found {len(matches)} match(es). Do not rerun the baseline. '
    f'Observed candidates: {[(str(path), digest) for path, digest in observed]}')

source = matches[0]
LOCAL_BASELINE.parent.mkdir(parents=True, exist_ok=True)
if str(source) != str(LOCAL_BASELINE):
    shutil.copytree(source, LOCAL_BASELINE, dirs_exist_ok=True)
    print('baseline restored from:', source)
else:
    print('baseline already at analyzer path')

h = baseline_predictions_sha(LOCAL_BASELINE)
print('baseline predictions sha256:', h)
assert h == BASELINE_SHA256
print('baseline identity OK')
```

---

## CELL 13 - run the analysis

```python
ANALYSIS = f'{OUT}_analysis'

!python scripts/analyze_stock_diagnostic.py \
  --baseline-run {BASELINE} \
  --diagnostic-run {OUT} \
  --gold benchmark/data/train.jsonl \
  --expected-source-sha {SOURCE_SHA} \
  --output-dir {ANALYSIS}
```

If the baseline directory is absent in a fresh clone (it is a gitignored
artifact), upload it from Drive first, or run this cell locally instead - the
analysis is CPU-only and needs no GPU.

---

## CELL 14 - promotion summary

```python
import json
sm = json.load(open(f'{ANALYSIS}/summary.json'))
b, d, delta = sm['baseline_final'], sm['diagnostic_final'], sm['metric_delta']

print('=== FINAL OUTPUT ===')
print(f" macro-F1 : {b['macro_f1']:.5f} -> {d['macro_f1']:.5f} ({delta['macro_f1']:+.5f})")
print(f" macro-P  : {b['macro_p']:.5f} -> {d['macro_p']:.5f}")
print(f" macro-R  : {b['macro_r']:.5f} -> {d['macro_r']:.5f}")
print(f" TP/FP/FN : {b['tp']}/{b['fp']}/{b['fn']} -> {d['tp']}/{d['fp']}/{d['fn']}")
print(f" exact    : {b['exact_rows']} -> {d['exact_rows']}")
print(f" empty    : {b['empty_predictions']} -> {d['empty_predictions']}")
print(f" mean card: {b['mean_prediction_cardinality']} -> {d['mean_prediction_cardinality']}")

print('\n=== GATE CONDITION A: FP down more than FN up ===')
se = sm['suppression_efficiency']
print(f" FP reduction {se['fp_reduction']}  vs  FN increase {se['fn_increase']}")
print(f" ratio {se['fp_reduction_per_fn_increase']}")

print('\n=== GATE CONDITION B: macro-F1 >= baseline ===')
print(f" {d['macro_f1']:.5f} >= {b['macro_f1']:.5f} -> {d['macro_f1'] >= b['macro_f1']}")

print('\n=== SPURIOUS-PASS CHECKS ===')
for name in ('empty_gold', 'single_gold', 'multi_gold', 'non_empty_gold'):
    p = sm['populations'][name]
    print(f" {name:16s} rows={p['rows']:3d} empty {p['baseline_empty']}->{p['diagnostic_empty']}"
          f"  TP {p['baseline_tp']}->{p['diagnostic_tp']}"
          f"  FP {p['baseline_fp']}->{p['diagnostic_fp']}"
          f"  card {p['baseline_mean_cardinality']}->{p['diagnostic_mean_cardinality']}")
print(' multi-gold :', sm['multi_gold_shape'])
print(' single-gold:', sm['single_gold_shape'])
print(' baseline-correct regressions:', sm['baseline_correct_regressions'])

print('\n=== CANDIDATE SUPPRESSION ===')
bc, dc = sm['baseline_candidates'], sm['diagnostic_candidates']
print(f" total candidates : {bc['total_candidates']} -> {dc['total_candidates']}")
print(f" mean / row       : {bc['mean_candidates_per_row']} -> {dc['mean_candidates_per_row']}")
print(f" gold-like rows   : {bc['gold_like_candidate_rows']} -> {dc['gold_like_candidate_rows']}")
print(f" false removed / gold-like removed : {se['false_per_gold_removed']}"
      f"  ({se['false_candidates_removed']} vs {se['gold_like_candidates_removed']})")

print('\n=== VERIFICATION ===')
print(' baseline :', sm['baseline_calls'])
print(' diagnostic:', sm['diagnostic_calls'])

print('\n=== TRANSITIONS ===')
for k, v in sorted(sm['transitions'].items(), key=lambda kv: -kv[1]):
    print(f'  {k:42s} {v}')

print('\nPROMOTE requires BOTH gate conditions AND no spurious-pass signature.')
print('A precision rise driven by more empty rows is the audit-0080 failure mode,')
print('not a win. The decision is a judgement over these tables, not one number.')
```

---

## CELL 15 - provenance to Drive

```python
import hashlib, json, os, subprocess

def sha256(path):
    h = hashlib.sha256()
    with open(path,'rb') as fh:
        for chunk in iter(lambda: fh.read(1<<16), b''):
            h.update(chunk)
    return h.hexdigest()

prov = {
    'audit': '0081',
    'relation': 'companyTradesAtStockExchange',
    'source_sha': SOURCE_SHA,
    'manifest_git_revision': json.load(open(f'{OUT}/manifest.json')).get('git_revision'),
    'manifest_cover_kbc_version': json.load(open(f'{OUT}/manifest.json')).get('cover_kbc_version'),
    'run_utc': RUN_UTC,
    'config': 'configs/experiments/v3_1_diag_stock.yaml',
    'config_sha256': sha256('configs/experiments/v3_1_diag_stock.yaml'),
    'train_sha256': sha256('benchmark/data/train.jsonl'),
    'class_b_feature': 'stock_listing_entity_prompt',
    'system_prompt_off_hash': '2fb9188dbeda44f3',
    'system_prompt_on_hash': '849a83f0c40440d1',
    'stock_instruction_sha256':
        '39af1c5be480c85350535f75ddd2466b497d1b124bb4e3b97c63b21bda1c39f8',
    'environment': env,
    'artifacts': {n: sha256(os.path.join(OUT, n))
                  for n in sorted(os.listdir(OUT))
                  if os.path.isfile(os.path.join(OUT, n))},
    'calibration_status': 'CALIBRATION_REVIEW_REQUIRED',
    'valid_for': ['promotion decision for stock_listing_entity_prompt'],
    'invalid_for': ['TEST submission', 'calibration derivation', 'production readiness'],
}
dest = f'{DEST}/provenance.json'
open(dest,'w').write(json.dumps(prov, indent=2, sort_keys=True) + '\n')

sums = f'{DEST}/SHA256SUMS.txt'
with open(sums,'w') as fh:
    for name in sorted(os.listdir(DEST)):
        p = os.path.join(DEST, name)
        if os.path.isfile(p) and name != 'SHA256SUMS.txt':
            fh.write(f'{sha256(p)}  {name}\n')

print('provenance :', dest)
print('checksums  :', sums)
print('\nBring the whole folder back locally, then re-run CELL 13 offline to')
print('reproduce the analysis under a clean tree.')
```

---

## Stop conditions

Abort and report rather than continuing if any of these occur:

| Condition | Where |
|---|---|
| tree dirty or HEAD != `SOURCE_SHA` | CELL 4 |
| TRAIN sha256 or row counts differ | CELL 7 |
| model id or revision differs | CELL 8 |
| more than one Class-B feature enabled | CELL 9 |
| rendered prompt identical OFF vs ON | CELL 9 |
| readiness gate refuses, or filter != 100 rows | **CELL 9b** |
| prompt hashes differ from the contract | CELL 9 |
| row count != 100, or any non-stock relation | CELL 12 |
| `failed_queries` or `unresolved_invariant_errors` > 0 | CELL 12 |
| `manifest.git_revision` != `SOURCE_SHA` | CELL 12 |

A run that trips a stop condition is not a negative result about the prompt - it
is an invalid experiment, and analysing it would attribute an orchestration or
provenance problem to the intervention.
