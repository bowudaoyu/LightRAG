# LightRAG 项目分析

## 1. 项目定位与总体结构
LightRAG 是一个面向 RAG（Retrieval-Augmented Generation）的框架，强调“向量检索 + 知识图谱”的双层检索策略。整体分为三部分：

- 核心 Python 包：`lightrag/`，负责索引、查询、存储与 LLM 交互。
- API 服务：`lightrag/api/`，基于 FastAPI 提供文档管理、查询、图谱访问与 Ollama 兼容接口。
- Web UI：`lightrag_webui/`，基于 React + Vite + Bun 的前端控制台。

参考入口与结构：
- 核心主类：`lightrag/lightrag.py`
- API 服务入口：`lightrag/api/lightrag_server.py`
- 前端入口：`lightrag_webui/src/AppRouter.tsx`

## 2. 核心模块职责
### 2.1 LightRAG 主类
文件：`lightrag/lightrag.py`

核心职责：
- 统一管理索引与查询流程；
- 组织向量库、KV、图数据库三类存储；
- 对外暴露文档导入、查询、删除、索引状态等能力；
- 通过配置项与环境变量控制检索与生成策略。

### 2.2 操作与流程编排
文件：`lightrag/operate.py`

该文件是流程编排的核心，包含：
- 文本切分：`chunking_by_token_size`
- 实体/关系抽取：`extract_entities`
- 图谱合并：`merge_nodes_and_edges`
- 查询策略：`kg_query`、`naive_query`
- 缓存与去重：`handle_cache`、`save_to_cache`

### 2.3 抽象基类与类型
文件：`lightrag/base.py`、`lightrag/types.py`

提供核心抽象：
- `BaseKVStorage` / `BaseVectorStorage` / `BaseGraphStorage`
- `QueryParam`（包含查询模式、top_k、tokens 限制、重排开关等）
- `KnowledgeGraph` 等结构化类型

## 3. 关键流程概览
### 3.1 文档索引流程
1. 文本切分（token 级）  
2. 实体与关系抽取  
3. 聚合与去重，形成节点与边  
4. 写入图存储 + 向量库 + KV 存储  
5. 记录文档状态（便于查询与追踪）

相关实现：`lightrag/operate.py`、`lightrag/lightrag.py`

### 3.2 查询流程
查询核心参数在 `QueryParam` 中定义，支持多种检索模式：
- `local`：基于局部上下文
- `global`：基于图谱全局关系
- `hybrid`：混合策略
- `naive`：纯向量检索
- `mix`：默认，结合图谱与向量检索
- `bypass`：绕过检索直接调用 LLM

最终生成包含：
1) 检索上下文  
2) Prompt 组织  
3) LLM 响应（可流式输出）

## 4. 存储与 LLM 绑定生态
### 4.1 存储后端
位于 `lightrag/kg/`，实现了多种存储适配：
- 图存储：`NetworkXStorage`、`Neo4j`、`Memgraph`
- 向量库：`NanoVectorDB`、`FAISS`、`Qdrant`、`Milvus`
- KV/Doc 状态：`JsonKVStorage`、`Redis`、`MongoDB`、`PostgreSQL`

存储统一在 `lightrag/kg/__init__.py` 的注册表中管理。

### 4.2 LLM/Embedding 绑定
位于 `lightrag/llm/`，支持多类模型：
- OpenAI / Azure OpenAI
- Gemini
- Ollama
- Anthropic、Bedrock、HF、Zhipu、Jina 等

API 服务启动时通过 `.env` 与 CLI 参数选择绑定（详见 `lightrag/api/lightrag_server.py` 的 `LLMConfigCache`）。

## 5. API 服务层
入口：`lightrag/api/lightrag_server.py`

主要路由：
- 文档管理：`lightrag/api/routers/document_routes.py`
- 查询接口：`lightrag/api/routers/query_routes.py`
- 图谱接口：`lightrag/api/routers/graph_routes.py`
- Ollama 兼容接口：`lightrag/api/routers/ollama_api.py`

特点：
- 支持 WebUI 静态资源挂载
- 可选认证（`lightrag/api/auth.py`）
- 通过 `.env` 统一配置 LLM 与 Embedding

## 6. Web UI
路径：`lightrag_webui/`

特点：
- React 19 + TypeScript + Vite + Bun
- 主要能力：文档导入、图谱可视化、检索配置与对话界面
- API 访问集中在 `lightrag_webui/src/api/lightrag.ts`

## 7. 配置与运行路径
常用配置文件：
- `.env`：运行时配置（LLM、Embedding、存储连接）
- `config.ini`：部分系统参数与默认值
- `env.example` / `config.ini.example`：配置模板

启动方式：
- 纯核心库：`uv sync` 或 `pip install -e .`
- API + WebUI：`lightrag-server` 或 `uvicorn lightrag.api.lightrag_server:app --reload`
- Docker：`docker compose up`

## 8. 测试与质量控制
- Python 测试：`tests/` + 根目录 `test_*.py`
- 测试入口：`python -m pytest tests`
- 前端测试：`bun test`
- 风格检查：`ruff check .`

## 9. 扩展点与二次开发建议
- **存储扩展**：遵循 `Base*Storage` 抽象，在 `lightrag/kg/` 注册新实现。
- **LLM 适配**：新增 `lightrag/llm/` 的绑定实现，配合 `binding_options.py` 暴露配置项。
- **流程定制**：`lightrag/operate.py` 包含核心步骤，可自定义实体抽取、图谱合并或检索策略。
- **API 扩展**：新增 FastAPI router 或扩展现有路由，保持统一依赖注入模式。

---
以上分析基于仓库当前源码结构与默认配置，后续如有特定模块或性能瓶颈需要深挖，可继续针对性梳理。
