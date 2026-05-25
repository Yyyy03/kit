# Interface Implementation Summary Indexer - 任务总结

**日期**: 2026-05-25
**任务**: 实现 Interface Implementation Summary Indexer MVP

## 需求概述

基于 kit 项目现有能力，实现一个 symbol-level 摘要索引器，让 AI 在理解代码库时先扫描接口/函数/类摘要，匹配后再根据 `file_path` + `line_start` + `line_end` 定位源码。

## 第一阶段：代码库调研

### 关键模块复用情况

| 模块 | 文件 | 复用方式 |
|---|---|---|
| Repository | `src/kit/repository.py` | 直接复用 `extract_symbols()`, `get_file_content()`, `get_summarizer()` |
| TreeSitterSymbolExtractor | `src/kit/tree_sitter_symbol_extractor.py` | 间接复用（通过 Repository） |
| Summarizer | `src/kit/summaries.py` | 复用 `summarize_function()`, `summarize_class()` |
| DocstringIndexer | `src/kit/docstring_indexer.py` | 复用向量后端（ChromaDB），但新增 metadata 字段 |
| VectorSearcher | `src/kit/vector_searcher.py` | 复用 ChromaDBBackend |
| CLI | `src/kit/cli.py` | 扩展 Typer 子命令 |

### 环境问题

Python 3.14 + tree-sitter 0.25.2 环境下 `parser.parse(bytes(...))` 报错，`repo.extract_symbols()` 返回空列表。这是 kit 项目自身的既有 bug，非 MVP 新增问题。解决方案：`build()` 方法提供 `symbols=` 参数，允许手动传入 symbol 数据。

## 第二阶段：实现 MVP

### 新增文件

1. **`src/kit/interface_summary_index.py`** — 核心模块
   - `InterfaceSummaryRecord` dataclass：包含 id, type, name, signature, summary, file_path, line_start, line_end, language, content_hash, dependencies, side_effects, metadata
   - `InterfaceSummaryIndexer`：构建索引，复用 Repository + Summarizer + DocstringIndexer 后端
   - `InterfaceSummarySearcher`：语义搜索，返回带 score 的结构化结果
   - `get_source_snippet()` / `open_source_for_summary()`：源码定位

2. **`tests/test_interface_summary_index.py`** — 21 个测试
   - TestInterfaceSummaryRecord (3 tests)
   - TestHelpers (4 tests)
   - TestInterfaceSummaryIndexer (6 tests)
   - TestInterfaceSummarySearcher (2 tests)
   - TestSourceLocator (4 tests)
   - TestEndToEnd (2 tests)

### 修改文件

1. **`src/kit/__init__.py`** — 新增导出 5 个类/函数
2. **`src/kit/cli.py`** — 新增 `interface-summary` Typer 子命令组（index/search/open-source）

### CLI 命令

```bash
# 构建索引
kit interface-summary index /path/to/repo
kit interface-summary index /path/to/repo --force --extensions ".py,.js"
kit interface-summary index /path/to/repo --model gpt-4

# 搜索
kit interface-summary search /path/to/repo "how does login session creation work?"
kit interface-summary search /path/to/repo "database connection" --top-k 5

# 定位源码
kit interface-summary open-source /path/to/repo "services/auth.py::AuthService"
```

### 测试结果

```
21 passed, 0 failed
ruff: All checks passed
mypy: Success: no issues found
```

## 当前 MVP 的限制

1. tree-sitter API 不兼容（Python 3.14 + tree-sitter 0.25.2），`repo.extract_symbols()` 返回空列表
2. LLM 摘要依赖外部 API（OpenAI 或 Ollama）
3. 向量搜索需要 chromadb + sentence-transformers
4. route handler 类型未实现，只支持 function/class/method
5. dependencies/side_effects 为空数组，字段保留未填充
6. 签名提取较简单（取代码第一行）

## 下一步建议

1. 修复 tree-sitter Python 3.14 兼容性（`parser.parse(str)` 代替 `bytes`，`root_node()` 方法代替属性）
2. 扩展为 MCP tool：在 `dev_server.py` 中注册 3 个 tool
3. 为 Flask/Django/FastAPI 添加 route handler tree-sitter query
4. 利用 DependencyAnalyzer 填充 dependencies 字段
5. 实现增量文件删除检测