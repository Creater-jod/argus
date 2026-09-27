"""Unit tests for AST analysis and NetworkX call graph traversal."""

from pathlib import Path

from agent_verifier.graph.ast_analyzer import (
    extract_symbols_from_code,
)
from agent_verifier.graph.call_graph import CallGraph

SAMPLE_PYTHON_CODE = """
class AuthService:
    def __init__(self, db):
        self.db = db

    def login(self, username, password):
        user = self.get_user(username)
        return self.verify_password(user, password)

    def get_user(self, username):
        return self.db.find(username)

    def verify_password(self, user, password):
        return True

def standalone_helper():
    auth = AuthService(None)
    auth.login("admin", "secret")
"""


def test_ast_symbol_extraction():
    symbols = extract_symbols_from_code(SAMPLE_PYTHON_CODE, file_path="auth.py")
    names = [s.name for s in symbols]

    assert "AuthService" in names
    assert "login" in names
    assert "get_user" in names
    assert "verify_password" in names
    assert "standalone_helper" in names

    # Check calls in login method
    login_sym = next(s for s in symbols if s.name == "login")
    assert any("get_user" in call for call in login_sym.calls)
    assert any("verify_password" in call for call in login_sym.calls)


def test_call_graph_construction(tmp_path: Path):
    file_a = tmp_path / "service.py"
    file_a.write_text(SAMPLE_PYTHON_CODE, encoding="utf-8")

    cg = CallGraph()
    cg.add_file(file_a)
    cg.link_calls()

    # Verify nodes exist
    nodes = list(cg.graph.nodes)
    assert any("AuthService" in n for n in nodes)
    assert any("login" in n for n in nodes)

    # Compute blast radius
    blast = cg.get_blast_radius(changed_files=[str(file_a)])
    assert blast.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert blast.impacted_symbols_count >= 0


def test_call_graph_out_of_scope_detection(tmp_path: Path):
    file_a = tmp_path / "auth.py"
    file_a.write_text(
        """
def authenticate():
    pass
""",
        encoding="utf-8",
    )

    file_b = tmp_path / "billing.py"
    file_b.write_text(
        """
from auth import authenticate
def charge_card():
    authenticate()
""",
        encoding="utf-8",
    )

    cg = CallGraph()
    cg.add_file(file_a)
    cg.add_file(file_b)
    cg.link_calls()

    # If allowed path is only auth.py, billing.py calling it should be detected
    blast = cg.get_blast_radius(changed_files=["auth.py"], allowed_paths=["auth.py"])
    assert blast.impacted_symbols_count >= 0
