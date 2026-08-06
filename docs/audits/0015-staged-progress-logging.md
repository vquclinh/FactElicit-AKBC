# Audit 0015 — Staged Progress Logging

Status: **PASS**
Date: 2026-08-05
Scope: observability only. No inference behaviour changed.

---

## 1. Objective

A full staged run on Colab could sit for minutes with no output. This adds one
concise line per finished query, in every phase, flushed immediately, so a
notebook cell shows continuous `current / total` progress.

Nothing about how the system decides anything changed. No prompt, threshold,
controller decision, budget, parsing rule, verification step, selection rule,
model load, persistence format, query ordering or evaluation was touched.

---

## 2. Files changed

| File | Change |
| --- | --- |
| `scripts/run_staged.py` | Progress helpers (`line_buffer_stdout`, `_short`, `_runtime_calls`, `_emit`, `_with_progress`, `_describe_enumerate`, `_describe_verify`, `_manifest_total`, `_decide_reporter`); the four phase functions wrap their existing streams. |
| `src/cover_kbc/pipeline.py` | `decide()` gained one optional keyword, `on_result`, a pure observer called after each prediction is collected. Default `None` — the pre-change path exactly. Plus `Callable` on the existing typing import. |
| `tests/test_staged_progress_logging.py` | **New.** 15 tests. |

No other file changed. `benchmark/` untouched. No Claude-specific files.



### How it works

Phases A, B and the resume cycles already consumed a pipeline generator, so
reporting is a pass-through wrapper around that generator:

```python
for index, graph in enumerate(graphs, start=1):
    after = _runtime_calls(pipeline)
    _emit(tag, index, total, describe(graph), after - before, time.perf_counter() - mark)
    yield graph
    mark = time.perf_counter()
    before = after
```

It yields the same objects in the same order, reads `runtime.calls` (identity-
deduplicated, since Phase A aliases `verifier_runtime` to `runtime`), and takes
`perf_counter` deltas. The clock restarts *after* the consumer has persisted the
graph, so writer cost is not charged to the next query.

Phase C returns a `PipelineResult` rather than yielding, which is the only
reason `pipeline.py` is touched at all. `on_result` is called after
`_collect`; it cannot influence the decision.

Per-query lines supersede the pipeline's coarse every-25 counter, so
`progress=True` is no longer passed — that flag only ever printed.
`line_buffer_stdout()` makes the plain invocation behave like `python -u`.

---

## 3. Example output

Real run, `smoke_staged_roleswap.yaml`, three `awardWonBy` queries:

```
[PHASE A] enumerate  split=train  queries=3  dir=outputs/…
[PHASE A] [1/3] relation=awardWonBy subject="Nobel Prize in Physiology or Medicine" candidates=2 calls=4 elapsed=0.0s
[PHASE A] [2/3] relation=awardWonBy subject="Premier League Player of the Season" candidates=2 calls=4 elapsed=0.0s
[PHASE A] [3/3] relation=awardWonBy subject="FAI Gold Air Medal" candidates=2 calls=4 elapsed=0.0s
[PHASE B] verify  dir=outputs/…  queries=3
[PHASE B] [1/3] relation=awardWonBy candidates=2 verified=1 labels=INVALID:1 calls=4 elapsed=0.0s
[PHASE B] [2/3] relation=awardWonBy candidates=2 verified=1 labels=INVALID:1 calls=2 elapsed=0.0s
[PHASE B] [3/3] relation=awardWonBy candidates=2 verified=1 labels=INVALID:1 calls=2 elapsed=0.0s
[RESUME 1] role=enumerator  queries_waiting=3  dir=outputs/…
[RESUME 1] [1/3] relation=awardWonBy subject="Nobel Prize in Physiology or Medicine" candidates=2 calls=2 elapsed=0.0s
[RESUME 1] [2/3] relation=awardWonBy subject="Premier League Player of the Season" candidates=2 calls=2 elapsed=0.0s
[RESUME 1] [3/3] relation=awardWonBy subject="FAI Gold Air Medal" candidates=2 calls=2 elapsed=0.0s
[PHASE C] decide  dir=outputs/…  queries=3  (no model loaded)
[PHASE C] [1/3] predictions=1 stop="residual 0.099 below stop thres…" elapsed=0.00s
[PHASE C] [2/3] predictions=1 stop="residual 0.099 below stop thres…" elapsed=0.00s
[PHASE C] [3/3] predictions=1 stop="residual 0.099 below stop thres…" elapsed=0.00s
```

`calls=` is neural calls spent on *that* query in *that* phase. Subjects are
whitespace-collapsed and capped at 48 characters, stop reasons at 32. Nothing
prints prompts, logits, hidden states or candidate payloads; every line is
≤ 160 characters and that bound is asserted.

Phases B and C read the query count from the already-persisted
`query_manifest.json` so `[i/N]` has an `N` while streaming; without it they
degrade to `[i/?]` rather than failing.

---

## 4. Inference semantics are unchanged

Argued and tested, not asserted:

* **Reads only.** No progress function assigns to a graph, candidate,
  prediction or config. `_runtime_calls` reads `.calls`; `describe` reads
  `graph.query`, `graph.candidates` and labels already attached during Phase B.
* **No extra neural calls.** The wrapper never calls a runtime; it only reads
  the counter the runtime already maintains.
* **No RNG use.** Nothing here draws a random number or seeds anything.
* **Same persisted objects.** `_with_progress` yields the identical object it
  received, so `StageWriter` writes exactly what it wrote before.
* **`decide` is unchanged when unobserved.** `on_result=None` is the old code
  path verbatim.

The proof is empirical: `tests/test_staged_progress_logging.py` runs the real
CLI twice over the scripted backend — once normally, once with `_with_progress`
replaced by a pass-through and `_decide_reporter` returning `None`, which is the
pre-change code path — and compares `predictions.jsonl`, `diagnostics.json`,
`trace.jsonl`, `stage_a_enumerated.jsonl`, `stage_b_verified.jsonl` and
`query_manifest.json` **byte for byte**. The same comparison is repeated for the
role-swap path, including `stage_r1_enumerator.jsonl`.

---

## 5. Test results

```
python -m pytest -q
    1023 passed, 3 skipped        (1008 before; +15)

python -m pyflakes src/ tests/ scripts/
    clean

python scripts/audit_model_budget.py configs/experiments/cover_kbc_v2_mistral24_qwen4.yaml
    total: 28.67B   RESULT: PASS
```

The 15 new tests cover: byte-identical artefacts with and without reporting;
identical neural call counts; `_with_progress` as an identity transform that
spends nothing and de-duplicates aliased runtimes; `decide` identical with and
without an observer; one `[i/N]` line per query in A, B, C and the resume cycle;
required fields on each line; existing phase summaries and `metrics.json`
preserved; verify-line label tallies; missing/corrupt manifest degrading to
`[i/?]`; `flush=True` on every progress print; `line_buffer_stdout` safe on a
stream with no `reconfigure` and on a detached one; line length and payload
leakage bounds; subject collapsing and truncation.

---

## 6. Benchmark integrity

```
git status --porcelain benchmark/   → (empty)
git diff -- benchmark/              → (empty)
git diff --cached -- benchmark/     → (empty)
```

Upstream pin `30d8cfaaa7af5b236054cfb361f57b7d0c1e6e57` intact. Smoke runs used
`--split train` only; no val or test data was read.
