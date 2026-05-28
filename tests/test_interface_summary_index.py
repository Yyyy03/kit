"""Tests for InterfaceSummaryIndexer, InterfaceSummarySearcher, and source locator.

Real-path tests use repo.extract_symbols() directly (no symbols= bypass).
Legacy tests still use symbols= for controlled fixture data comparison.
Structured error tests verify clear error messages for invalid inputs.
"""

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kit import Repository
from kit.interface_summary_index import (
    InterfaceSummaryError,
    InterfaceSummaryIndexer,
    InterfaceSummaryRecord,
    InterfaceSummarySearcher,
    SourceReadError,
    SourceSnippetResult,
    _compute_content_hash,
    _detect_language,
    _extract_signature,
    _make_record_id,
    detect_interface_type,
    get_source_snippet,
    get_source_snippet_structured,
    open_source_for_summary,
    open_source_for_summary_structured,
)
from kit.vector_searcher import VectorDBBackend

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "realistic_repo"


def _fixture_symbols_for_repo(repo):
    """Return a pre-extracted symbol list matching the realistic fixture repo.

    This simulates what repo.extract_symbols() would return on a working
    tree-sitter environment, so tests are not dependent on tree-sitter
    API compatibility.
    """
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
            "start_line": 8,
            "end_line": 17,
            "code": "\n".join(auth_lines[8:18]),
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
            "file": str(Path(repo_path, "services", "auth.py")),
            "name": "logout",
            "node_path": "AuthService.logout",
            "type": "method",
            "start_line": 60,
            "end_line": 63,
            "code": "\n".join(auth_lines[60:64]),
        },
        {
            "file": str(Path(repo_path, "services", "auth.py")),
            "name": "is_valid_token",
            "node_path": "AuthService.is_valid_token",
            "type": "method",
            "start_line": 65,
            "end_line": 67,
            "code": "\n".join(auth_lines[65:68]),
        },
        {
            "file": str(Path(repo_path, "services", "auth.py")),
            "name": "get_user_from_token",
            "node_path": "AuthService.get_user_from_token",
            "type": "method",
            "start_line": 69,
            "end_line": 81,
            "code": "\n".join(auth_lines[69:82]),
        },
        {
            "file": str(Path(repo_path, "models", "user.py")),
            "name": "User",
            "node_path": "User",
            "type": "class",
            "start_line": 10,
            "end_line": 17,
            "code": "\n".join(user_lines[10:18]),
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
            "file": str(Path(repo_path, "models", "user.py")),
            "name": "update_email",
            "node_path": "User.update_email",
            "type": "method",
            "start_line": 25,
            "end_line": 37,
            "code": "\n".join(user_lines[25:38]),
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


class DummyBackend(VectorDBBackend):
    """Minimal in-memory backend with delete support for tests."""

    def __init__(self):
        self.embeddings = []
        self.metadatas = []
        self.ids = []

    def add(self, embeddings, metadatas, ids=None):
        self.embeddings.extend(embeddings)
        self.metadatas.extend(metadatas)
        self.ids.extend(ids or [str(i) for i in range(len(metadatas))])

    def query(self, embedding, top_k):
        return self.metadatas[:top_k]

    def persist(self):
        pass

    def count(self):
        return len(self.metadatas)

    def delete(self, ids):
        for _id in ids:
            if _id in self.ids:
                idx = self.ids.index(_id)
                self.ids.pop(idx)
                self.embeddings.pop(idx)
                self.metadatas.pop(idx)


@pytest.fixture(scope="function")
def realistic_repo(tmp_path):
    workdir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, workdir)
    return Repository(str(workdir))


@pytest.fixture
def mock_summarizer():
    summarizer = MagicMock()
    summarizer.summarize_function.side_effect = lambda p, s: f"Summary of function {s} in {p}"
    summarizer.summarize_class.side_effect = lambda p, s: f"Summary of class {s} in {p}"
    return summarizer


@pytest.fixture
def mock_embed_fn():
    def embed_fn(text):
        return [float(len(text))]
    return embed_fn


@pytest.fixture
def fixture_symbols(realistic_repo):
    return _fixture_symbols_for_repo(realistic_repo)


