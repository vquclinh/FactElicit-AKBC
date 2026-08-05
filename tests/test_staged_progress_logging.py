"""Progress logging for the staged CLI is observability only.

Two things have to hold, and they are tested separately:

* the run produces exactly the same artefacts with reporting as without - the
  same predictions, the same neural call counts, the same persisted stages;
* the ``[i/N]`` lines actually appear, one per query, in every phase.

The first is the load-bearing one. It is checked by running the real CLI twice
over the scripted backend - once normally, once with progress reporting patched
out entirely - and comparing the output files byte for byte.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

CONFIG = "configs/experiments/smoke_staged_scripted.yaml"
#: Enables the active controller, so Phase B leaves enumerator-role work and
#: the ``[RESUME n]`` cycle actually runs.
ROLESWAP = "configs/experiments/smoke_staged_roleswap.yaml"
BORDERS = "countryLandBordersCountry"
ARTEFACTS = (
    "predictions.jsonl",
    "diagnostics.json",
    "trace.jsonl",
    "stage_a_enumerated.jsonl",
    "stage_b_verified.jsonl",
    "query_manifest.json",
)


@pytest.fixture(scope="module")
def cli():
    """The real ``scripts/run_staged.py``, loaded as a module."""
    scripts_dir = str(Path("scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_staged", "scripts/run_staged.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    cli,
    run_dir: Path,
    monkeypatch,
    *,
    silent: bool = False,
    config: str = CONFIG,
    relation: str = BORDERS,
    limit: int = 4,
) -> None:
    """Run ``run_staged.py all`` over a few scripted queries."""
    if silent:
        # Remove progress reporting altogether: the wrapper becomes a pass-through
        # and ``decide`` gets no observer, which is the pre-change code path.
        monkeypatch.setattr(cli, "_with_progress", lambda graphs, *a, **k: graphs)
        monkeypatch.setattr(cli, "_decide_reporter", lambda total: None)
    monkeypatch.setattr(
        sys, "argv",
        [
            "run_staged.py", "all",
            "--config", config,
            "--split", "train",
            "--limit", str(limit),
            "--relation", relation,
            "--run-dir", str(run_dir),
        ],
    )
    assert cli.main() == 0


# --------------------------------------------------------------------------
# Inference results are unchanged
# --------------------------------------------------------------------------


def test_progress_reporting_changes_no_artefact(cli, tmp_path, monkeypatch, capsys):
    loud, quiet = tmp_path / "loud", tmp_path / "quiet"
    _run(cli, loud, monkeypatch)
    _run(cli, quiet, monkeypatch, silent=True)
    capsys.readouterr()

    for name in ARTEFACTS:
        assert (loud / name).read_bytes() == (quiet / name).read_bytes(), name


def test_progress_reporting_changes_no_neural_call_count(cli, tmp_path, monkeypatch, capsys):
    loud, quiet = tmp_path / "loud", tmp_path / "quiet"
    _run(cli, loud, monkeypatch)
    _run(cli, quiet, monkeypatch, silent=True)
    capsys.readouterr()

    a = json.loads((loud / "diagnostics.json").read_text())
    b = json.loads((quiet / "diagnostics.json").read_text())
    for key in ("total_calls", "total_verification_calls", "total_generated_tokens"):
        if key in a or key in b:
            assert a.get(key) == b.get(key), key
    assert a == b


def test_with_progress_is_an_identity_transform(cli):
    """The wrapper must yield the same objects, in order, and spend nothing."""

    class _Runtime:
        calls = 7

    class _Pipeline:
        runtime = _Runtime()
        verifier_runtime = runtime          # Phase A aliases the two names

    items = [object(), object(), object()]
    seen = list(cli._with_progress(iter(items), "[T]", 3, _Pipeline(), lambda _g: "x"))

    assert seen == items
    assert all(a is b for a, b in zip(seen, items))
    assert _Pipeline.runtime.calls == 7
    # Aliased runtimes are counted once, not twice.
    assert cli._runtime_calls(_Pipeline()) == 7


def test_decide_observer_does_not_change_the_result(borders_query, borders_contract):
    """``decide`` behaves identically with and without an observer."""
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.models.offline import NullRuntime
    from cover_kbc.pipeline import CoverPipeline, PipelineConfig

    def graphs():
        return [build_graph(borders_query, borders_contract)]

    pipeline = CoverPipeline(NullRuntime(model_id="offline/null"), PipelineConfig())
    plain = pipeline.decide(graphs())

    seen = []
    observed = pipeline.decide(graphs(), on_result=lambda p, g: seen.append((p, g)))

    assert len(seen) == 1
    assert [p.to_official_row() for p in plain.predictions] == [
        p.to_official_row() for p in observed.predictions
    ]
    assert plain.total_calls == observed.total_calls
    assert plain.total_verification_calls == observed.total_verification_calls
    assert plain.empty_reasons == observed.empty_reasons
    assert plain.stop_reasons == observed.stop_reasons


# --------------------------------------------------------------------------
# The lines are actually emitted
# --------------------------------------------------------------------------


def test_every_phase_emits_one_indexed_line_per_query(cli, tmp_path, monkeypatch, capsys):
    _run(cli, tmp_path / "run", monkeypatch)
    lines = capsys.readouterr().out.splitlines()

    for tag in ("PHASE A", "PHASE B", "PHASE C"):
        pattern = re.compile(rf"^\[{tag}\] \[(\d+)/(\d+)\] ")
        found = [pattern.match(ln) for ln in lines]
        positions = [(m.group(1), m.group(2)) for m in found if m]
        assert positions == [(str(i), "4") for i in range(1, 5)], tag


def test_role_swap_cycles_report_progress_and_change_nothing(cli, tmp_path, monkeypatch, capsys):
    """The resume phase runs the same wrapper, under a ``[RESUME n]`` tag."""
    loud, quiet = tmp_path / "loud", tmp_path / "quiet"
    _run(cli, loud, monkeypatch, config=ROLESWAP, relation="awardWonBy", limit=3)
    out = capsys.readouterr().out
    _run(cli, quiet, monkeypatch, config=ROLESWAP, relation="awardWonBy", limit=3, silent=True)
    capsys.readouterr()

    resumed = [ln for ln in out.splitlines() if re.match(r"^\[RESUME \d+\] \[\d+/3\] ", ln)]
    assert len(resumed) == 3, "the role-swap cycle reported no per-query progress"
    for name in ("predictions.jsonl", "diagnostics.json", "stage_r1_enumerator.jsonl"):
        assert (loud / name).read_bytes() == (quiet / name).read_bytes(), name


def test_phase_a_line_carries_the_required_fields(cli, tmp_path, monkeypatch, capsys):
    _run(cli, tmp_path / "run", monkeypatch)
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[PHASE A] [1/4]")
    )
    for field in ("relation=", "subject=", "candidates=", "calls=", "elapsed="):
        assert field in line, field
    assert line.endswith("s")


def test_phase_c_line_carries_the_required_fields(cli, tmp_path, monkeypatch, capsys):
    _run(cli, tmp_path / "run", monkeypatch)
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[PHASE C] [1/4]")
    )
    for field in ("predictions=", "stop=", "elapsed="):
        assert field in line, field


def test_existing_phase_summaries_survive(cli, tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    _run(cli, run_dir, monkeypatch)
    out = capsys.readouterr().out

    assert "[PHASE A] enumerate" in out
    assert "[PHASE B] verify" in out
    assert "[PHASE C] decide" in out
    assert "predictions :" in out
    assert (run_dir / "metrics.json").is_file()      # final metrics still written


def test_verify_line_reports_labels_already_on_the_candidates(cli, borders_query, borders_contract):
    from cover_kbc.evidence.graph import build_graph
    from cover_kbc.types import Candidate, VerificationLabel, VerificationResult

    graph = build_graph(borders_query, borders_contract)
    for value, label in (("Alpha", VerificationLabel.VALID), ("Beta", VerificationLabel.INVALID)):
        candidate = Candidate(
            key=value.lower(), display_value=value, relation=borders_contract.relation
        )
        candidate.verifications.append(
            VerificationResult(candidate_key=candidate.key, label=label)
        )
        graph.candidates[candidate.key] = candidate

    body = cli._describe_verify(graph)
    assert "candidates=2" in body
    assert "verified=2" in body
    assert f"{VerificationLabel.VALID.value}:1" in body
    assert f"{VerificationLabel.INVALID.value}:1" in body


def test_no_query_total_degrades_to_a_question_mark(cli, tmp_path):
    assert cli._manifest_total(tmp_path) is None
    (tmp_path / cli.QUERY_MANIFEST).write_text("{ not json")
    assert cli._manifest_total(tmp_path) is None


# --------------------------------------------------------------------------
# Colab-friendliness
# --------------------------------------------------------------------------


def test_progress_prints_are_flushed(cli, monkeypatch):
    """Colab shows nothing until the stream is flushed."""
    flushes = []
    real_print = builtins.print
    monkeypatch.setattr(
        builtins, "print",
        lambda *a, **k: (flushes.append(k.get("flush")), real_print(*a, **k))[1],
    )

    cli._emit("[T]", 1, 4, "relation=r", 2, 1.25)
    cli._decide_reporter(4)(_FakePrediction(), None)

    assert flushes == [True, True]


class _FakePrediction:
    object_entities = ["x"]
    stopped_reason = "done"


def test_line_buffer_stdout_is_safe_on_any_stream(cli, monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    cli.line_buffer_stdout()          # StringIO has no reconfigure; must not raise

    class _Detached:
        def reconfigure(self, **_):
            raise ValueError("detached")

    monkeypatch.setattr(sys, "stdout", _Detached())
    cli.line_buffer_stdout()


def test_progress_lines_stay_short_and_leak_no_payload(cli, tmp_path, monkeypatch, capsys):
    _run(cli, tmp_path / "run", monkeypatch)
    indexed = [
        ln for ln in capsys.readouterr().out.splitlines()
        if ln.startswith(("[PHASE A] [", "[PHASE B] [", "[PHASE C] [", "[RESUME "))
    ]
    assert indexed
    for line in indexed:
        assert len(line) <= 160, line
        lowered = line.lower()
        for forbidden in ("prompt", "logit", "hidden_state", "system:", "assistant"):
            assert forbidden not in lowered, line


def test_subject_is_collapsed_and_truncated(cli):
    assert cli._short("a\n  b\tc") == "a b c"
    long = "x" * 200
    short = cli._short(long)
    assert len(short) == 48 and short.endswith("…")
    assert "\n" not in cli._short("line\nbreak")
