"""Unit tests for the VerificationPipeline orchestrator."""

from pathlib import Path

from agent_verifier.models.session_claim import SessionClaim
from agent_verifier.models.trust_report import Verdict
from agent_verifier.pipeline import VerificationPipeline


def test_pipeline_runs_on_clean_repo():
    pipeline = VerificationPipeline()
    claim = SessionClaim(summary="No changes")

    report = pipeline.run(
        repo_path=Path("."),
        claim=claim,
        skip_tests=True,
        skip_graph=True,
    )

    assert report.verdict in (Verdict.VERIFIED, Verdict.SUSPICIOUS, Verdict.FAILED)
    assert report.duration_seconds >= 0.0
    assert report.diff_verification is not None
    assert report.test_verification is not None
    assert report.scope_verification is not None
    assert report.spec_compliance is not None


def test_pipeline_handles_custom_spec(tmp_path: Path):
    import subprocess

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    pipeline = VerificationPipeline()
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("- [ ] Must update quantum telemetry\n- [ ] Must log tachyon pulse", encoding="utf-8")

    claim = SessionClaim(summary="Updated quantum telemetry and logged tachyon pulse")
    report = pipeline.run(
        repo_path=repo_dir,
        claim=claim,
        spec_path=str(spec_file),
        skip_tests=True,
        skip_graph=True,
    )

    assert report.spec_compliance is not None
    assert len(report.spec_compliance.unmet_requirements) == 2
    assert len(report.spec_compliance.hallucinated_claims) == 2