class TestInterfaceSummaryRecord:
    def test_to_dict_roundtrip(self):
        record = InterfaceSummaryRecord(
            id="src/foo.py::my_func",
            type="function",
            name="my_func",
            signature="def my_func(arg1, arg2):",
            summary="A test function",
            file_path="src/foo.py",
            line_start=10,
            line_end=42,
            language="python",
            content_hash="abc123",
            dependencies=["os", "sys"],
            side_effects=["prints output"],
            metadata={"complexity": "low"},
        )
        d = record.to_dict()
        assert d["id"] == "src/foo.py::my_func"
        assert d["dependencies"] == ["os", "sys"]
        assert d["side_effects"] == ["prints output"]

        restored = InterfaceSummaryRecord.from_dict(d)
        assert restored.id == record.id
        assert restored.dependencies == record.dependencies
        assert restored.side_effects == record.side_effects

    def test_to_index_metadata(self):
        record = InterfaceSummaryRecord(
            id="a.py::foo",
            type="function",
            name="foo",
            file_path="a.py",
            line_start=5,
            line_end=10,
        )
        meta = record.to_index_metadata()
        assert meta["level"] == "interface_summary"
        assert meta["id"] == "a.py::foo"

    def test_default_empty_fields(self):
        record = InterfaceSummaryRecord(
            id="b.py::bar",
            type="class",
            name="bar",
        )
        assert record.dependencies == []
        assert record.side_effects == []
        assert record.metadata == {}
        assert record.signature == ""
        assert record.summary == ""

    def test_route_handler_metadata(self):
        record = InterfaceSummaryRecord(
            id="app.py::get_user",
            type="route_handler",
            name="get_user",
            metadata={
                "framework": "FastAPI",
                "http_method": "GET",
                "route_path": "/users/{id}",
            },
        )
        d = record.to_dict()
        assert d["type"] == "route_handler"
        assert d["metadata"]["framework"] == "FastAPI"
        assert d["metadata"]["http_method"] == "GET"
        assert d["metadata"]["route_path"] == "/users/{id}"


class TestHelpers:
    def test_detect_language(self):
        assert _detect_language("foo.py") == "python"
        assert _detect_language("foo.js") == "javascript"
        assert _detect_language("foo.go") == "go"
        assert _detect_language("foo.unknown") == "unknown"

    def test_extract_signature(self):
        code = "def my_func(arg1, arg2):\n    return arg1 + arg2"
        assert _extract_signature(code, "function") == "def my_func(arg1, arg2):"

    def test_extract_signature_empty(self):
        assert _extract_signature("", "function") == ""

    def test_extract_signature_long(self):
        very_long_line = "def func(" + ", ".join([f"arg{i}" for i in range(50)]) + "):"
        sig = _extract_signature(very_long_line, "function")
        assert len(sig) <= 203

    def test_make_record_id(self):
        assert _make_record_id("src/foo.py", "my_func") == "src/foo.py::my_func"

    def test_compute_content_hash(self):
        assert _compute_content_hash("") == ""
        h1 = _compute_content_hash("hello")
        h2 = _compute_content_hash("hello")
        assert h1 == h2
        h3 = _compute_content_hash("world")
        assert h1 != h3

    def test_compute_content_hash_length(self):
        h = _compute_content_hash("some code")
        assert len(h) == 40


