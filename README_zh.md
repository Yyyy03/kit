# kit 🛠️ 代码智能工具包

<img src="https://github.com/user-attachments/assets/7bdfa9c6-94f0-4ee0-9fdd-cbd8bd7ec060" width="360">

`kit` 是一个生产级工具包，用于代码库映射、符号提取、代码搜索，以及构建 LLM 驱动的开发者工具、智能体和工作流。

使用 `kit` 可以构建代码审查器、代码生成器，甚至是 IDE，所有这些都融入了正确的代码上下文。你可以通过 Python 直接使用 `kit`，也可以通过 MCP + 函数调用、REST API 或 CLI 使用。

访问 **[完整文档](https://kit.cased.com)** 了解详细用法、高级功能和实际示例。也可以查看 kit 的 [本地开发 MCP 服务器文档](https://kit-mcp.cased.com)。


## 快速安装

### 从 PyPI 安装

```bash
uv pip install cased-kit

# 安装 ML 功能，用于高级分析和向量搜索
uv pip install 'cased-kit[all]'
```

### 使用 uv 全局安装（最适合 CLI 使用）

如果你想全局使用 `kit` CLI 但不影响系统 Python，可以使用 `uv tool install`。这会为 `kit` 创建一个隔离环境，同时让 CLI 可以从任何地方调用：

```bash
# 全局安装基础 kit CLI
uv tool install cased-kit

# 安装全部功能（包括 MCP 服务器和所有特性）
uv tool install cased-kit[all]
```

安装后，`kit` 和 `kit-dev-mcp` 命令将全局可用。管理 uv 工具安装：

```bash
# 列出已安装的工具
uv tool list

# 如需卸载
uv tool uninstall cased-kit
```

### Claude Code 插件

使用官方插件在 [Claude Code](https://claude.ai/code) 中直接使用 kit：

```bash
/plugin marketplace add cased/claude-code-plugins
/plugin install kit-cli
```

该插件让 Claude 自主访问 kit 的代码库分析工具。当你提出以下问题时，Claude 会自动使用 kit：
- "这个代码库中的认证是怎么工作的？"
- "查找 UserModel 类的所有用法"
- "这个项目的依赖有哪些？"
- "显示 src/ 的文件结构"

详见 [Claude Code 集成指南](https://kit.cased.com/introduction/claude-code)。

## 工具包使用

### 基础 Python API

```python
from kit import Repository

# 加载本地代码库
repo = Repository("/path/to/your/local/codebase")

# 加载远程公开 GitHub 仓库
repo = Repository("https://github.com/owner/repo")

# 加载私有 GitHub 仓库（如果设置了 KIT_GITHUB_TOKEN 则自动使用）
repo = Repository("https://github.com/owner/private-repo")

# 或显式传入 token
repo = Repository("https://github.com/owner/private-repo", github_token="ghp_...")

# 指定特定 commit、tag 或分支
# repo = Repository("https://github.com/owner/repo", ref="v1.2.3")

# 多仓库场景（微服务、monorepo、团队项目）
from kit import MultiRepo
repos = MultiRepo(["~/code/frontend", "~/code/backend", "~/code/shared"])
repos.search("handleAuth")  # 在所有仓库中搜索
```

```python
# 探索仓库
print(repo.get_file_tree())
# 输出: [{"path": "src/main.py", "is_dir": False, ...}, ...]

print(repo.extract_symbols('src/main.py'))
# 输出: [{"name": "main", "type": "function", "file": "src/main.py", ...}, ...]

# 访问 git 元数据
print(f"当前 SHA: {repo.current_sha}")
print(f"分支: {repo.current_branch}")

# 读取单个文件
main_py = repo.get_file_content("src/main.py")

# 一次性读取多个文件
contents = repo.get_file_content([
    "src/main.py",
    "src/utils/helper.py",
    "tests/test_main.py",
])
print(contents["src/utils/helper.py"])
```

### 命令行界面

`kit` 提供了全面的 CLI，用于仓库分析和代码探索。

**仓库分析：**
```bash
# 获取仓库文件结构
kit file-tree /path/to/repo

# 提取符号（函数、类等）
kit symbols /path/to/repo --format table

# 搜索代码模式
kit search /path/to/repo "def main" --pattern "*.py"

# 查找符号用法
kit usages /path/to/repo "MyClass"

# 导出数据给外部工具
kit export /path/to/repo symbols symbols.json
```

**PR 审查：**
```bash
# 初始化配置
kit review --init-config

# 审查 GitHub PR
kit review --dry-run https://github.com/owner/repo/pull/123
kit review https://github.com/owner/repo/pull/123

# 审查本地 git diff（不需要 PR！）
kit review main..feature  # 比较分支
kit review HEAD~3..HEAD   # 审查最近 3 个 commit
kit review --staged       # 审查暂存的改动
```

**PR 摘要：**
```bash
# 快速生成 PR 摘要便于分类筛选
kit summarize https://github.com/owner/repo/pull/123
kit summarize --update-pr-body https://github.com/owner/repo/pull/123
```

**提交信息：**
```bash
# 从暂存的改动智能生成提交信息
git add .  # 先暂存你的改动
kit commit  # 分析并用 AI 生成的信息提交
```

**包搜索**（需要 Chroma API key）：
```bash
kit package-search-grep numpy "def.*fft" --max-results 10  # grep 风格输出
kit package-search-grep numpy "def.*fft" --json           # 结构化 JSON 输出
kit package-search-hybrid django "authentication middleware"
kit package-search-read requests "requests/models.py"
```

详见 [CLI 文档](https://kit.cased.com/introduction/cli) 获取完整使用示例。

## 核心工具能力

`kit` 帮助你的应用和智能体理解并交互代码库，提供组件让你构建自己的 AI 驱动开发者工具。

*   **探索代码结构：**
    *   使用 `repo.get_file_tree()` 获取高层视图，列出所有文件和目录。也可以传入子目录进行更有限的扫描。
    *   使用 `repo.extract_symbols()` 深入识别函数、类和其他代码构造，可在整个仓库或单个文件中提取。
    *   使用 `repo.extract_symbols_incremental()` 获取快速、缓存感知的符号提取——最适合处理仓库的小改动场景。

*   **精确定位信息：**
    *   使用 `repo.search_text()` 在代码库中快速正则搜索（可用时自动使用 [ripgrep](https://github.com/BurntSushi/ripgrep) 实现 10 倍加速）。
    *   使用 `repo.find_symbol_usages()` 追踪特定符号（如函数或类）。
    *   使用基于 AST 的模式匹配按结构查找代码（异步函数、try 块、类继承等）。

*   **为 LLM 和分析准备代码：**
    *   使用 `repo.chunk_file_by_lines()` 或 `repo.chunk_file_by_symbols()` 将大文件拆分为适合 LLM 上下文窗口的片段。
    *   使用 `repo.extract_context_around_line()` 通过行号获取函数或类的完整定义。

*   **生成代码摘要：**
    *   使用 `Summarizer` 通过 LLM 为文件、函数或类生成自然语言摘要（如 `summarizer.summarize_file()`、`summarizer.summarize_function()`）。
    *   使用 `DocstringIndexer` 构建这些 AI 生成的文档字符串的可搜索索引，用 `SummarySearcher` 查询实现智能代码发现。

*   **分析代码依赖：**
    *   使用 `repo.get_dependency_analyzer()` 映射模块间的导入关系，理解代码库结构。
    *   使用 `analyzer.generate_dependency_report()` 和 `analyzer.generate_llm_context()` 生成依赖报告和 LLM 友好的上下文。

*   **搜索包源代码（通过 Chroma）：**
    *   使用 `ChromaPackageSearch` 在热门包源代码中搜索正则模式和语义查询。
    *   访问 numpy、django、fastapi、pandas 等包的源代码。
    *   集成到 kit-dev MCP 中，在 AI 助手中无缝探索包。

*   **仓库版本与历史分析：**
    *   使用 `ref` 参数分析特定 commit、tag 或分支的仓库。
    *   比较代码随时间的演进，处理 diff，确保可复现的分析结果。
    *   使用 `repo.current_sha`、`repo.current_branch` 等访问 git 元数据。

*   **多仓库分析：**
    *   使用 `MultiRepo` 同时分析多个仓库，适用于微服务、monorepo 或团队项目。
    *   跨所有仓库的统一搜索、符号查找和依赖审计。
    *   CLI 支持：`kit multi search`、`kit multi deps`、`kit multi summary`。

## MCP 服务器（kit-dev MCP）

`kit` 包含一个增强的 MCP（模型上下文协议）服务器 **kit-dev**，专为个人本地开发工作设计。它包含 kit 的生产级代码智能和上下文构建能力，并增加了多源文档研究和包搜索功能。

**环境变量：** `OPENAI_API_KEY`、`OPENAI_BASE_URL`（用于代理/自定义端点）、`ANTHROPIC_API_KEY`

**[→ kit-dev MCP 完整文档](https://kit-mcp.cased.com)**

## kit 驱动的功能与工具

作为本库的演示，同时也是独立产品，`kit` 附带 MIT 许可的、基于 CLI 的拉取请求审查和摘要功能。

### PR 审查

拉取请求审查器的水平与付费的闭源选项相当，但使用云端模型只需极低成本。在 Cased，我们大量使用 `kit`，配合 Sonnet 4 和 gpt4.1 等模型，只需支付 token 的费用。

```bash
kit review --init-config
kit review https://github.com/owner/repo/pull/123
```

**[→ 完整 PR 审查器文档](src/kit/pr_review/README.md)**

### PR 摘要

为快速 PR 分类和理解，`kit` 包含了快速、低成本的 PR 摘要功能。非常适合需要在详细审查前快速了解 PR 内容的团队。

```bash
kit summarize https://github.com/owner/repo/pull/123
kit summarize --update-pr-body https://github.com/owner/repo/pull/123
```

**核心特性：**
- **成本比完整审查低 5-10 倍**（约 $0.005-0.02 vs $0.01-0.05+）
- **快速分类**：快速概览变更、影响和关键修改

### 提交信息

使用同样的仓库智能从暂存的改动生成智能提交信息：

```bash
git add .       # 暂存你的改动
kit commit      # 分析并用 AI 生成的信息提交
```

## 文档

**[完整文档](https://kit.cased.com)** - 详细用法、高级功能和实际示例。
完整的 REST 文档也可用。

**[kit-dev MCP 文档](https://kit-mcp.cased.com)** - 增强型 MCP 服务器完整指南

**[变更日志](https://kit.cased.com/changelog)** - 跟踪 kit 各版本的所有变更和改进

## 许可证

MIT 许可证

## 贡献

- **本地开发**：查看我们的 [运行测试指南](https://kit.cased.com/development/running-tests) 开始本地开发。
- **项目方向**：查看我们的 [路线图](https://kit.cased.com/development/roadmap) 了解未来计划和重点领域。

贡献方式：fork 仓库，做出修改，提交拉取请求。