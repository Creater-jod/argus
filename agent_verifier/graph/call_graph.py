"""Call graph construction and blast radius evaluation using NetworkX.

Builds a directed call graph from Python AST symbol extraction and
computes upstream/downstream impact to quantify the blast radius
of code changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, Field

from agent_verifier.graph.ast_analyzer import Symbol, extract_symbols_from_file
from agent_verifier.models.trust_report import RiskLevel

logger = logging.getLogger("agent_verify.graph")


class BlastRadiusResult(BaseModel):
    """Calculated blast radius impact of changed files/symbols."""

    risk_level: RiskLevel = RiskLevel.LOW
    changed_symbols: list[str] = Field(default_factory=list)
    impacted_symbols: list[str] = Field(default_factory=list)
    impacted_symbols_count: int = 0
    impacted_files: list[str] = Field(default_factory=list)
    out_of_scope_impacts: list[str] = Field(default_factory=list)
    notes: str = ""


class CallGraph:
    """Builds and analyzes caller-callee dependencies across repository files."""

    def __init__(self):
        self.graph = nx.DiGraph()
        self.symbols_by_name: dict[str, list[Symbol]] = {}
        self.symbols_by_file: dict[str, list[Symbol]] = {}

    def add_file(self, file_path: Path | str) -> list[Symbol]:
        """Extract symbols from a file and add them to the call graph."""
        symbols = extract_symbols_from_file(file_path)
        norm_file = str(file_path).replace("\\", "/")

        self.symbols_by_file[norm_file] = symbols
        for sym in symbols:
            node_id = f"{norm_file}::{sym.name}"
            self.graph.add_node(
                node_id,
                file=norm_file,
                name=sym.name,
                qualified_name=sym.qualified_name,
                symbol_type=sym.symbol_type,
                line=sym.start_line,
            )
            self.symbols_by_name.setdefault(sym.name, []).append(sym)

        return symbols

    def link_calls(self) -> None:
        """Resolve call links across indexed symbols and create directed edges."""
        for file_path, symbols in self.symbols_by_file.items():
            for caller in symbols:
                caller_id = f"{file_path}::{caller.name}"
                for called_name in caller.calls:
                    # Match against indexed targets
                    targets = self.symbols_by_name.get(called_name, [])
                    # Match simple name or attribute leaf
                    if not targets and "." in called_name:
                        leaf = called_name.split(".")[-1]
                        targets = self.symbols_by_name.get(leaf, [])

                    for target in targets:
                        target_id = f"{target.file_path}::{target.name}"
                        if caller_id != target_id:
                            self.graph.add_edge(caller_id, target_id, relation="calls")

    def build_from_repo(self, repo_path: Path | str, max_files: int = 200) -> None:
        """Scan repository Python files and build full call graph."""
        p = Path(repo_path).resolve()
        count = 0
        for py_file in p.rglob("*.py"):
            # Skip hidden, virtual environments, build and test caches
            parts = py_file.parts
            if any(
                part.startswith(".")
                or part in ("venv", ".venv", "build", "dist", "__pycache__", "site-packages")
                for part in parts
            ):
                continue

            self.add_file(py_file)
            count += 1
            if count >= max_files:
                logger.warning(
                    "Call graph file limit reached (%d). Remaining Python files skipped.",
                    max_files,
                )
                break

        self.link_calls()
        logger.info(
            "Call graph built: %d files, %d nodes, %d edges",
            count,
            self.graph.number_of_nodes(),
            self.graph.number_of_edges(),
        )

    def get_blast_radius(
        self,
        changed_files: list[str],
        allowed_paths: list[str] | None = None,
        max_hops: int = 2,
    ) -> BlastRadiusResult:
        """Determine downstream impact of changes to the specified files."""
        allowed_paths = allowed_paths or []
        changed_nodes: set[str] = set()

        for c_file in changed_files:
            norm_c = c_file.replace("\\", "/").strip().lower()
            if norm_c.startswith("./"):
                norm_c = norm_c[2:]
            for node, data in self.graph.nodes(data=True):
                node_file = data.get("file", "").lower()
                if node_file.endswith(norm_c) or norm_c in node_file:
                    changed_nodes.add(node)

        # Reverse graph to traverse upstream callers (who depends on changed code)
        # plus forward graph for downstream callees
        impacted_nodes: set[str] = set()
        for c_node in changed_nodes:
            # Callers (upstream impact)
            try:
                upstream = nx.single_source_shortest_path_length(
                    self.graph.reverse(copy=False), c_node, cutoff=max_hops
                )
                impacted_nodes.update(upstream.keys())
            except nx.NetworkXError as e:
                logger.debug("Upstream traversal error for %s: %s", c_node, e)

            # Callees (downstream impact)
            try:
                downstream = nx.single_source_shortest_path_length(
                    self.graph, c_node, cutoff=max_hops
                )
                impacted_nodes.update(downstream.keys())
            except nx.NetworkXError as e:
                logger.debug("Downstream traversal error for %s: %s", c_node, e)

        impacted_nodes = impacted_nodes - changed_nodes

        impacted_files_set: set[str] = set()
        out_of_scope_impacts: list[str] = []

        for node in impacted_nodes:
            data = self.graph.nodes[node]
            f = data.get("file", "")
            impacted_files_set.add(f)

            if allowed_paths:
                norm_f = f.replace("\\", "/").lower()
                is_allowed = any(ap.lower() in norm_f for ap in allowed_paths)
                if not is_allowed:
                    out_of_scope_impacts.append(f"{node} in {f}")

        impact_count = len(impacted_nodes)

        if impact_count == 0 or (impact_count <= 4 and not out_of_scope_impacts):
            risk = RiskLevel.LOW
        elif impact_count <= 8 and len(out_of_scope_impacts) <= 1:
            risk = RiskLevel.MEDIUM
        elif impact_count <= 15 or len(out_of_scope_impacts) <= 3:
            risk = RiskLevel.HIGH
        else:
            risk = RiskLevel.CRITICAL

        notes = (
            f"Blast radius: {len(changed_nodes)} source symbol(s) -> "
            f"{impact_count} impacted symbol(s) across {len(impacted_files_set)} file(s). "
            f"Risk: {risk.value}."
        )
        if out_of_scope_impacts:
            notes += (
                f" {len(out_of_scope_impacts)} out-of-scope caller/callee relation(s) detected."
            )

        return BlastRadiusResult(
            risk_level=risk,
            changed_symbols=sorted(list(changed_nodes)),
            impacted_symbols=sorted(list(impacted_nodes)),
            impacted_symbols_count=impact_count,
            impacted_files=sorted(list(impacted_files_set)),
            out_of_scope_impacts=out_of_scope_impacts,
            notes=notes,
        )


def compute_repo_blast_radius(
    repo_path: Path | str,
    changed_files: list[str],
    allowed_paths: list[str] | None = None,
) -> BlastRadiusResult:
    """Convenience function to build graph and compute blast radius in one pass."""
    cg = CallGraph()
    cg.build_from_repo(repo_path)
    return cg.get_blast_radius(changed_files, allowed_paths=allowed_paths)
