"""Tests for InterfaceSummaryIndexer, InterfaceSummarySearcher, and source locator.

Note: On Python 3.14 + tree-sitter 0.25.2, the tree-sitter parse API
changed from bytes to str input. The kit project's TreeSitterSymbolExtractor
has not been updated for this yet, so repo.extract_symbols() returns []
on this environment. To make tests portable, we provide pre-extracted
symbol data via the `symbols=` parameter of InterfaceSummaryIndexer.build().
"""

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kit import Repository
from kit.interface_summary_index import (
    InterfaceSummaryIndexer,
    InterfaceSummaryRecord,
    InterfaceSummarySearcher,
    _detect_language,
    _extract_signature,
    _make_record_id,
    get_source_snippet,
    open_source_for_summary,
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


class TestSourceLocator:
    def test_get_source_snippet(self, realistic_repo):
        snippet = get_source_snippet(realistic_repo, "services/auth.py", 8, 17)
        assert snippet is not None
        assert "AuthService" in snippet

    def test_get_source_snippet_missing_file(self, realistic_repo):
        snippet = get_source_snippet(realistic_repo, "nonexistent.py", 0, 10)
        assert snippet is None

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
