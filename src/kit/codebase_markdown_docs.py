"""Generate structured Markdown documentation from a codebase.

Scans source files, extracts symbols (functions, classes, methods),
generates summaries and usage examples, and renders them as Markdown.

Supports incremental (content-hash based) caching and split-by-directory output.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kit.interface_summary_index import (
    _compute_content_hash,
    _detect_language,
    _extract_signature,
    detect_interface_type,
)
from kit.repository import Repository

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    "test",
    "tests",
    "testing",
    "__pycache__",
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "site-packages",
}

_SKIP_PREFIXES = ("_", ".")


@dataclass
class SymbolDocRecord:
    """Structured documentation record for a single symbol."""

    id: str
    type: str
    name: str
    signature: str = ""
    summary: str = ""
    file_path: str = ""
    line_start: int = -1
    line_end: int = -1
    language: str = ""
    content_hash: str = ""
    code: str = ""
    usage_example: str = ""
    import_statement: str = ""
    parameters: str = ""
    return_value: str = ""
    interface_detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SymbolDocRecord":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _should_skip_file(
    rel_path: str,
    extensions: Optional[List[str]] = None,
) -> bool:
    """Return True if a file should be skipped based on path or extension."""
    parts = Path(rel_path).parts
    for part in parts:
        if part.lower() in _SKIP_DIRS:
            return True
        if part.startswith(".") and part not in (".github", ".kit"):
            return True
    filename = parts[-1] if parts else ""
    if filename.startswith("_") and filename != "__init__.py":
        return True
    if extensions:
        ext = Path(rel_path).suffix.lower()
        if ext not in extensions:
            return True
    return False


def _make_import_statement(
    file_path: str,
    symbol_name: str,
    symbol_type: str,
    language: str,
    node_path: str,
) -> str:
    """Generate a plausible import statement for a symbol."""
    if language != "python":
        return ""
    parts = Path(file_path).parts
    module_parts = [p for p in parts[:-1]] if len(parts) > 1 else []
    stem = Path(file_path).stem
    if stem == "__init__":
        if module_parts:
            module = ".".join(module_parts)
        else:
            module = ""
    else:
        module_parts.append(stem)
        module = ".".join(module_parts)
    if not module:
        module = stem
    if symbol_type == "class":
        return f"from {module} import {symbol_name}"
    elif symbol_type in ("function", "method"):
        if node_path and "." in node_path:
            parent = node_path.rsplit(".", 1)[0]
            return f"from {module} import {parent}"
        return f"from {module} import {symbol_name}"
    return f"from {module} import {symbol_name}"


def _extract_call_args(raw_args: str) -> str:
    """Extract just the positional arg names for a usage example call."""
    if not raw_args:
        return ""
    arg_names: list[str] = []
    for a in raw_args.split(","):
        a = a.strip()
        if a in ("self", "cls", "", "*"):
            continue
        if a.startswith("**") or a.startswith("*"):
            continue
        if "=" in a:
            a = a.split("=")[0].strip()
        if ":" in a:
            a = a.split(":")[0].strip()
        if a:
            arg_names.append(a)
    return ", ".join(arg_names[:3])


def _generate_python_usage_example(
    symbol_name: str,
    symbol_type: str,
    signature: str,
    node_path: str,
    import_statement: str,
) -> str:
    """Generate a rule-based usage example for Python symbols."""
    if symbol_type == "class":
        return f"{import_statement}\n\nobj = {symbol_name}()"
    elif symbol_type == "function":
        if "(" in signature and ")" in signature:
            args_match = re.search(r"\(([^)]*)\)", signature)
            if args_match:
                raw_args = args_match.group(1).strip()
                call_args = _extract_call_args(raw_args)
                if call_args:
                    return f"{import_statement}\n\nresult = {symbol_name}({call_args})"
        return f"{import_statement}\n\nresult = {symbol_name}()"
    elif symbol_type == "method":
        if not node_path or "." not in node_path:
            return ""
        parent = node_path.rsplit(".", 1)[0]
        call_name = f"obj.{symbol_name}"
        if "(" in signature and ")" in signature:
            args_match = re.search(r"\(([^)]*)\)", signature)
            if args_match:
                raw_args = args_match.group(1).strip()
                call_args = _extract_call_args(raw_args)
                if call_args:
                    return f"{import_statement}\n\nobj = {parent}()\nresult = {call_name}({call_args})"
        return f"{import_statement}\n\nobj = {parent}()\nresult = {call_name}()"
    return ""


class UsageExampleGenerator:
    """Rule-based usage example generator with optional LLM enhancement."""

    def generate(
        self,
        record: SymbolDocRecord,
        *,
        summarizer: Any = None,
    ) -> str:
        """Generate a usage example for the given symbol doc record.

        First tries rule-based generation; if available, optionally
        enhances with LLM summarizer.
        """
        rule_based = ""
        if record.language == "python":
            rule_based = _generate_python_usage_example(
                symbol_name=record.name,
                symbol_type=record.type,
                signature=record.signature,
                node_path=record.interface_detail.get("node_path", ""),
                import_statement=record.import_statement,
            )
        if summarizer is not None and rule_based:
            try:
                enhanced = self._enhance_with_llm(record, rule_based, summarizer)
                if enhanced:
                    return enhanced
            except Exception:
                logger.debug("LLM usage example enhancement failed, keeping rule-based")
        return rule_based

    def _enhance_with_llm(
        self,
        record: SymbolDocRecord,
        rule_based: str,
        summarizer: Any,
    ) -> str:
        """Attempt to enhance usage example via LLM summarizer."""
        prompt = (
            f"Given this Python code signature:\n{record.signature}\n\n"
            f"Here is a basic usage example:\n{rule_based}\n\n"
            f"Improve the usage example to be more realistic and demonstrative. "
            f"Return ONLY the improved code block, no explanation."
        )
        result = summarizer.summarize_function(record.code, prompt_extra=prompt)
        if result and len(result) > 10:
            if "```" not in result:
                return result.strip()
            lines = result.strip().split("\n")
            code_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    code_lines.append(line)
            if code_lines:
                return "\n".join(code_lines)
        return ""


_STRIP_SUMMARY_HEADERS_RE = re.compile(
    r"^\s*\*\*([^*]+)\*\*\s*:? *(.*)$", re.MULTILINE
)
_BULLET_LINE_RE = re.compile(r"^\s*-+\s+")
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

_SUMMARY_SECTIONS = {
    "purpose": "purpose",
    "description": "purpose",
    "summary": "purpose",
    "parameters": "parameters",
    "args": "parameters",
    "arguments": "parameters",
    "param": "parameters",
    "return value": "return_value",
    "returns": "return_value",
    "return": "return_value",
    "key attributes": "purpose",
    "main methods": "purpose",
    "example": "example",
    "examples": "example",
    "notes": "purpose",
    "note": "purpose",
    "side effects": "purpose",
}


def _parse_structured_summary(raw: str) -> Tuple[str, str, str]:
    """Parse an LLM summary into (clean_summary, parameters_text, return_value_text).

    Sections tagged as 'purpose' are merged into the clean_summary paragraph.
    Sections tagged as 'parameters' become the parameters_text.
    Sections tagged as 'return_value' become the return_value_text.
    'example' sections are dropped (Usage field replaces them).
    """
    if not raw:
        return ("", "", "")

    text = _CODE_BLOCK_RE.sub("", raw)
    purpose_parts: List[str] = []
    params_parts: List[str] = []
    retval_parts: List[str] = []

    lines = text.splitlines()
    current_section: Optional[str] = None
    collected: List[str] = []

    for line in lines:
        header_match = _STRIP_SUMMARY_HEADERS_RE.match(line)
        if header_match:
            heading = header_match.group(1).lower().rstrip(":").strip()
            content_on_same_line = header_match.group(2).strip()
            section_key = _SUMMARY_SECTIONS.get(heading)
            if current_section is not None:
                _flush_section(current_section, collected, purpose_parts, params_parts, retval_parts)
                collected = []
            current_section = section_key
            if content_on_same_line and section_key:
                collected.append(content_on_same_line)
            continue

        if line.strip().startswith("-"):
            bullet_content = _BULLET_LINE_RE.sub("", line).strip()
            if bullet_content and current_section:
                collected.append(bullet_content)
            continue

        if current_section is None:
            stripped = line.strip()
            if stripped:
                purpose_parts.append(stripped)
        else:
            stripped = line.strip()
            if stripped:
                collected.append(stripped)

    if current_section is not None:
        _flush_section(current_section, collected, purpose_parts, params_parts, retval_parts)

    summary = _join_purpose_parts(purpose_parts)
    parameters = _join_parts(params_parts)
    return_value = _join_parts(retval_parts)
    return (summary, parameters, return_value)


def _flush_section(
    section: Optional[str],
    collected: List[str],
    purpose_parts: List[str],
    params_parts: List[str],
    retval_parts: List[str],
) -> None:
    if section == "purpose":
        purpose_parts.extend(collected)
    elif section == "parameters":
        params_parts.extend(collected)
    elif section == "return_value":
        retval_parts.extend(collected)
    elif section == "example":
        pass


def _join_purpose_parts(parts: List[str]) -> str:
    if not parts:
        return ""
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 300:
        sentences = re.split(r"[.!?]\s+", text)
        kept = []
        total_len = 0
        for s in sentences:
            if total_len + len(s) > 280:
                break
            kept.append(s)
            total_len += len(s)
        text = " ".join(kept)
        if not text.endswith((".", "!", "?")):
            text = text.rstrip() + "."
    return text


def _join_parts(parts: List[str]) -> str:
    if not parts:
        return ""
    return " ".join(parts).strip()


def _clean_summary(raw: str) -> str:
    """Return a 1-3 sentence pure paragraph summary from raw LLM output.

    Merges Purpose/Description/Key Attributes/Main Methods content into
    a single paragraph. Drops Parameters/Return Value/Example sections
    (they are shown as separate fields when verbose=True).
    """
    summary, _params, _retval = _parse_structured_summary(raw)
    return summary


class MarkdownRenderer:
    """Render SymbolDocRecords as structured Markdown.

    Default output: file title, symbol title, Summary, Parameters, Return Value, Usage, separator.
    Set verbose=True to also render Type, Signature, Import, Source, Location, totals.
    Set include_source=True to include source code blocks (only shown when verbose=True).
    """

    def render_single_file(
        self,
        file_path: str,
        records: List[SymbolDocRecord],
        language: str,
        *,
        include_source: bool = False,
        verbose: bool = False,
    ) -> str:
        """Render documentation for a single file."""
        lang_tag = language if language != "unknown" else ""
        lines: List[str] = []
        lines.append(f"## `{file_path}`")
        lines.append("")
        sorted_records = sorted(records, key=lambda r: r.line_start if r.line_start >= 0 else 0)
        for rec in sorted_records:
            lines.append(f"#### `{rec.name}`")
            lines.append("")
            if verbose:
                type_label = rec.type.replace("_", " ").title()
                if rec.interface_detail:
                    iface_type = rec.interface_detail.get("interface_type", "")
                    if iface_type and iface_type != rec.type:
                        framework = rec.interface_detail.get("framework", "")
                        extra = f" ({framework} {iface_type})" if framework else f" ({iface_type})"
                        type_label += extra
                lines.append(f"**Type**: {type_label}")
                lines.append("")
                if rec.signature:
                    lines.append(f"**Signature**: `{rec.signature}`")
                    lines.append("")
                if rec.import_statement:
                    if lang_tag:
                        lines.append("**Import**:")
                        lines.append(f"```{lang_tag}")
                    else:
                        lines.append("**Import**:")
                        lines.append("```")
                    lines.append(rec.import_statement)
                    lines.append("```")
                    lines.append("")
            if include_source and verbose and rec.code:
                if lang_tag:
                    lines.append("**Source**:")
                    lines.append(f"```{lang_tag}")
                else:
                    lines.append("**Source**:")
                    lines.append("```")
                lines.append(rec.code)
                lines.append("```")
                lines.append("")
            summary = rec.summary
            if summary:
                lines.append("**Summary**:")
                lines.append(summary)
                lines.append("")
            if rec.parameters:
                lines.append("**Parameters**:")
                lines.append(rec.parameters)
                lines.append("")
            if rec.return_value:
                lines.append("**Return Value**:")
                lines.append(rec.return_value)
                lines.append("")
            if rec.usage_example:
                if lang_tag:
                    lines.append("**Usage**:")
                    lines.append(f"```{lang_tag}")
                else:
                    lines.append("**Usage**:")
                    lines.append("```")
                lines.append(rec.usage_example)
                lines.append("```")
                lines.append("")
            if verbose:
                loc = f"L{rec.line_start}" if rec.line_start >= 0 else ""
                if rec.line_end >= 0 and rec.line_end != rec.line_start:
                    loc = f"L{rec.line_start}-{rec.line_end}"
                if loc:
                    lines.append(f"*Location: {file_path}:{loc}*")
                lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    def render_full(
        self,
        records: List[SymbolDocRecord],
        *,
        title: str = "Codebase Documentation",
        split_by_dir: bool = False,
        include_source: bool = False,
        verbose: bool = False,
    ) -> str:
        """Render full documentation from all records."""
        if not records:
            return f"# {title}\n\nNo documented symbols found.\n"
        by_file: Dict[str, List[SymbolDocRecord]] = {}
        for r in records:
            by_file.setdefault(r.file_path, []).append(r)
        parts: List[str] = []
        parts.append(f"# {title}")
        parts.append("")
        if verbose:
            total = len(records)
            files = len(by_file)
            parts.append(f"**Total symbols:** {total}  ")
            parts.append(f"**Total files:** {files}")
            parts.append("")
        parts.append("---")
        parts.append("")
        if split_by_dir:
            by_dir: Dict[str, Dict[str, List[SymbolDocRecord]]] = {}
            for fp, recs in by_file.items():
                dir_key = str(Path(fp).parent) if Path(fp).parent != Path(".") else "(root)"
                by_dir.setdefault(dir_key, {})[fp] = recs
            for dir_key in sorted(by_dir.keys()):
                for fp in sorted(by_dir[dir_key].keys()):
                    recs = by_dir[dir_key][fp]
                    lang = recs[0].language if recs else "unknown"
                    parts.append(self.render_single_file(fp, recs, lang, include_source=include_source, verbose=verbose))
                parts.append("")
        else:
            for fp in sorted(by_file.keys()):
                recs = by_file[fp]
                lang = recs[0].language if recs else "unknown"
                parts.append(self.render_single_file(fp, recs, lang, include_source=include_source, verbose=verbose))
        return "\n".join(parts)

    def render_split_by_dir(
        self,
        records: List[SymbolDocRecord],
        *,
        title: str = "Codebase Documentation",
        output_dir: Path,
        include_source: bool = False,
        verbose: bool = False,
    ) -> Dict[str, Path]:
        """Render documentation split into separate files per directory.

        Returns mapping of dir_key -> output file path.
        """
        by_file: Dict[str, List[SymbolDocRecord]] = {}
        for r in records:
            by_file.setdefault(r.file_path, []).append(r)
        by_dir: Dict[str, Dict[str, List[SymbolDocRecord]]] = {}
        for fp, recs in by_file.items():
            dir_key = str(Path(fp).parent) if Path(fp).parent != Path(".") else "_root"
            by_dir.setdefault(dir_key, {})[fp] = recs
        output: Dict[str, Path] = {}
        output_dir.mkdir(parents=True, exist_ok=True)
        index_lines: List[str] = [f"# {title} — Index", ""]
        for dir_key in sorted(by_dir.keys()):
            safe_name = dir_key.replace("/", "_").replace("\\", "_").replace(" ", "_")
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "", safe_name)
            filename = f"{safe_name}.md"
            filepath = output_dir / filename
            dir_records: List[SymbolDocRecord] = []
            for fp in sorted(by_dir[dir_key].keys()):
                dir_records.extend(by_dir[dir_key][fp])
            content = self.render_full(dir_records, title=f"{title} — {dir_key}", split_by_dir=False, include_source=include_source, verbose=verbose)
            filepath.write_text(content, encoding="utf-8")
            output[dir_key] = filepath
            count = len(dir_records)
            index_lines.append(f"- [{dir_key}](./{filename}) — {count} symbols")
        index_lines.append("")
        index_path = output_dir / "index.md"
        index_path.write_text("\n".join(index_lines), encoding="utf-8")
        output["_index"] = index_path
        return output


class DocCache:
    """Content-hash based incremental cache for doc records."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir
        self._records: Dict[str, SymbolDocRecord] = {}

    def load(self) -> None:
        """Load cached records from disk."""
        if not self.cache_dir:
            return
        cache_file = self.cache_dir / "doc_cache.json"
        if not cache_file.exists():
            return
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            for entry in data:
                rec = SymbolDocRecord.from_dict(entry)
                self._records[rec.id] = rec
        except Exception:
            logger.debug("Failed to load doc cache, starting fresh")
            self._records = {}

    def save(self) -> None:
        """Save cached records to disk."""
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / "doc_cache.json"
        data = [r.to_dict() for r in self._records.values()]
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, record_id: str, content_hash: str) -> Optional[SymbolDocRecord]:
        """Return cached record if content_hash matches."""
        cached = self._records.get(record_id)
        if cached and cached.content_hash == content_hash:
            return cached
        return None

    def put(self, record: SymbolDocRecord) -> None:
        """Store a record in cache."""
        self._records[record.id] = record

    def clear(self) -> None:
        """Clear all cached records."""
        self._records = {}

    def stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        return {
            "total_cached": len(self._records),
            "unique_files": len({r.file_path for r in self._records.values()}),
        }


