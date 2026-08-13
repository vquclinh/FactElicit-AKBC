"""Neural runtime preflight, caught before checkpoint download.

On an L4 (Audit 0065) ``transformers`` 4.57.6 raised *"The checkpoint has model
type qwen3_5 but Transformers does not recognize this architecture"* — after
minutes of downloading, at the moment the verifier was constructed.

That remains a historical Qwen-profile concern, but the active Profile E3
pipeline is Mistral-only. The runner now enforces the stricter Qwen floor only
when a declared neural block actually names Qwen.

Every path here runs without importing transformers, without CUDA and without
downloading a byte: the version is passed in as a string and the model blocks
are plain dicts.
"""

from __future__ import annotations

import sys

import pytest
import yaml

from cover_kbc.models.preflight import (
    MISTRAL3_MIN_TRANSFORMERS,
    MISTRAL3_MODEL_TYPE,
    NEURAL_BACKENDS,
    QWEN3_5_MIN_TRANSFORMERS,
    QWEN3_5_MODEL_TYPE,
    check_mistral3,
    check_qwen3_5,
    huggingface_runtime_report,
    installed_transformers_version,
    needs_transformers,
    require_huggingface_runtime,
    supports_mistral3,
    supports_qwen3_5,
)
from cover_kbc.paths import REPO_ROOT

EXPERIMENTS = REPO_ROOT / "configs" / "experiments"
QWEN_BLOCK = {
    "backend": "huggingface",
    "model_id": "Qwen/Qwen3.5-4B",
    "family": "qwen",
}
MISTRAL_BLOCK = {
    "backend": "huggingface",
    "model_id": "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    "family": "mistral",
    "tokenizer_backend": "mistral_common",
}
STUB_BLOCK = {"backend": "scripted", "model_id": "offline/scripted"}


# ==========================================================================
# the transformers floor
# ==========================================================================


def test_the_floor_is_the_first_release_that_ships_qwen3_5() -> None:
    """5.2.0. Verified against the upstream tree, not guessed."""
    assert QWEN3_5_MIN_TRANSFORMERS == (5, 2, 0)
    assert QWEN3_5_MODEL_TYPE == "qwen3_5"


def test_active_mistral_floor_is_lower_than_historical_qwen_floor() -> None:
    assert MISTRAL3_MIN_TRANSFORMERS == (4, 52, 4)
    assert MISTRAL3_MODEL_TYPE == "mistral3"
    assert MISTRAL3_MIN_TRANSFORMERS < QWEN3_5_MIN_TRANSFORMERS


@pytest.mark.parametrize("version", ["4.51.0", "4.52.4", "4.57.0", "4.57.6",
                                     "5.0.0", "5.1.0", "5.1.9"])
def test_versions_below_the_floor_are_refused(version) -> None:
    """4.57.6 is the one the real run actually failed on."""
    assert supports_qwen3_5(version) is False
    report = check_qwen3_5(version)
    assert report.blockers
    assert QWEN3_5_MODEL_TYPE in report.blockers[0]
    assert "5.2.0" in report.blockers[0]


@pytest.mark.parametrize("version", ["5.2.0", "5.2.1", "5.3.0", "5.14.1",
                                     "6.0.0"])
def test_versions_at_or_above_the_floor_are_accepted(version) -> None:
    assert supports_qwen3_5(version) is True
    assert check_qwen3_5(version).blockers == ()


def test_the_version_that_ran_successfully_is_accepted() -> None:
    """5.14.1 is what the successful real-weight run used."""
    assert supports_qwen3_5("5.14.1") is True


def test_a_prerelease_of_the_floor_still_counts() -> None:
    """`5.2.0.dev0` carries the module; the numeric prefix is what matters."""
    assert supports_qwen3_5("5.2.0.dev0") is True


def test_an_absent_transformers_is_refused_with_the_install_command() -> None:
    report = check_qwen3_5("")
    assert report.blockers
    assert "not installed" in report.blockers[0]
    assert ".[hf]" in report.blockers[0]


@pytest.mark.parametrize("version", ["4.52.4", "4.57.6", "5.1.9", "5.2.0"])
def test_active_mistral_versions_at_or_above_floor_are_accepted(version) -> None:
    assert supports_mistral3(version) is True
    assert check_mistral3(version).blockers == ()


def test_active_mistral_versions_below_floor_are_refused() -> None:
    report = check_mistral3("4.51.0")
    assert supports_mistral3("4.51.0") is False
    assert report.blockers
    assert MISTRAL3_MODEL_TYPE in report.blockers[0]
    assert "4.52.4" in report.blockers[0]


def test_the_version_probe_never_imports_transformers() -> None:
    """It runs before any model work; importing transformers pulls in torch."""
    import inspect

    from cover_kbc.models import preflight

    source = inspect.getsource(preflight)
    assert "import transformers" not in source
    assert "importlib.metadata" in source
    # ...and it answers on this machine, which has no transformers at all.
    assert isinstance(installed_transformers_version(), str)


