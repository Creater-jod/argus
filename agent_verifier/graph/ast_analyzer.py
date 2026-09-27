"""Python AST-based symbol and invocation extraction."""

from __future__ import annotations

import ast
from pathlib import Path

from pydantic import BaseModel, Field


class Symbol(BaseModel):
    """Represents a code symbol (function, method, class) and its dependencies."""

    name: str
    qualified_name: str
    file_path: str
    symbol_type: str  # "function", "async_function", "class", "method"
    start_line: int
    end_line: int
    calls: list[str] = Field(default_factory=list)
    docstring: str | None = None


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.symbols: list[Symbol] = []
        self._scope_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qname = ".".join(self._scope_stack + [node.name])
        calls = self._collect_calls(node)
        self.symbols.append(
            Symbol(
                name=node.name,
                qualified_name=qname,
                file_path=self.file_path,
                symbol_type="class",
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                calls=calls,
                docstring=ast.get_docstring(node),
            )
        )
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_func(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_func(node, is_async=True)

    def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        stype = "method" if self._scope_stack else ("async_function" if is_async else "function")
        qname = ".".join(self._scope_stack + [node.name])
        calls = self._collect_calls(node)

        self.symbols.append(
            Symbol(
                name=node.name,
                qualified_name=qname,
                file_path=self.file_path,
                symbol_type=stype,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                calls=calls,
                docstring=ast.get_docstring(node),
            )
        )
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def _collect_calls(self, root: ast.AST) -> list[str]:
        calls: set[str] = set()
        for child in ast.walk(root):
            if isinstance(child, ast.Call):
                call_name = self._resolve_call_name(child.func)
                if call_name:
                    calls.add(call_name)
        return sorted(list(calls))

    @staticmethod
    def _resolve_call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            base = _SymbolVisitor._resolve_call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return None


def extract_symbols_from_code(code: str, file_path: str = "<string>") -> list[Symbol]:
    """Parse python source string and extract all function and class symbols."""
    try:
        tree = ast.parse(code, filename=file_path)
    except (SyntaxError, UnicodeDecodeError):
        return []

    visitor = _SymbolVisitor(file_path=file_path)
    visitor.visit(tree)
    return visitor.symbols


def extract_symbols_from_file(file_path: Path | str) -> list[Symbol]:
    """Read a python file and extract symbol definitions and call links."""
    p = Path(file_path)
    if not p.is_file() or p.suffix != ".py":
        return []

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        norm_path = str(p).replace("\\", "/")
        return extract_symbols_from_code(content, file_path=norm_path)
    except Exception:
        return []
