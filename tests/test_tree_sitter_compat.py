"""Regression tests for tree-sitter symbol extraction compatibility.

Verifies that TreeSitterSymbolExtractor works correctly with
Python 3.14 + tree-sitter 0.25.2, where the parser API changed.

Key issue: tree_sitter_language_pack.get_parser() returns builtins.Parser
which creates builtins.Node objects incompatible with tree_sitter.QueryCursor.
Fix: use tree_sitter.Language + tree_sitter.Parser(lang) to create proper
tree_sitter.Node objects.
"""

from pathlib import Path

from kit import Repository
from kit.tree_sitter_symbol_extractor import TreeSitterSymbolExtractor

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "realistic_repo"

PYTHON_CODE = """\
def greet(name):
    return f'Hello, {name}!'

class Foo:
    def bar(self):
        pass

    @property
    def baz(self):
        return 42
"""

JS_CODE = """\
function hello(name) {
    return 'Hello, ' + name;
}

class Bar {
    constructor(x) {
        this.x = x;
    }
    method() {
        return this.x;
    }
}
"""


class TestPythonSymbolExtraction:
    def test_extract_functions(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".py", PYTHON_CODE)
        func_symbols = [s for s in symbols if s["type"] == "function"]
        assert len(func_symbols) >= 1
        assert any(s["name"] == "greet" for s in func_symbols)

    def test_extract_classes(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".py", PYTHON_CODE)
        class_symbols = [s for s in symbols if s["type"] == "class"]
        assert len(class_symbols) >= 1
        assert any(s["name"] == "Foo" for s in class_symbols)

    def test_extract_methods(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".py", PYTHON_CODE)
        method_symbols = [s for s in symbols if s["type"] == "method"]
        assert len(method_symbols) >= 1
        assert any(s["name"] == "bar" for s in method_symbols)

    def test_symbol_has_required_fields(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".py", PYTHON_CODE)
        for s in symbols:
            assert "name" in s
            assert "type" in s
            assert "start_line" in s
            assert "end_line" in s
            assert "code" in s

    def test_code_field_contains_source(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".py", PYTHON_CODE)
        greet = next(s for s in symbols if s["name"] == "greet")
        assert "def greet" in greet["code"]
        assert "return" in greet["code"]

    def test_line_numbers_are_valid(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".py", PYTHON_CODE)
        for s in symbols:
            assert s["start_line"] >= 0
            assert s["end_line"] >= s["start_line"]

    def test_fixture_repo_extraction(self):
        repo = Repository(str(FIXTURE_REPO))
        symbols = repo.extract_symbols()
        assert len(symbols) >= 10
        assert any(s["name"] == "User" and s["type"] == "class" for s in symbols)
        assert any(s["name"] == "AuthService" and s["type"] == "class" for s in symbols)

    def test_fixture_repo_has_methods(self):
        repo = Repository(str(FIXTURE_REPO))
        symbols = repo.extract_symbols()
        method_symbols = [s for s in symbols if s["type"] == "method"]
        assert len(method_symbols) >= 5


class TestJavaScriptSymbolExtraction:
    def test_extract_js_functions(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".js", JS_CODE)
        func_symbols = [s for s in symbols if s["type"] == "function"]
        assert len(func_symbols) >= 1

    def test_extract_js_classes(self):
        symbols = TreeSitterSymbolExtractor.extract_symbols(".js", JS_CODE)
        class_symbols = [s for s in symbols if s["type"] == "class"]
        assert len(class_symbols) >= 1


class TestParserCreationCompat:
    def test_get_parser_returns_tree_sitter_parser(self):
        parser = TreeSitterSymbolExtractor.get_parser(".py")
        assert parser is not None

        tree = parser.parse(b"def foo(): pass")
        root = tree.root_node
        assert hasattr(root, "type")
        assert hasattr(root, "children")
        assert root.type in ("source_file", "module")

    def test_parser_proper_node_types(self):
        parser = TreeSitterSymbolExtractor.get_parser(".py")
        tree = parser.parse(b"class Foo:\n    pass")
        root = tree.root_node

        for child in root.children:
            assert hasattr(child, "type")
            assert hasattr(child, "start_point")
            assert hasattr(child, "end_point")

    def test_parser_for_multiple_languages(self):
        for ext in [".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".c", ".cpp"]:
            parser = TreeSitterSymbolExtractor.get_parser(ext)
            if parser is not None:
                tree = parser.parse(b"")
                assert tree.root_node.type in ("source_file", "module", "program", "translation_unit")
