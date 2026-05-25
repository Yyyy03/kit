"""Interface Implementation Summary Indexer.

Provides symbol-level summary records with structured metadata for
interface/API discovery. Builds a searchable index that enables
"search first, then read source" workflows.

Classes:
    InterfaceSummaryRecord - structured summary for a single symbol
    InterfaceSummaryIndexer - builds the index from a repository
    InterfaceSummarySearcher - queries the index with semantic search
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .docstring_indexer import DocstringIndexer
from .repository import Repository
from .summaries import Summarizer
from .tree_sitter_symbol_extractor import TreeSitterSymbolExtractor
from .vector_searcher import VectorDBBackend

logger = logging.getLogger(__name__)

SUPPORTED_SYMBOL_TYPES = {"function", "method", "class"}

LANGUAGES_MAP = TreeSitterSymbolExtractor.LANGUAGES if hasattr(TreeSitterSymbolExtractor, "LANGUAGES") else set()

_LANG_EXT_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".dart": "dart",
    ".hs": "haskell",
    ".zig": "zig",
    ".tf": "hcl",
    ".hcl": "hcl",
}


@dataclass
class InterfaceSummaryRecord:
    """Structured summary record for a single interface-level symbol."""

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
    dependencies: List[str] = field(default_factory=list)
    side_effects: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_index_metadata(self) -> Dict[str, Any]:
        """Produce a flat metadata dict suitable for vector index storage."""
        d = self.to_dict()
        d["level"] = "interface_summary"
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InterfaceSummaryRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _detect_language(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return _LANG_EXT_MAP.get(ext, "unknown")


def _extract_signature(code: str, symbol_type: str) -> str:
    """Extract a one-line signature from the symbol's code block."""
    if not code:
        return ""
    first_line = code.split("\n", 1)[0].strip()
    if len(first_line) > 200:
        first_line = first_line[:200] + "..."
    return first_line


def _make_record_id(file_path: str, symbol_name: str) -> str:
    return f"{file_path}::{symbol_name}"


class InterfaceSummaryIndexer:
    """Builds a vector index of interface-level symbol summaries.

    Reuses kit's existing Repository, TreeSitterSymbolExtractor, Summarizer,
    and DocstringIndexer/VectorDBBackend infrastructure. Adds structured
    metadata (line_start, line_end, signature, content_hash, etc.) that
    the basic DocstringIndexer does not store.

    Parameters
    ----------
    repo
        Active Repository instance.
    summarizer
        Configured Summarizer instance for generating LLM summaries.
    embed_fn
        Callable that converts text -> embedding vector.
    backend
        Optional VectorDBBackend override.
    persist_dir
        Where on disk to store vector index data.
    """

    def __init__(
        self,
        repo: Repository,
        summarizer: Summarizer,
        embed_fn: Optional[Callable[[str], List[float]]] = None,
        *,
        backend: Optional[VectorDBBackend] = None,
        persist_dir: Optional[str] = None,
    ) -> None:
        self.repo = repo
        self.summarizer = summarizer

        if persist_dir:
            self.persist_dir = persist_dir
        else:
            self.persist_dir = os.path.join(self.repo.repo_path, ".kit_cache", "interface_summary_db")

        if not os.path.exists(self.persist_dir):
            os.makedirs(self.persist_dir, exist_ok=True)

        records_path = os.path.join(self.persist_dir, "summary_records.json")
        self.records_path = records_path

        self._records: Dict[str, InterfaceSummaryRecord] = {}
        if os.path.exists(records_path):
            try:
                with open(records_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._records = {k: InterfaceSummaryRecord.from_dict(v) for k, v in raw.items()}
            except Exception:
                self._records = {}

        self._docstring_indexer = DocstringIndexer(
            repo=repo,
            summarizer=summarizer,
            embed_fn=embed_fn,
            backend=backend,
            persist_dir=self.persist_dir,
        )
        self.embed_fn = self._docstring_indexer.embed_fn

    def build(
        self,
        force: bool = False,
        file_extensions: Optional[List[str]] = None,
        symbols: Optional[List[Dict[str, Any]]] = None,
    ) -> List[InterfaceSummaryRecord]:
        """Build the interface summary index for the repository.

        Scans all supported source files, extracts symbols of supported
        types (function, method, class), generates LLM summaries, and
        stores structured records + vector embeddings.

        Parameters
        ----------
        force
            If True, rebuild even if cached data exists.
        file_extensions
            Optional list of file extensions to include.
        symbols
            Optional pre-extracted symbol list to use instead of
            calling repo.extract_symbols(). Useful for testing or
            when tree-sitter extraction is unavailable.

        Returns
        -------
        List of InterfaceSummaryRecord that were indexed.
        """
        if symbols is not None:
            all_symbols = symbols
        else:
            all_symbols = self.repo.extract_symbols()

        if file_extensions:
            ext_set = set(file_extensions)
            all_symbols = [s for s in all_symbols if Path(s.get("file", "")).suffix.lower() in ext_set]

        supported = [s for s in all_symbols if s.get("type", "").lower() in SUPPORTED_SYMBOL_TYPES]

        records: List[InterfaceSummaryRecord] = []
        embeddings: List[List[float]] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []

        for sym in supported:
            file_path = sym.get("file", "")
            rel_path = self._to_relative_path(file_path)
            symbol_name = sym.get("name", "")
            symbol_type = sym.get("type", "").lower()
            code = sym.get("code", "")
            start_line = sym.get("start_line", sym.get("line", -1))
            end_line = sym.get("end_line", start_line)
            display_name = sym.get("node_path", symbol_name)

            if not symbol_name or not rel_path:
                continue

            record_id = _make_record_id(rel_path, display_name)

            content_hash = hashlib.sha1(code.encode("utf-8", "ignore")).hexdigest() if code else ""

            if not force and record_id in self._records:
                cached = self._records[record_id]
                if cached.content_hash == content_hash:
                    records.append(cached)
                    continue

            summary_text = ""
            try:
                if symbol_type in ("function", "method"):
                    summary_text = self.summarizer.summarize_function(rel_path, display_name)
                elif symbol_type == "class":
                    summary_text = self.summarizer.summarize_class(rel_path, display_name)
            except Exception as exc:
                logger.warning(f"Summarization failed for {record_id}: {exc}")
                summary_text = f"(summary unavailable: {exc})"

            signature = _extract_signature(code, symbol_type)
            language = _detect_language(rel_path)

            record = InterfaceSummaryRecord(
                id=record_id,
                type=symbol_type,
                name=display_name,
                signature=signature,
                summary=summary_text,
                file_path=rel_path,
                line_start=start_line,
                line_end=end_line,
                language=language,
                content_hash=content_hash,
                dependencies=[],
                side_effects=[],
                metadata={},
            )

            self._records[record_id] = record
            records.append(record)

            if summary_text and not summary_text.startswith("(summary unavailable"):
                emb = self.embed_fn(summary_text)
                embeddings.append(emb)
                metadatas.append(record.to_index_metadata())
                ids.append(record_id)

        if embeddings:
            logger.info(f"Adding {len(embeddings)} interface summary embeddings to the index.")
            self._docstring_indexer.backend.add(embeddings=embeddings, metadatas=metadatas, ids=ids)
            self._docstring_indexer.backend.persist()
        else:
            logger.warning("No embeddings generated for interface summary index.")

        self._save_records()
        return records

    def _to_relative_path(self, file_path: str) -> str:
        """Convert an absolute or repo-relative file path to repo-relative form."""
        repo_path_str = str(self.repo.local_path)
        if file_path.startswith(repo_path_str):
            return file_path[len(repo_path_str):].lstrip(os.sep)
        return file_path

    def _save_records(self) -> None:
        os.makedirs(self.persist_dir, exist_ok=True)
        with open(self.records_path, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in self._records.items()}, f, indent=2)

    def get_record(self, record_id: str) -> Optional[InterfaceSummaryRecord]:
        return self._records.get(record_id)

    def get_all_records(self) -> List[InterfaceSummaryRecord]:
        return list(self._records.values())

    def get_searcher(self) -> "InterfaceSummarySearcher":
        return InterfaceSummarySearcher(indexer=self)