# ==========================================================================
# only a neural profile is asked for transformers
# ==========================================================================


def test_a_stub_profile_needs_no_transformers() -> None:
    """The abstain and scripted smokes must keep running on a bare machine."""
    assert needs_transformers(STUB_BLOCK, STUB_BLOCK) is False
    report = huggingface_runtime_report(STUB_BLOCK, STUB_BLOCK,
                                        version_string="4.51.0")
    assert report.ready is True
    require_huggingface_runtime(STUB_BLOCK, STUB_BLOCK, version_string="4.51.0")


def test_a_neural_profile_is_checked() -> None:
    assert needs_transformers(STUB_BLOCK, QWEN_BLOCK) is True
    report = huggingface_runtime_report(STUB_BLOCK, QWEN_BLOCK,
                                        version_string="4.57.6")
    assert report.ready is False


def test_active_mistral_profile_does_not_inherit_qwen_floor() -> None:
    report = huggingface_runtime_report(MISTRAL_BLOCK, MISTRAL_BLOCK,
                                        version_string="4.57.6")
    assert report.ready is True
    assert any(MISTRAL3_MODEL_TYPE in item for item in report.satisfied)
    require_huggingface_runtime(MISTRAL_BLOCK, MISTRAL_BLOCK,
                                version_string="4.57.6")


@pytest.mark.parametrize("backend", sorted(NEURAL_BACKENDS))
def test_every_neural_backend_alias_is_recognised(backend) -> None:
    assert needs_transformers({"backend": backend}) is True


def test_the_gate_raises_and_names_the_fix() -> None:
    with pytest.raises(RuntimeError, match="cannot load the declared models"):
        require_huggingface_runtime(QWEN_BLOCK, version_string="4.57.6")


def test_the_gate_is_silent_on_a_ready_environment() -> None:
    require_huggingface_runtime(QWEN_BLOCK, version_string="5.2.0")


# ==========================================================================
# the declaration agrees with what the preflight enforces
# ==========================================================================


def test_pyproject_pins_the_active_mistral_floor_not_the_historical_qwen_floor() -> None:
    body = (REPO_ROOT / "pyproject.toml").read_text()
    assert '"transformers>=4.52.4"' in body
    assert '"transformers>=5.2.0"' not in body
    # The old floor could never have loaded the verifier.
    assert '"transformers>=4.51.0"' not in body
    assert '">=4.57"' not in body


def test_the_retired_portfolio_cuda_extras_are_gone() -> None:
    """Nemotron is retired, so its Mamba kernels are not a dependency."""
    body = (REPO_ROOT / "pyproject.toml").read_text()
    assert "portfolio = [" not in body
    for package in ("mamba-ssm", "causal-conv1d"):
        assert package not in body, package
    readme = (REPO_ROOT / "README.md").read_text()
    for package in ("mamba-ssm", "causal-conv1d", "--no-build-isolation"):
        assert package not in readme, package


def test_historical_qwen_profile_is_what_needs_the_stricter_floor() -> None:
    config = yaml.safe_load(
        (EXPERIMENTS / "cover_kbc_v2_test.yaml").read_text())
    assert config["model_profile"]["verifier"]["model_id"] == "Qwen/Qwen3.5-4B"
    report = huggingface_runtime_report(
        config["model_profile"]["verifier"],
        version_string="4.57.6",
    )
    assert report.ready is False


# ==========================================================================
# the runner calls it, before any weight is fetched
# ==========================================================================


def _runner():
    import importlib.util

    path = REPO_ROOT / "scripts" / "run_cover.py"
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("run_cover_preflight",
                                                      path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


def test_the_runner_preflights_before_building_a_runtime() -> None:
    source = (REPO_ROOT / "scripts" / "run_cover.py").read_text()
    body = source[source.index("def main("):]
    call = body.index("require_huggingface_runtime(")
    assert call < body.index("build_runtime(")


def test_the_runner_imports_the_preflight() -> None:
    runner = _runner()
    assert hasattr(runner, "require_huggingface_runtime")


def test_a_stub_config_still_runs_without_transformers(tmp_path,
                                                       monkeypatch) -> None:
    """The decisive one: a bare machine can still run the offline smokes."""
    runner = _runner()

    class _Built(RuntimeError):
        pass

    monkeypatch.setattr(runner, "build_runtime",
                        lambda block: (_ for _ in ()).throw(_Built("reached")))
    monkeypatch.setattr(sys, "argv", [
        "run_cover.py", "--config",
        str(EXPERIMENTS / "smoke_abstain.yaml")])
    # Reaching `build_runtime` proves the preflight did not refuse.
    with pytest.raises(_Built):
        runner.main()
