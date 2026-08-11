# Runbook 0080 - hasCapacity real-weight targeted diagnostic (Colab)

Copy-paste cells. **No Python file needs editing in Colab.** The only thing you
set by hand is `SOURCE_SHA` in CELL 3.

What this runs: 100 TRAIN `hasCapacity` rows, with exactly one experimental
feature on (`capacity_definition_prompt`). No TEST. No other relation. No other
Class-B feature.

Expected wall clock: the authoritative TRAIN run averaged ~24 s/row, so budget
**40-60 minutes** on an A100 plus model download time.

Cell order is deliberate: config, dataset and model *metadata* checks first,
then the **real** `run_cover.py` pre-flight (CELL 9b), and only then weights.
The first attempt at this experiment loaded 28.7B parameters and then aborted on
a config guard; CELL 9b is that guard, run for free.

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

**Set `SOURCE_SHA` to the pre-run commit reported by audit 0080.** The run is
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
print('hasCapacity  :', counts['hasCapacity'])
assert digest == 'ad37cd30d1ff4b9f1ef2579b25e64093b202c40da11e8c412e13386f1e5d332e'
assert len(rows) == 477 and counts['hasCapacity'] == 100
print('TRAIN identity OK')
```

---

## CELL 8 - verify the model contract

```python
import yaml
cfg = yaml.safe_load(open('configs/experiments/v3_1_diag_capacity.yaml'))
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
assert cfg['experiment']['relation_filter'] == ['hasCapacity']
assert cfg['experiment']['expected_rows'] == 100
assert 'test_dataset' not in cfg, 'this profile must not be able to read TEST'
assert v31.aggressive.enabled_features == ('capacity_definition_prompt',), \
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

# the capacity instruction actually reaches the rendered request
def render(block):
    rt = ScriptedRuntime(fallback=lambda r: 'UNKNOWN', model_id='offline',
                         family='m', role='enumerator')
    eng = ElicitationEngine(rt, relation_instructions=RelationInstructions.from_config(block))
    view = views_for('hasCapacity', CONTRACTS['hasCapacity'].mandatory_views)[0]
    return eng.system_prompt_for(view)

off, on = render(None), render(v31)
print('live prompt version   :', LIVE_PROMPT_VERSION)
print('system prompt OFF hash:', prompt_hash(off), f'({len(off)} chars)')
print('system prompt ON  hash:', prompt_hash(on),  f'({len(on)} chars)')
assert off != on, 'INVALID EXPERIMENT: the rendered prompt is identical OFF vs ON'
assert prompt_hash(off) == '2fb9188dbeda44f3', prompt_hash(off)
assert prompt_hash(on)  == 'bcffa96f37770392', prompt_hash(on)
print('prompt wiring         : OK  (hashes match the audit-0080 contract)')
```

---

## CELL 9b - REAL runner pre-flight, before any weights download

This is the cell the first attempt did not have. It calls
`run_cover.py`'s **own** gate resolution and readiness evaluation - not a copy
of the rules - so a config guard fails here in seconds instead of after 28.7B
parameters have downloaded.

```python
import importlib.util, sys
sys.path.insert(0, 'scripts')
spec = importlib.util.spec_from_file_location('run_cover_preflight', 'scripts/run_cover.py')
rc = importlib.util.module_from_spec(spec); sys.modules[spec.name] = rc
spec.loader.exec_module(rc)

from pathlib import Path
CONFIG = Path('configs/experiments/v3_1_diag_capacity.yaml')

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
assert sorted(wanted) == ['hasCapacity'] and len(rows) == 100

print('\nPRE-FLIGHT PASSED - safe to load weights')
```

If this cell fails, **stop and fix the config**. Nothing below it is worth the
download time.

---

## CELL 10 - launch the capacity-only run

```python
import os, datetime
OUT = f'/content/FactElicit-AKBC/outputs/v3_2_capacity_diag_{SOURCE_SHA[:12]}_{RUN_UTC}'
LOG = f'{DRIVE_ROOT}/capacity_{SOURCE_SHA[:12]}_{RUN_UTC}.log'
os.makedirs(os.path.dirname(LOG), exist_ok=True)
print('output dir :', OUT)
print('log        :', LOG)

!python scripts/run_cover.py \
  --config configs/experiments/v3_1_diag_capacity.yaml \
  --output-dir {OUT} \
  --no-eval 2>&1 | tee {LOG}
```

The run prints `relation filter ['hasCapacity']: 100 rows` near the start. If it
prints any other number, **stop** - do not analyse it.

---

## CELL 11 - copy artifacts to Drive

```python
import shutil, os
DEST = f'{DRIVE_ROOT}/capacity_{SOURCE_SHA[:12]}_{RUN_UTC}'
shutil.copytree(OUT, DEST, dirs_exist_ok=True)
print('copied to :', DEST)
for name in sorted(os.listdir(DEST)):
    size = os.path.getsize(os.path.join(DEST, name))
    print(f'  {name:42s} {size:>12,} bytes')