class CodebaseMarkdownDocGenerator:
    """Orchestrates the full pipeline: scan -> extract -> summarize -> render."""

    def __init__(
        self,
        repo: Repository,
        summarizer: Any = None,
        *,
        cache_dir: Optional[str] = None,
    ):
        self.repo = repo
        self.summarizer = summarizer
        self.usage_gen = UsageExampleGenerator()
        self.renderer = MarkdownRenderer()
        cache_path = Path(cache_dir) if cache_dir else None
        self.cache = DocCache(cache_path)
        self.cache.load()

    def generate(
        self,
        *,
        file_extensions: Optional[List[str]] = None,
        max_symbols: Optional[int] = None,
        force: bool = False,
        split_by_dir: bool = False,
        title: str = "Codebase Documentation",
        include_source: bool = False,
        verbose: bool = False,
    ) -> Tuple[List[SymbolDocRecord], str]:
        """Generate documentation for the repository.

        Returns (records, markdown_string).
        """
        if force:
            self.cache.clear()

        all_symbols = self.repo.extract_symbols()
        filtered: List[Dict[str, Any]] = []
        for sym in all_symbols:
            fp = sym.get("file", "")
            if isinstance(fp, str):
                rel = self._to_relative(fp)
            else:
                rel = str(fp)
            ext = Path(rel).suffix.lower()
            if file_extensions and ext not in file_extensions:
                continue
            if _should_skip_file(rel, file_extensions):
                continue
            filtered.append(sym)

        if max_symbols and len(filtered) > max_symbols:
            filtered = filtered[:max_symbols]

        records: List[SymbolDocRecord] = []
        errors: List[str] = []

        for sym in filtered:
            try:
                rec = self._build_record(sym)
                records.append(rec)
            except Exception as exc:
                fp = sym.get("file", "unknown")
                name = sym.get("name", "unknown")
                msg = f"Error processing {fp}::{name}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        if errors:
            logger.warning(f"{len(errors)} symbols had errors during doc generation")

        self.cache.save()

        md = self.renderer.render_full(records, title=title, split_by_dir=split_by_dir, include_source=include_source, verbose=verbose)
        return records, md

    def generate_to_file(
        self,
        output_path: str,
        *,
        file_extensions: Optional[List[str]] = None,
        max_symbols: Optional[int] = None,
        force: bool = False,
        split_by_dir: bool = False,
        title: str = "Codebase Documentation",
        include_source: bool = False,
        verbose: bool = False,
    ) -> Tuple[List[SymbolDocRecord], Path]:
        """Generate documentation and write to a file.

        If split_by_dir is True, output_path must be a directory.
        Returns (records, output_path).
        """
        records, md = self.generate(
            file_extensions=file_extensions,
            max_symbols=max_symbols,
            force=force,
            split_by_dir=split_by_dir,
            title=title,
            include_source=include_source,
            verbose=verbose,
        )
        out = Path(output_path)
        if split_by_dir:
            self.renderer.render_split_by_dir(records, title=title, output_dir=out, include_source=include_source, verbose=verbose)
            return records, out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        return records, out

    def _build_record(self, sym: Dict[str, Any]) -> SymbolDocRecord:
        """Build a SymbolDocRecord from a raw symbol dict."""
        fp = sym.get("file", "")
        rel = self._to_relative(fp)
        name = sym.get("name", "unknown")
        sym_type = sym.get("type", "unknown")
        node_path = sym.get("node_path", "")
        code = sym.get("code", "")
        start_line = sym.get("start_line", -1)
        end_line = sym.get("end_line", -1)
        language = _detect_language(rel)
        content_hash = _compute_content_hash(code)
        record_id = f"{rel}::{name}"

        cached = self.cache.get(record_id, content_hash)
        if cached:
            return cached

        signature = _extract_signature(code, sym_type)

        try:
            source = self.repo.get_file_content(rel)
            iface_type, iface_detail = detect_interface_type(sym, source, rel)
        except Exception:
            iface_type = sym_type
            iface_detail = {}

        detail = dict(iface_detail)
        detail["interface_type"] = iface_type
        detail["node_path"] = node_path

        import_stmt = _make_import_statement(rel, name, sym_type, language, node_path)

        summary = ""
        if self.summarizer is not None and code:
            try:
                if sym_type == "class":
                    summary = self.summarizer.summarize_class(rel, name)
                elif sym_type in ("function", "method"):
                    summary = self.summarizer.summarize_function(rel, name)
                else:
                    summary = self.summarizer.summarize_function(rel, name)
            except Exception:
                logger.debug(f"Summary generation failed for {record_id}")

        parsed_summary, parsed_params, parsed_retval = _parse_structured_summary(summary)

        rec = SymbolDocRecord(
            id=record_id,
            type=sym_type,
            name=name,
            signature=signature,
            summary=parsed_summary,
            file_path=rel,
            line_start=start_line,
            line_end=end_line,
            language=language,
            content_hash=content_hash,
            code=code,
            usage_example="",
            import_statement=import_stmt,
            parameters=parsed_params,
            return_value=parsed_retval,
            interface_detail=detail,
        )

        rec.usage_example = self.usage_gen.generate(rec, summarizer=self.summarizer)

        self.cache.put(rec)
        return rec

    def _to_relative(self, file_path: str) -> str:
        """Convert an absolute or already-relative path to repo-relative."""
        abs_root = str(self.repo.local_path)
        if file_path.startswith(abs_root):
            return file_path[len(abs_root):].lstrip("/").lstrip("\\")
        return file_path