class TestDetectInterfaceType:
    def test_basic_function_no_decorator(self):
        symbol = {"type": "function", "name": "plain_func", "start_line": 5, "code": "def plain_func():\n    pass"}
        result_type, metadata = detect_interface_type(symbol, "def plain_func():\n    pass", "app.py")
        assert result_type == "function"
        assert metadata == {}

    def test_fastapi_route_handler(self):
        source = "@app.get('/users/{id}')\ndef get_user(id: str):\n    pass\n"
        symbol = {"type": "function", "name": "get_user", "start_line": 1, "code": "def get_user(id: str):\n    pass"}
        result_type, metadata = detect_interface_type(symbol, source, "app.py")
        assert result_type == "route_handler"
        assert metadata["framework"] == "FastAPI"
        assert metadata["http_method"] == "GET"
        assert metadata["route_path"] == "/users/{id}"

    def test_fastapi_post_handler(self):
        source = "@app.post('/items')\ndef create_item():\n    pass\n"
        symbol = {"type": "function", "name": "create_item", "start_line": 1, "code": "def create_item():\n    pass"}
        result_type, metadata = detect_interface_type(symbol, source, "app.py")
        assert result_type == "route_handler"
        assert metadata["framework"] == "FastAPI"
        assert metadata["http_method"] == "POST"
        assert metadata["route_path"] == "/items"

    def test_flask_route_handler(self):
        source = "@app.route('/api/data', methods=['GET', 'POST'])\ndef handle_data():\n    pass\n"
        symbol = {"type": "function", "name": "handle_data", "start_line": 1, "code": "def handle_data():\n    pass"}
        result_type, metadata = detect_interface_type(symbol, source, "app.py")
        assert result_type == "route_handler"
        assert metadata["framework"] == "Flask"
        assert metadata["http_method"] == "GET"
        assert metadata["route_path"] == "/api/data"

    def test_class_type_unchanged(self):
        symbol = {"type": "class", "name": "MyClass", "start_line": 0, "code": "class MyClass:\n    pass"}
        result_type, metadata = detect_interface_type(symbol, "class MyClass:\n    pass", "app.py")
        assert result_type == "class"
        assert metadata == {}

    def test_non_python_language_skipped(self):
        symbol = {"type": "function", "name": "handler", "start_line": 0, "code": "func handler() {}"}
        result_type, metadata = detect_interface_type(symbol, "func handler() {}", "main.go")
        assert result_type == "function"
        assert metadata == {}

    def test_decorator_on_previous_line(self):
        source = "\n\n@app.get('/health')\ndef health_check():\n    return {'status': 'ok'}\n"
        symbol = {"type": "function", "name": "health_check", "start_line": 3, "code": "def health_check():\n    return {'status': 'ok'}"}
        result_type, metadata = detect_interface_type(symbol, source, "app.py")
        assert result_type == "route_handler"
        assert metadata["framework"] == "FastAPI"


