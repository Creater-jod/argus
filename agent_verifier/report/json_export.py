"""JSON persistence and loading for TrustReport."""

from __future__ import annotations

from pathlib import Path

from agent_verifier.models.trust_report import TrustReport


def export_json(report: TrustReport, output_path: str | Path, indent: int = 2) -> Path:
    """Save TrustReport object as a formatted JSON file."""
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_data = report.model_dump_json(indent=indent)
    path.write_text(json_data, encoding="utf-8")
    return path


def load_json_report(filepath: str | Path) -> TrustReport:
    """Load and validate a TrustReport instance from a JSON file."""
    path = Path(filepath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"TrustReport file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    return TrustReport.model_validate_json(raw)