class InterfaceSummarySearcher:
    """Searches interface summary records using vector similarity.

    Wraps the underlying SummarySearcher from DocstringIndexer and
    augments results with full InterfaceSummaryRecord metadata.
    """

    def __init__(self, indexer: InterfaceSummaryIndexer):
        self.indexer = indexer
        self.embed_fn = indexer.embed_fn

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """Search interface summaries by semantic similarity.

        Returns a list of dicts with: score, id, type, name, summary,
        file_path, line_start, line_end.
        """
        if top_k <= 0:
            return []

        emb = self.embed_fn(query)
        raw_hits = self.indexer._docstring_indexer.backend.query(emb, top_k)

        results: List[Dict[str, Any]] = []
        for hit in raw_hits:
            hit_id = hit.get("id", "")
            record = self.indexer.get_record(hit_id)
            score = hit.get("score", 0.0)

            if record:
                results.append({
                    "score": score,
                    "id": record.id,
                    "type": record.type,
                    "name": record.name,
                    "summary": record.summary,
                    "file_path": record.file_path,
                    "line_start": record.line_start,
                    "line_end": record.line_end,
                })
            else:
                results.append({
                    "score": score,
                    "id": hit_id,
                    "type": hit.get("type", "unknown"),
                    "name": hit.get("name", hit.get("symbol_name", "")),
                    "summary": hit.get("summary", ""),
                    "file_path": hit.get("file_path", ""),
                    "line_start": hit.get("line_start", -1),
                    "line_end": hit.get("line_end", -1),
                })

        return results


def get_source_snippet(repo: Repository, file_path: str, line_start: int, line_end: int) -> Optional[str]:
    """Read a source code snippet from the repository by file path and line range.

    Parameters
    ----------
    repo
        Active Repository instance.
    file_path
        Relative file path within the repository.
    line_start
        Starting line number (0-indexed, matching tree-sitter output).
    line_end
        Ending line number (0-indexed).

    Returns
    -------
    The source code snippet as a string, or None if the file cannot be read.
    """
    try:
        content = repo.get_file_content(file_path)
    except (FileNotFoundError, IOError):
        return None

    lines = content.splitlines()
    snippet_lines = lines[line_start : line_end + 1]
    return "\n".join(snippet_lines)


def open_source_for_summary(repo: Repository, summary_id: str, records: Dict[str, InterfaceSummaryRecord]) -> Optional[str]:
    """Locate and read source code for a given summary record ID.

    Parameters
    ----------
    repo
        Active Repository instance.
    summary_id
        The record ID (e.g. "relative/path/file.py::symbol_name").
    records
        The dictionary of cached InterfaceSummaryRecords.

    Returns
    -------
    Source code snippet string, or None if not found.
    """
    record = records.get(summary_id)
    if not record:
        return None
    return get_source_snippet(repo, record.file_path, record.line_start, record.line_end)