class TestInterfaceSummaryIndexer:
    def test_build_extracts_supported_symbols(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True, symbols=fixture_symbols)

        function_records = [r for r in records if r.type == "function"]
        method_records = [r for r in records if r.type == "method"]
        class_records = [r for r in records if r.type == "class"]

        assert len(function_records) > 0, "Should extract at least one function"
        assert len(class_records) > 0, "Should extract at least one class"
        assert len(method_records) > 0, "Should extract at least one method"

    def test_build_generates_summary_records(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True, symbols=fixture_symbols)

        for r in records:
            assert r.id
            assert r.name
            assert r.type in ("function", "method", "class")
            assert r.file_path
            assert r.language == "python"
            assert r.content_hash
            assert r.dependencies == []
            assert r.side_effects == []

    def test_build_with_file_extension_filter(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True, file_extensions=[".py"], symbols=fixture_symbols)

        for r in records:
            assert r.file_path.endswith(".py")

    def test_incremental_caching(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        persist_dir = os.path.join(realistic_repo.repo_path, ".kit_cache", "test_interface_summary")
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
            persist_dir=persist_dir,
        )

        records_first = indexer.build(force=True, symbols=fixture_symbols)
        assert len(records_first) > 0

        mock_summarizer.summarize_function.reset_mock()
        mock_summarizer.summarize_class.reset_mock()

        records_second = indexer.build(force=False, symbols=fixture_symbols)
        assert len(records_second) == len(records_first)

    def test_force_rebuild_clears_old_data(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        persist_dir = os.path.join(realistic_repo.repo_path, ".kit_cache", "test_force_rebuild")
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
            persist_dir=persist_dir,
        )

        records_first = indexer.build(force=True, symbols=fixture_symbols)
        assert len(records_first) > 0
        assert len(backend.ids) > 0

        records_second = indexer.build(force=True, symbols=fixture_symbols)
        assert len(records_second) == len(records_first)

    def test_get_record_and_get_all_records(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        records = indexer.build(force=True, symbols=fixture_symbols)

        all_records = indexer.get_all_records()
        assert len(all_records) == len(records)

        if records:
            first = records[0]
            retrieved = indexer.get_record(first.id)
            assert retrieved is not None
            assert retrieved.name == first.name

    def test_get_searcher_returns_searcher(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        searcher = indexer.get_searcher()
        assert isinstance(searcher, InterfaceSummarySearcher)
        assert searcher.embed_fn is not None

    def test_get_stats(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True, symbols=fixture_symbols)

        stats = indexer.get_stats()
        assert stats["total_records"] > 0
        assert "type_counts" in stats
        assert "function" in stats["type_counts"] or "method" in stats["type_counts"] or "class" in stats["type_counts"]

    def test_route_handler_detection_in_build(self, realistic_repo, mock_summarizer, mock_embed_fn):
        app_py_path = Path(realistic_repo.repo_path) / "app_with_routes.py"
        app_py_path.write_text(
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n"
            "@app.get('/users/{id}')\ndef get_user(id: str):\n    return {'id': id}\n\n"
            "@app.post('/items')\ndef create_item():\n    return {'created': True}\n\n"
            "def helper():\n    pass\n"
        )

        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
            detect_route_handlers=True,
        )

        records = indexer.build(force=True)

        route_handlers = [r for r in records if r.type == "route_handler"]
        functions = [r for r in records if r.type == "function" and r.name == "helper"]

        assert len(route_handlers) >= 2
        for rh in route_handlers:
            assert rh.metadata.get("framework") == "FastAPI"
            assert rh.metadata.get("http_method") in ("GET", "POST")
            assert rh.metadata.get("route_path") is not None

        assert len(functions) >= 1


class TestInterfaceSummarySearcher:
    def test_search_returns_results(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True, symbols=fixture_symbols)

        searcher = indexer.get_searcher()
        results = searcher.search("authentication", top_k=5)

        assert len(results) > 0
        for r in results:
            assert "score" in r
            assert "id" in r
            assert "type" in r
            assert "name" in r
            assert "summary" in r
            assert "file_path" in r
            assert "line_start" in r
            assert "line_end" in r

    def test_search_top_k_zero(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True, symbols=fixture_symbols)

        searcher = indexer.get_searcher()
        results = searcher.search("test", top_k=0)
        assert results == []

    def test_search_returns_metadata(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True, symbols=fixture_symbols)

        searcher = indexer.get_searcher()
        results = searcher.search("authentication", top_k=5)

        for r in results:
            record = indexer.get_record(r["id"])
            if record and record.metadata:
                assert "metadata" in r


class TestSourceLocator:
    def test_get_source_snippet(self, realistic_repo):
        snippet = get_source_snippet(realistic_repo, "services/auth.py", 8, 17)
        assert snippet is not None
        assert "AuthService" in snippet

    def test_get_source_snippet_missing_file(self, realistic_repo):
        snippet = get_source_snippet(realistic_repo, "nonexistent.py", 0, 10)
        assert snippet is None

    def test_get_source_snippet_structured_valid(self, realistic_repo):
        result = get_source_snippet_structured(realistic_repo, "services/auth.py", 8, 17)
        assert result.error is None
        assert result.source != ""
        assert result.file_path == "services/auth.py"
        assert result.line_start == 8
        assert "AuthService" in result.source

    def test_get_source_snippet_structured_missing_file(self, realistic_repo):
        result = get_source_snippet_structured(realistic_repo, "nonexistent.py", 0, 10)
        assert result.error is not None
        assert "not found" in result.error.lower()
        assert result.source == ""

    def test_get_source_snippet_structured_negative_line_start(self, realistic_repo):
        result = get_source_snippet_structured(realistic_repo, "services/auth.py", -1, 10)
        assert result.error is not None
        assert "must be >= 0" in result.error

    def test_get_source_snippet_structured_line_end_before_start(self, realistic_repo):
        result = get_source_snippet_structured(realistic_repo, "services/auth.py", 10, 5)
        assert result.error is not None
        assert "must be >= line_start" in result.error

    def test_get_source_snippet_structured_line_start_exceeds_file(self, realistic_repo):
        result = get_source_snippet_structured(realistic_repo, "utils.py", 9999, 10000)
        assert result.error is not None
        assert "exceeds file length" in result.error

    def test_open_source_for_summary(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True, symbols=fixture_symbols)

        if fixture_symbols:
            first = indexer.get_all_records()[0]
            source = open_source_for_summary(realistic_repo, first.id, indexer._records)
            assert source is not None

    def test_open_source_for_summary_not_found(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True)

        source = open_source_for_summary(realistic_repo, "nonexistent::id", indexer._records)
        assert source is None

    def test_open_source_for_summary_structured_valid(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True, symbols=fixture_symbols)

        if fixture_symbols:
            first = indexer.get_all_records()[0]
            result = open_source_for_summary_structured(realistic_repo, first.id, indexer._records)
            assert result.error is None
            assert result.source != ""
            assert result.file_path == first.file_path

    def test_open_source_for_summary_structured_not_found(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True)

        result = open_source_for_summary_structured(realistic_repo, "nonexistent::id", indexer._records)
        assert result.error is not None
        assert "not found" in result.error.lower() or result.source == ""


class TestEndToEnd:
    def test_index_search_open_source(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True, symbols=fixture_symbols)
        assert len(records) > 0

        searcher = indexer.get_searcher()
        results = searcher.search("login", top_k=5)
        assert len(results) > 0

        hit = results[0]
        source = get_source_snippet(realistic_repo, hit["file_path"], hit["line_start"], hit["line_end"])
        assert source is not None

    def test_index_search_open_source_structured(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True, symbols=fixture_symbols)
        assert len(records) > 0

        searcher = indexer.get_searcher()
        results = searcher.search("login", top_k=5)
        assert len(results) > 0

        hit = results[0]
        source_result = get_source_snippet_structured(realistic_repo, hit["file_path"], hit["line_start"], hit["line_end"])
        assert source_result.error is None
        assert source_result.source != ""

    def test_open_source_for_summary_via_search(self, realistic_repo, mock_summarizer, mock_embed_fn, fixture_symbols):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        indexer.build(force=True, symbols=fixture_symbols)
        searcher = indexer.get_searcher()
        results = searcher.search("auth", top_k=3)

        if results:
            hit_id = results[0]["id"]
            source = open_source_for_summary(realistic_repo, hit_id, indexer._records)
            assert source is not None


class TestRealPath:
    """Tests using repo.extract_symbols() directly -- no symbols= bypass."""

    def test_build_with_real_symbol_extraction(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True)

        assert len(records) > 0, "Real symbol extraction should produce records"
        for r in records:
            assert r.id
            assert r.name
            assert r.type in ("function", "method", "class", "route_handler")
            assert r.file_path
            assert r.language == "python"
            assert r.content_hash

    def test_build_real_extracts_functions_classes_methods(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )

        records = indexer.build(force=True)

        function_records = [r for r in records if r.type == "function"]
        class_records = [r for r in records if r.type == "class"]
        method_records = [r for r in records if r.type == "method"]

        assert len(function_records) > 0
        assert len(class_records) > 0
        assert len(method_records) > 0

    def test_search_with_real_extraction(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
        )
        indexer.build(force=True)

        searcher = indexer.get_searcher()
        results = searcher.search("authentication", top_k=5)

        assert len(results) > 0

    def test_incremental_with_real_extraction(self, realistic_repo, mock_summarizer, mock_embed_fn):
        backend = DummyBackend()
        persist_dir = os.path.join(realistic_repo.repo_path, ".kit_cache", "test_interface_summary_real")
        indexer = InterfaceSummaryIndexer(
            repo=realistic_repo,
            summarizer=mock_summarizer,
            embed_fn=mock_embed_fn,
            backend=backend,
            persist_dir=persist_dir,
        )

        records_first = indexer.build(force=True)
        assert len(records_first) > 0

        mock_summarizer.summarize_function.reset_mock()
        mock_summarizer.summarize_class.reset_mock()

        records_second = indexer.build(force=False)
        assert len(records_second) == len(records_first)


class TestErrorHierarchy:
    def test_error_hierarchy(self):
        assert issubclass(SourceReadError, InterfaceSummaryError)
        assert issubclass(InterfaceSummaryError, Exception)

    def test_source_read_error(self):
        err = SourceReadError("File missing", detail={"file": "foo.py"})
        assert "File missing" in str(err)
        assert err.detail["file"] == "foo.py"

    def test_source_snippet_result_to_dict(self):
        result = SourceSnippetResult(
            file_path="foo.py",
            line_start=5,
            line_end=10,
            source="def foo():\n    pass",
            error=None,
        )
        d = result.to_dict()
        assert d["file_path"] == "foo.py"
        assert d["source"] == "def foo():\n    pass"
        assert d["error"] is None

    def test_source_snippet_result_to_dict_with_error(self):
        result = SourceSnippetResult(
            file_path="missing.py",
            line_start=0,
            line_end=10,
            source="",
            error="File not found",
        )
        d = result.to_dict()
        assert d["source"] == ""
        assert d["error"] == "File not found"
