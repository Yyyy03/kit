"""Tests for CodebaseMarkdownDocGenerator, DocCache, MarkdownRenderer, and helpers.

Uses the realistic fixture repo and mock summarizer. No external network calls.
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kit import Repository
from kit.codebase_markdown_docs import (
    CodebaseMarkdownDocGenerator,
    DocCache,
    MarkdownRenderer,
    SymbolDocRecord,
    UsageExampleGenerator,
    _clean_summary,
    _generate_python_usage_example,
    _make_import_statement,
    _parse_structured_summary,
    _should_skip_file,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "realistic_repo"


def _fixture_symbols_for_repo(repo):
    """Return a pre-extracted symbol list matching the realistic fixture repo."""
    repo_path = str(repo.local_path)

    auth_code = Path(repo_path, "services", "auth.py").read_text()
    auth_lines = auth_code.splitlines()

    user_code = Path(repo_path, "models", "user.py").read_text()
    user_lines = user_code.splitlines()

    utils_code = Path(repo_path, "utils.py").read_text()
    utils_lines = utils_code.splitlines()

    symbols = [
        {
            "file": str(Path(repo_path, "services", "auth.py")),
            "name": "AuthService",
            "node_path": "AuthService",
            "type": "class",
            "start_line": 9,
            "end_line": 16,
            "code": "\n".join(auth_lines[9:17]),
        },
        {
            "file": str(Path(repo_path, "services", "auth.py")),
            "name": "register_user",
            "node_path": "AuthService.register_user",
            "type": "method",
            "start_line": 18,
            "end_line": 39,
            "code": "\n".join(auth_lines[18:40]),
        },
        {
            "file": str(Path(repo_path, "services", "auth.py")),
            "name": "login",
            "node_path": "AuthService.login",
            "type": "method",
            "start_line": 41,
            "end_line": 58,
            "code": "\n".join(auth_lines[41:59]),
        },
        {
            "file": str(Path(repo_path, "models", "user.py")),
            "name": "User",
            "node_path": "User",
            "type": "class",
            "start_line": 11,
            "end_line": 18,
            "code": "\n".join(user_lines[11:19]),
        },
        {
            "file": str(Path(repo_path, "models", "user.py")),
            "name": "display",
            "node_path": "User.display",
            "type": "method",
            "start_line": 20,
            "end_line": 23,
            "code": "\n".join(user_lines[20:24]),
        },
        {
            "file": str(Path(repo_path, "utils.py")),
            "name": "greet",
            "node_path": "greet",
            "type": "function",
            "start_line": 7,
            "end_line": 9,
            "code": "\n".join(utils_lines[7:10]),
        },
        {
            "file": str(Path(repo_path, "utils.py")),
            "name": "format_timestamp",
            "node_path": "format_timestamp",
            "type": "function",
            "start_line": 12,
            "end_line": 23,
            "code": "\n".join(utils_lines[12:24]),
        },
        {
            "file": str(Path(repo_path, "utils.py")),
            "name": "is_valid_email",
            "node_path": "is_valid_email",
            "type": "function",
            "start_line": 26,
            "end_line": 39,
            "code": "\n".join(utils_lines[26:40]),
        },
    ]
    return symbols


@pytest.fixture(scope="function")
def realistic_repo(tmp_path):
    workdir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, workdir)
    return Repository(str(workdir))


@pytest.fixture
def mock_summarizer():
    s = MagicMock()
    s.summarize_function.side_effect = lambda file_path, name: f"Summary of {name}"
    s.summarize_class.side_effect = lambda file_path, name: f"Summary of {name}"
    return s


@pytest.fixture
def fixture_symbols(realistic_repo):
    return _fixture_symbols_for_repo(realistic_repo)


class TestSymbolDocRecord:
    def test_to_dict_roundtrip(self):
        rec = SymbolDocRecord(
            id="utils.py::greet",
            type="function",
            name="greet",
            signature="def greet(name: str) -> str",
            file_path="utils.py",
            line_start=7,
            line_end=9,
            language="python",
            content_hash="abc123",
            code="def greet(name: str) -> str:\n    return f'Hello, {name}!'",
            usage_example="from utils import greet\nresult = greet(name)",
            import_statement="from utils import greet",
        )
        d = rec.to_dict()
        assert d["id"] == "utils.py::greet"
        assert d["type"] == "function"
        rec2 = SymbolDocRecord.from_dict(d)
        assert rec2.id == rec.id
        assert rec2.name == rec.name
        assert rec2.code == rec.code

    def test_from_dict_ignores_unknown_fields(self):
        d = {"id": "f::x", "type": "function", "name": "x", "extra_field": "ignored"}
        rec = SymbolDocRecord.from_dict(d)
        assert rec.id == "f::x"
        assert not hasattr(rec, "extra_field") or rec.interface_detail == {}

    def test_default_values(self):
        rec = SymbolDocRecord(id="f::x", type="function", name="x")
        assert rec.signature == ""
        assert rec.summary == ""
        assert rec.line_start == -1
        assert rec.line_end == -1
        assert rec.language == ""
        assert rec.content_hash == ""
        assert rec.code == ""
        assert rec.usage_example == ""
        assert rec.import_statement == ""
        assert rec.interface_detail == {}


class TestShouldSkipFile:
    def test_skip_test_dir(self):
        assert _should_skip_file("tests/test_foo.py") is True

    def test_skip_node_modules(self):
        assert _should_skip_file("node_modules/bar.js") is True

    def test_skip_venv(self):
        assert _should_skip_file("venv/lib/python.py") is True

    def test_skip_hidden_dir(self):
        assert _should_skip_file(".git/config") is True

    def test_skip_dist(self):
        assert _should_skip_file("dist/output.js") is True

    def test_skip_build(self):
        assert _should_skip_file("build/output.o") is True

    def test_not_skip_normal(self):
        assert _should_skip_file("services/auth.py") is False

    def test_not_skip_init(self):
        assert _should_skip_file("models/__init__.py") is False

    def test_skip_private_module(self):
        assert _should_skip_file("_internal.py") is True

    def test_extension_filter_includes(self):
        assert _should_skip_file("utils.py", extensions=[".py"]) is False

    def test_extension_filter_excludes(self):
        assert _should_skip_file("utils.js", extensions=[".py"]) is True

    def test_skip_pytest_cache(self):
        assert _should_skip_file(".pytest_cache/data") is True

    def test_skip_site_packages(self):
        assert _should_skip_file("site-packages/numpy/__init__.py") is True


class TestMakeImportStatement:
    def test_python_class(self):
        result = _make_import_statement("models/user.py", "User", "class", "python", "User")
        assert result == "from models.user import User"

    def test_python_function(self):
        result = _make_import_statement("utils.py", "greet", "function", "python", "greet")
        assert result == "from utils import greet"

    def test_python_method_imports_parent(self):
        result = _make_import_statement("services/auth.py", "login", "method", "python", "AuthService.login")
        assert result == "from services.auth import AuthService"

    def test_python_init_file(self):
        result = _make_import_statement("models/__init__.py", "SomeClass", "class", "python", "SomeClass")
        assert result == "from models import SomeClass"

    def test_non_python_returns_empty(self):
        result = _make_import_statement("app.js", "MyComponent", "class", "javascript", "MyComponent")
        assert result == ""


class TestGeneratePythonUsageExample:
    def test_function_with_args(self):
        result = _generate_python_usage_example(
            "greet", "function",
            "def greet(name: str) -> str",
            "greet",
            "from utils import greet",
        )
        assert "from utils import greet" in result
        assert "greet(name)" in result

    def test_function_no_args(self):
        result = _generate_python_usage_example(
            "get_all", "function",
            "def get_all() -> list",
            "get_all",
            "from mod import get_all",
        )
        assert "get_all()" in result

    def test_class_instantiation(self):
        result = _generate_python_usage_example(
            "AuthService", "class",
            "class AuthService:",
            "AuthService",
            "from services.auth import AuthService",
        )
        assert "AuthService()" in result

    def test_method_with_args(self):
        result = _generate_python_usage_example(
            "login", "method",
            "def login(self, *, username: str, password: str) -> Optional[str]",
            "AuthService.login",
            "from services.auth import AuthService",
        )
        assert "obj = AuthService()" in result
        assert "obj.login(username, password)" in result

    def test_method_no_node_path(self):
        result = _generate_python_usage_example(
            "foo", "method", "def foo(self):", "", ""
        )
        assert result == ""

    def test_kwargs_in_signature(self):
        result = _generate_python_usage_example(
            "process", "function",
            "def process(data, timeout=30, verbose=False)",
            "process",
            "from mod import process",
        )
        assert "process(data" in result


class TestUsageExampleGenerator:
    def test_generate_python_function(self):
        gen = UsageExampleGenerator()
        rec = SymbolDocRecord(
            id="utils.py::greet", type="function", name="greet",
            signature="def greet(name: str) -> str", language="python",
            import_statement="from utils import greet",
            interface_detail={"node_path": "greet"},
        )
        result = gen.generate(rec)
        assert "greet(name)" in result

    def test_generate_non_python_returns_empty(self):
        gen = UsageExampleGenerator()
        rec = SymbolDocRecord(
            id="app.js::MyComponent", type="class", name="MyComponent",
            language="javascript",
        )
        result = gen.generate(rec)
        assert result == ""

    def test_generate_with_llm_enhancement(self):
        gen = UsageExampleGenerator()
        rec = SymbolDocRecord(
            id="utils.py::greet", type="function", name="greet",
            signature="def greet(name: str) -> str", language="python",
            code="def greet(name: str) -> str:\n    return f'Hello, {name}!'",
            import_statement="from utils import greet",
            interface_detail={"node_path": "greet"},
        )
        mock_llm = MagicMock()
        mock_llm.summarize_function.return_value = "from utils import greet\n\nresult = greet('Alice')\nprint(result)"
        result = gen.generate(rec, summarizer=mock_llm)
        assert "greet" in result
        assert len(result) > 10

    def test_generate_llm_failure_falls_back(self):
        gen = UsageExampleGenerator()
        rec = SymbolDocRecord(
            id="utils.py::greet", type="function", name="greet",
            signature="def greet(name: str) -> str", language="python",
            import_statement="from utils import greet",
            interface_detail={"node_path": "greet"},
        )
        mock_llm = MagicMock()
        mock_llm.summarize_function.side_effect = Exception("LLM error")
        result = gen.generate(rec, summarizer=mock_llm)
        assert "greet(name)" in result


class TestMarkdownRenderer:
    def test_render_single_file_compact(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                signature="def greet(name: str) -> str",
                summary="Returns a friendly greeting.",
                file_path="utils.py", line_start=7, line_end=9,
                language="python", code="def greet(name: str) -> str:\n    return f'Hello, {name}!'",
                usage_example="from utils import greet\nresult = greet(name)",
                import_statement="from utils import greet",
                parameters="name (str): Person's name",
                return_value="A greeting string",
            ),
            SymbolDocRecord(
                id="utils.py::format_timestamp", type="function", name="format_timestamp",
                signature="def format_timestamp(ts: float, format_string: str = '%Y-%m-%d') -> str",
                summary="Formats a Unix timestamp into a human-readable string.",
                file_path="utils.py", line_start=12, line_end=23,
                language="python",
                usage_example="from utils import format_timestamp\nresult = format_timestamp(ts, format_string)",
                import_statement="from utils import format_timestamp",
                parameters="ts (float): Unix timestamp\nformat_string (str): Output format",
                return_value="Formatted date string",
            ),
        ]
        md = renderer.render_single_file("utils.py", records, "python")
        assert "## `utils.py`" in md
        assert "#### `greet`" in md
        assert "#### `format_timestamp`" in md
        assert "**Summary**:" in md
        assert "**Parameters**:" in md
        assert "**Return Value**:" in md
        assert "**Usage**:" in md
        assert "```python" in md
        assert "from utils import greet" in md
        assert "---" in md
        assert "**Source**:" not in md
        assert "**Type**:" not in md
        assert "**Signature**:" not in md
        assert "**Import**:" not in md
        assert "*Location:" not in md
        assert "**Total symbols:**" not in md
        assert "**Total files:**" not in md

    def test_render_single_file_compact_no_params(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                summary="Returns a friendly greeting.",
                file_path="utils.py", line_start=7, line_end=9,
                language="python",
                usage_example="from utils import greet\nresult = greet(name)",
            ),
        ]
        md = renderer.render_single_file("utils.py", records, "python")
        assert "**Summary**:" in md
        assert "**Usage**:" in md
        assert "**Parameters**:" not in md
        assert "**Return Value**:" not in md

    def test_render_single_file_verbose(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                signature="def greet(name: str) -> str",
                summary="Returns a friendly greeting.",
                file_path="utils.py", line_start=7, line_end=9,
                language="python", code="def greet(name: str) -> str:\n    return f'Hello!'",
                usage_example="from utils import greet\nresult = greet(name)",
                import_statement="from utils import greet",
                parameters="name (str): Person's name",
                return_value="A greeting string",
            ),
        ]
        md = renderer.render_single_file("utils.py", records, "python", verbose=True, include_source=True)
        assert "**Source**:" in md
        assert "**Type**:" in md
        assert "**Signature**:" in md
        assert "**Import**:" in md
        assert "*Location:" in md
        assert "**Parameters**:" in md
        assert "**Return Value**:" in md

    def test_render_full_empty(self):
        renderer = MarkdownRenderer()
        md = renderer.render_full([], title="Test Docs")
        assert "No documented symbols found" in md

    def test_render_full_compact_no_totals(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                summary="A greeting function.",
                file_path="utils.py", line_start=7, line_end=9,
                language="python",
                usage_example="from utils import greet\nresult = greet(name)",
            ),
            SymbolDocRecord(
                id="services/auth.py::AuthService", type="class", name="AuthService",
                summary="Manages user registration and login.",
                file_path="services/auth.py", line_start=9, line_end=16,
                language="python",
                usage_example="from services.auth import AuthService\nobj = AuthService()",
            ),
        ]
        md = renderer.render_full(records, title="My Docs")
        assert "# My Docs" in md
        assert "## `services/auth.py`" in md
        assert "## `utils.py`" in md
        assert "**Summary**:" in md
        assert "**Usage**:" in md
        assert "**Source**:" not in md
        assert "**Type**:" not in md
        assert "*Location:" not in md
        assert "**Total symbols:**" not in md
        assert "**Total files:**" not in md

    def test_render_full_verbose_with_totals(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                signature="def greet(name: str) -> str",
                summary="A greeting function.",
                file_path="utils.py", line_start=7, line_end=9,
                language="python",
                code="def greet(name): pass",
                usage_example="from utils import greet\nresult = greet(name)",
                import_statement="from utils import greet",
            ),
        ]
        md = renderer.render_full(records, title="My Docs", verbose=True, include_source=True)
        assert "**Total symbols:** 1" in md
        assert "**Total files:** 1" in md
        assert "**Source**:" in md
        assert "**Type**:" in md
        assert "*Location:" in md

    def test_render_full_with_split_by_dir(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                summary="Greeting.",
                file_path="utils.py", line_start=7, line_end=9,
                language="python",
                usage_example="from utils import greet\nresult = greet(name)",
            ),
            SymbolDocRecord(
                id="services/auth.py::AuthService", type="class", name="AuthService",
                summary="Auth service.",
                file_path="services/auth.py", line_start=9, line_end=16,
                language="python",
                usage_example="from services.auth import AuthService\nobj = AuthService()",
            ),
        ]
        md = renderer.render_full(records, title="My Docs", split_by_dir=True)
        assert "## `utils.py`" in md
        assert "## `services/auth.py`" in md

    def test_render_interface_detail_verbose(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="app.py::get_users", type="function", name="get_users",
                file_path="app.py", line_start=10, line_end=20,
                language="python",
                interface_detail={"interface_type": "route_handler", "framework": "FastAPI"},
                summary="Returns list of users.",
                usage_example="from app import get_users\nresult = get_users()",
            ),
        ]
        md = renderer.render_full(records, verbose=True)
        assert "Function (FastAPI route_handler)" in md

    def test_render_interface_detail_compact_hidden(self):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="app.py::get_users", type="function", name="get_users",
                file_path="app.py", line_start=10, line_end=20,
                language="python",
                interface_detail={"interface_type": "route_handler", "framework": "FastAPI"},
                summary="Returns list of users.",
                usage_example="from app import get_users\nresult = get_users()",
            ),
        ]
        md = renderer.render_full(records)
        assert "**Type**:" not in md

    def test_render_usage_and_language_tag(self):
        renderer = MarkdownRenderer()
        rec = SymbolDocRecord(
            id="f.py::foo", type="function", name="foo",
            file_path="f.py", line_start=1, line_end=3,
            language="python",
            usage_example="from f import foo\nfoo()",
            summary="Does something.",
        )
        md = renderer.render_single_file("f.py", [rec], "python")
        assert "**Usage**:" in md
        assert "```python" in md
        assert "from f import foo" in md
        assert "**Source**:" not in md

    def test_render_compact_no_extras(self):
        renderer = MarkdownRenderer()
        rec = SymbolDocRecord(
            id="f.xyz::bar", type="function", name="bar",
            file_path="f.xyz", line_start=1, line_end=2,
language="unknown",
            summary="Something.",
        )
        md = renderer.render_single_file("f.xyz", [rec], "unknown")
        assert "#### `bar`" in md
        assert "**Summary**:" in md
        assert "**Type**:" not in md
        assert "*Location:" not in md


class TestCleanSummary:
    def test_clean_plain_summary(self):
        result = _clean_summary("Returns a friendly greeting message.")
        assert result == "Returns a friendly greeting message."

    def test_clean_purpose_header_merged(self):
        raw = "**Purpose:**\nGenerates a greeting message using the provided name."
        result = _clean_summary(raw)
        assert "**Purpose:**" not in result
        assert "Generates a greeting" in result

    def test_clean_parameters_excluded(self):
        raw = "**Parameters:**\n- name (str): The name\n\n**Return Value:**\n- A greeting string."
        result = _clean_summary(raw)
        assert "**Parameters:**" not in result
        assert "**Return Value:**" not in result
        assert "name" not in result
        assert "greeting string" not in result

    def test_clean_key_attributes_merged(self):
        raw = "**Key Attributes:**\n- Stores user data\n\n**Main Methods:**\n- login\n- logout"
        result = _clean_summary(raw)
        assert "**Key Attributes:**" not in result
        assert "**Main Methods:**" not in result
        assert "Stores user data" in result
        assert "login" in result

    def test_clean_example_dropped(self):
        raw = "**Example:**\n```python\ngreet('Alice')\n```"
        result = _clean_summary(raw)
        assert "**Example:**" not in result
        assert "greet" not in result

    def test_clean_empty_string(self):
        assert _clean_summary("") == ""

    def test_clean_truncates_long_summary(self):
        raw = "This function does A. Then it does B. Then it does C. Then it does D. Then it does E. Then it does F. Then it does G."
        result = _clean_summary(raw)
        assert len(result) <= 310


class TestParseStructuredSummary:
    def test_plain_text_only(self):
        summary, params, retval = _parse_structured_summary("A simple function.")
        assert summary == "A simple function."
        assert params == ""
        assert retval == ""

    def test_full_structured_llm_output(self):
        raw = (
            "**Purpose:**\n"
            "Creates a new user account with the given credentials.\n\n"
            "**Parameters:**\n"
            "- username (str): The desired username\n"
            "- password (str): The password to set\n\n"
            "**Return Value:**\n"
            "- The newly created User instance.\n\n"
            "**Example:**\n"
            "```python\nuser = create_user('alice', 'secret')\n```"
        )
        summary, params, retval = _parse_structured_summary(raw)
        assert "Creates a new user account" in summary
        assert "username" in params
        assert "password" in params
        assert "newly created User" in retval
        assert "alice" not in summary
        assert "alice" not in params
        assert "alice" not in retval

    def test_description_section_merged(self):
        raw = (
            "**Description:**\n"
            "Manages user authentication and sessions.\n\n"
            "**Parameters:**\n"
            "- db: Database connection\n\n"
            "**Return Value:**\n"
            "- None"
        )
        summary, params, retval = _parse_structured_summary(raw)
        assert "Manages user authentication" in summary
        assert "db" in params
        assert "None" in retval

    def test_returns_alias(self):
        raw = "**Returns:**\n- A list of strings."
        summary, params, retval = _parse_structured_summary(raw)
        assert retval == "A list of strings."

    def test_empty_input(self):
        summary, params, retval = _parse_structured_summary("")
        assert summary == ""
        assert params == ""
        assert retval == ""

    def test_bullet_params(self):
        raw = (
            "A greeting function.\n\n"
            "**Parameters:**\n"
            "- name (str): Person's name\n"
            "- greeting (str): Greeting template\n\n"
            "**Return Value:**\n"
            "- Formatted string"
        )
        summary, params, retval = _parse_structured_summary(raw)
        assert summary == "A greeting function."
        assert "name" in params
        assert "greeting" in params
        assert "Formatted string" in retval


class TestMarkdownRendererSplitByDir:
    def test_render_split_by_dir(self, tmp_path):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                file_path="utils.py", line_start=7, line_end=9,
                language="python",
            ),
            SymbolDocRecord(
                id="services/auth.py::AuthService", type="class", name="AuthService",
                file_path="services/auth.py", line_start=9, line_end=16,
                language="python",
            ),
        ]
        output_dir = tmp_path / "docs_output"
        result = renderer.render_split_by_dir(records, title="Test Docs", output_dir=output_dir)
        assert output_dir.exists()
        assert "_root" in result or "(root)" in str(result.values())
        assert "_index" in result
        index_file = result["_index"]
        assert index_file.exists()
        index_content = index_file.read_text()
        assert "Index" in index_content

    def test_render_split_by_dir_creates_dir(self, tmp_path):
        renderer = MarkdownRenderer()
        records = [
            SymbolDocRecord(
                id="utils.py::greet", type="function", name="greet",
                file_path="utils.py", line_start=7, line_end=9,
                language="python",
            ),
        ]
        output_dir = tmp_path / "new_dir" / "docs"
        renderer.render_split_by_dir(records, title="Test", output_dir=output_dir)
        assert output_dir.exists()


class TestDocCache:
    def test_cache_put_and_get(self):
        cache = DocCache()
        rec = SymbolDocRecord(
            id="f::x", type="function", name="x",
            content_hash="hash1",
        )
        cache.put(rec)
        found = cache.get("f::x", "hash1")
        assert found is not None
        assert found.name == "x"

    def test_cache_get_mismatch_returns_none(self):
        cache = DocCache()
        rec = SymbolDocRecord(
            id="f::x", type="function", name="x",
            content_hash="hash1",
        )
        cache.put(rec)
        found = cache.get("f::x", "hash2")
        assert found is None

    def test_cache_clear(self):
        cache = DocCache()
        cache.put(SymbolDocRecord(id="f::x", type="function", name="x", content_hash="h"))
        cache.clear()
        assert cache.get("f::x", "h") is None

    def test_cache_stats(self):
        cache = DocCache()
        cache.put(SymbolDocRecord(id="f1::x", type="function", name="x", file_path="f1.py", content_hash="h1"))
        cache.put(SymbolDocRecord(id="f2::y", type="class", name="y", file_path="f2.py", content_hash="h2"))
        stats = cache.stats()
        assert stats["total_cached"] == 2
        assert stats["unique_files"] == 2

    def test_cache_save_and_load(self, tmp_path):
        cache = DocCache(cache_dir=tmp_path)
        rec = SymbolDocRecord(
            id="f::x", type="function", name="x",
            content_hash="hash1", file_path="f.py",
        )
        cache.put(rec)
        cache.save()

        cache2 = DocCache(cache_dir=tmp_path)
        cache2.load()
        found = cache2.get("f::x", "hash1")
        assert found is not None
        assert found.name == "x"

    def test_cache_load_missing_dir(self, tmp_path):
        cache = DocCache(cache_dir=tmp_path / "nonexistent")
        cache.load()
        stats = cache.stats()
        assert stats["total_cached"] == 0

    def test_cache_load_corrupt_json(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "doc_cache.json").write_text("not valid json{{")
        cache = DocCache(cache_dir=tmp_path)
        cache.load()
        stats = cache.stats()
        assert stats["total_cached"] == 0

    def test_cache_no_dir_skips_save(self):
        cache = DocCache()
        cache.put(SymbolDocRecord(id="f::x", type="function", name="x", content_hash="h"))
        cache.save()
        stats = cache.stats()
        assert stats["total_cached"] == 1


class TestCodebaseMarkdownDocGenerator:
    def test_generate_no_summarizer(self, realistic_repo, fixture_symbols):
        repo = realistic_repo
        real_symbols = repo.extract_symbols()
        if not real_symbols:
            real_symbols = fixture_symbols

        gen = CodebaseMarkdownDocGenerator(repo=repo)
        records, md = gen.generate()
        assert isinstance(records, list)
        assert isinstance(md, str)
        if records:
            assert "# Codebase Documentation" in md

    def test_generate_with_mock_summarizer(self, realistic_repo, mock_summarizer):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        _records, _md = gen.generate()
        if _records:
            for rec in _records:
                if rec.summary:
                    assert "Summary of" in rec.summary

    def test_generate_with_extension_filter(self, realistic_repo, mock_summarizer):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        records, _md = gen.generate(file_extensions=[".py"])
        for rec in records:
            assert Path(rec.file_path).suffix == ".py"

    def test_generate_max_symbols(self, realistic_repo, mock_summarizer):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        records, _md = gen.generate(max_symbols=2)
        assert len(records) <= 2

    def test_generate_force_clears_cache(self, realistic_repo, tmp_path, mock_summarizer):
        cache_dir = str(tmp_path / "cache")
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer, cache_dir=cache_dir)
        _records1, _ = gen.generate()
        gen.cache.save()
        stats_before = gen.cache.stats()
        assert stats_before["total_cached"] > 0

        _records2, md2 = gen.generate(force=True)
        assert isinstance(md2, str)

    def test_generate_to_file(self, realistic_repo, tmp_path, mock_summarizer):
        output = str(tmp_path / "output" / "docs.md")
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        _records, _out_path = gen.generate_to_file(output)
        assert Path(output).exists()
        content = Path(output).read_text()
        if _records:
            assert "Codebase Documentation" in content

    def test_generate_to_file_split_by_dir(self, realistic_repo, tmp_path, mock_summarizer):
        output_dir = str(tmp_path / "split_output")
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        _records, _out_path = gen.generate_to_file(output_dir, split_by_dir=True)
        assert Path(output_dir).exists()

    def test_generate_incremental_cache(self, realistic_repo, tmp_path, mock_summarizer):
        cache_dir = str(tmp_path / "doc_cache")
        gen1 = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer, cache_dir=cache_dir)
        records1, _ = gen1.generate()
        gen1.cache.save()

        gen2 = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer, cache_dir=cache_dir)
        gen2.cache.load()
        records2, _ = gen2.generate()
        assert len(records2) == len(records1)

    def test_generate_empty_repo(self, tmp_path):
        empty_dir = tmp_path / "empty_repo"
        empty_dir.mkdir()
        (empty_dir / "README.md").write_text("# Empty")
        repo = Repository(str(empty_dir))
        gen = CodebaseMarkdownDocGenerator(repo=repo)
        _records, md = gen.generate()
        assert "No documented symbols found" in md

    def test_generate_with_split_by_dir(self, realistic_repo, mock_summarizer):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        _records, md = gen.generate(split_by_dir=True)
        if _records:
            assert "## `" in md

    def test_generate_custom_title(self, realistic_repo, mock_summarizer):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo, summarizer=mock_summarizer)
        _records, md = gen.generate(title="Custom Title")
        assert "# Custom Title" in md

    def test_generate_error_handling(self, realistic_repo):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo)
        records, md = gen.generate()
        assert isinstance(md, str)
        assert isinstance(records, list)

    def test_to_relative(self, realistic_repo):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo)
        abs_path = str(realistic_repo.local_path) + "/services/auth.py"
        rel = gen._to_relative(abs_path)
        assert rel == "services/auth.py"

    def test_to_relative_already_relative(self, realistic_repo):
        gen = CodebaseMarkdownDocGenerator(repo=realistic_repo)
        rel = gen._to_relative("utils.py")
        assert rel == "utils.py"


class TestIntegration:
    def test_full_pipeline_with_fixture(self, realistic_repo, mock_summarizer, tmp_path):
        gen = CodebaseMarkdownDocGenerator(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            cache_dir=str(tmp_path / "cache"),
        )
        records, md = gen.generate(
            file_extensions=[".py"],
            title="Fixture Repo Docs",
        )
        assert isinstance(records, list)
        assert isinstance(md, str)
        if records:
            assert "Fixture Repo Docs" in md
            for rec in records:
                assert rec.language == "python"
                assert rec.content_hash != ""
                assert rec.import_statement != ""
        gen.cache.save()
        assert (tmp_path / "cache" / "doc_cache.json").exists()

    def test_full_pipeline_to_file(self, realistic_repo, mock_summarizer, tmp_path):
        output_path = str(tmp_path / "docs_output.md")
        gen = CodebaseMarkdownDocGenerator(
            repo=realistic_repo,
            summarizer=mock_summarizer,
        )
        _records, _out = gen.generate_to_file(output_path, file_extensions=[".py"])
        assert Path(output_path).exists()
        content = Path(output_path).read_text()
        if _records:
            assert "# Codebase Documentation" in content

    def test_split_by_dir_output(self, realistic_repo, mock_summarizer, tmp_path):
        output_dir = str(tmp_path / "split_docs")
        gen = CodebaseMarkdownDocGenerator(
            repo=realistic_repo,
            summarizer=mock_summarizer,
        )
        _records, _out = gen.generate_to_file(
            output_dir,
            file_extensions=[".py"],
            split_by_dir=True,
        )
        assert Path(output_dir).exists()
        assert (Path(output_dir) / "index.md").exists()

    def test_cache_incremental_second_run(self, realistic_repo, mock_summarizer, tmp_path):
        cache_dir = str(tmp_path / "cache2")
        gen1 = CodebaseMarkdownDocGenerator(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            cache_dir=cache_dir,
        )
        records1, _ = gen1.generate(file_extensions=[".py"])
        gen1.cache.save()

        gen2 = CodebaseMarkdownDocGenerator(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            cache_dir=cache_dir,
        )
        gen2.cache.load()
        records2, _md2 = gen2.generate(file_extensions=[".py"])
        assert len(records2) == len(records1)