```

---

## CELL 12 - validate exactly 100 hasCapacity rows

```python
import json, collections, os
preds = [json.loads(l) for l in open(f'{OUT}/predictions.jsonl') if l.strip()]
rels = collections.Counter(p['Relation'] for p in preds)
print('prediction rows :', len(preds))
print('relations       :', dict(rels))
assert len(preds) == 100, f'{len(preds)} rows, expected 100'
assert set(rels) == {'hasCapacity'}, rels

errors_path = f'{OUT}/errors.json'
errors = json.load(open(errors_path)) if os.path.exists(errors_path) else []
print('query errors    :', len(errors))
for e in errors[:5]:
    print('   ', e['SubjectEntity'], '|', e['error'][:110])

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

## CELL 13 - run the analysis

```python
BASELINE = ('outputs/v3_train_collect_v2_coverage/collection/'
            'cover_kbc_v3_train_collection_train-collect_20260809T232616Z')
ANALYSIS = f'{OUT}_analysis'

!python scripts/analyze_capacity_diagnostic.py \
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
s = json.load(open(f'{ANALYSIS}/summary.json'))
print('=== FINAL PREDICTION ===')
print(' macro-F1 :', s['baseline_metrics']['macro-f1'], '->',
      s['diagnostic_metrics']['macro-f1'], f"({s['metric_delta']['macro-f1']:+})")
print(' macro-P  :', s['baseline_metrics']['macro-p'], '->', s['diagnostic_metrics']['macro-p'])
print(' macro-R  :', s['baseline_metrics']['macro-r'], '->', s['diagnostic_metrics']['macro-r'])
print(' correct  :', s['final_prediction']['baseline_correct_rows'], '->',
      s['final_prediction']['diagnostic_correct_rows'])
print(' empty    :', s['final_prediction']['baseline_empty_rows'], '->',
      s['final_prediction']['diagnostic_empty_rows'])

print('\n=== CANDIDATE RECALL (the primary question) ===')
cr = s['candidate_recall']
print(' gold-like candidate rows :', cr['baseline_gold_like_rows'], '->',
      cr['diagnostic_gold_like_rows'], f"({cr['delta']:+d})")
print(' mean candidates per row  :', cr['baseline_mean_candidates_per_row'], '->',
      cr['diagnostic_mean_candidates_per_row'])

print('\n=== SCALE ERRORS ===')
print(' baseline  :', s['ratio_buckets']['baseline'])
print(' diagnostic:', s['ratio_buckets']['diagnostic'])

print('\n=== TRANSITIONS ===')
for k, v in sorted(s['transitions'].items(), key=lambda kv: -kv[1]):
    print(f'  {k:52s} {v}')

print('\n=== DOWNSTREAM OPPORTUNITY ===')
print(' verification-budget rows :', s['opportunities']['verification_budget_rows'])
print(' numeric-resolver rows    :', s['opportunities']['numeric_resolver_rows'])
print(' baseline-correct regress :', s['baseline_correct_regressions'])

print('\nPROMOTE if final F1 improves materially without precision collapse,')
print('OR if gold-like candidate rows rise substantially and the extra candidates')
print('are better capacity variants. REJECT if candidate count rises without')
print('gold-like rows rising, or if 10x/100x buckets grow. The decision is a')
print('judgement over both tables - not a threshold on one number.')
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
    'audit': '0080',
    'relation': 'hasCapacity',
    'source_sha': SOURCE_SHA,
    'run_utc': RUN_UTC,
    'config': 'configs/experiments/v3_1_diag_capacity.yaml',
    'config_sha256': sha256('configs/experiments/v3_1_diag_capacity.yaml'),
    'train_sha256': sha256('benchmark/data/train.jsonl'),
    'class_b_feature': 'capacity_definition_prompt',
    'system_prompt_off_hash': '2fb9188dbeda44f3',
    'system_prompt_on_hash': 'bcffa96f37770392',
    'capacity_instruction_sha256':
        '27ea6e49bb5547f360effcf133c037fc6f2cd396fddd3f83e2eec12aa8c4ac36',
    'environment': env,
    'artifacts': {n: sha256(os.path.join(OUT, n))
                  for n in sorted(os.listdir(OUT))
                  if os.path.isfile(os.path.join(OUT, n))},
    'calibration_status': 'CALIBRATION_REVIEW_REQUIRED',
    'valid_for': ['promotion decision for capacity_definition_prompt'],
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
| row count != 100, or any non-capacity relation | CELL 12 |
| `failed_queries` or `unresolved_invariant_errors` > 0 | CELL 12 |

A run that trips a stop condition is not a negative result about the prompt - it
is an invalid experiment, and analysing it would attribute an orchestration or
provenance problem to the intervention.
