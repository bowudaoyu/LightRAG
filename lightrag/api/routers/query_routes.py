"""
This module contains all query-related routes for the LightRAG API.
"""

import json
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from lightrag.base import QueryParam
from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger
from pydantic import BaseModel, Field, field_validator

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    query: str = Field(
        min_length=3,
        description="The query text",
    )

    mode: Literal["local", "global", "hybrid", "naive", "mix", "bypass"] = Field(
        default="mix",
        description="Query mode",
    )

    only_need_context: Optional[bool] = Field(
        default=None,
        description="If True, only returns the retrieved context without generating a response.",
    )

    only_need_prompt: Optional[bool] = Field(
        default=None,
        description="If True, only returns the generated prompt without producing a response.",
    )

    response_type: Optional[str] = Field(
        min_length=1,
        default=None,
        description="Defines the response format. Examples: 'Multiple Paragraphs', 'Single Paragraph', 'Bullet Points'.",
    )

    top_k: Optional[int] = Field(
        ge=1,
        default=None,
        description="Number of top items to retrieve. Represents entities in 'local' mode and relationships in 'global' mode.",
    )

    chunk_top_k: Optional[int] = Field(
        ge=1,
        default=None,
        description="Number of text chunks to retrieve initially from vector search and keep after reranking.",
    )

    max_entity_tokens: Optional[int] = Field(
        default=None,
        description="Maximum number of tokens allocated for entity context in unified token control system.",
        ge=1,
    )

    max_relation_tokens: Optional[int] = Field(
        default=None,
        description="Maximum number of tokens allocated for relationship context in unified token control system.",
        ge=1,
    )

    max_total_tokens: Optional[int] = Field(
        default=None,
        description="Maximum total tokens budget for the entire query context (entities + relations + chunks + system prompt).",
        ge=1,
    )

    hl_keywords: list[str] = Field(
        default_factory=list,
        description="List of high-level keywords to prioritize in retrieval. Leave empty to use the LLM to generate the keywords.",
    )

    ll_keywords: list[str] = Field(
        default_factory=list,
        description="List of low-level keywords to refine retrieval focus. Leave empty to use the LLM to generate the keywords.",
    )

    conversation_history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Stores past conversation history to maintain context. Format: [{'role': 'user/assistant', 'content': 'message'}].",
    )

    user_prompt: Optional[str] = Field(
        default=None,
        description="User-provided prompt for the query. If provided, this will be used instead of the default value from prompt template.",
    )

    enable_rerank: Optional[bool] = Field(
        default=None,
        description="Enable reranking for retrieved text chunks. If True but no rerank model is configured, a warning will be issued. Default is True.",
    )

    include_references: Optional[bool] = Field(
        default=True,
        description="If True, includes reference list in responses. Affects /query and /query/stream endpoints. /query/data always includes references.",
    )

    include_chunk_content: Optional[bool] = Field(
        default=False,
        description="If True, includes actual chunk text content in references. Only applies when include_references=True. Useful for evaluation and debugging.",
    )

    stream: Optional[bool] = Field(
        default=True,
        description="If True, enables streaming output for real-time responses. Only affects /query/stream endpoint.",
    )

    @field_validator("query", mode="after")
    @classmethod
    def query_strip_after(cls, query: str) -> str:
        return query.strip()

    @field_validator("conversation_history", mode="after")
    @classmethod
    def conversation_history_role_check(
        cls, conversation_history: List[Dict[str, Any]] | None
    ) -> List[Dict[str, Any]] | None:
        if conversation_history is None:
            return None
        for msg in conversation_history:
            if "role" not in msg:
                raise ValueError("Each message must have a 'role' key.")
            if not isinstance(msg["role"], str) or not msg["role"].strip():
                raise ValueError("Each message 'role' must be a non-empty string.")
        return conversation_history

    def to_query_params(self, is_stream: bool) -> "QueryParam":
        """Converts a QueryRequest instance into a QueryParam instance."""
        # Use Pydantic's `.model_dump(exclude_none=True)` to remove None values automatically
        # Exclude API-level parameters that don't belong in QueryParam
        request_data = self.model_dump(
            exclude_none=True, exclude={"query", "include_chunk_content"}
        )

        # Ensure `mode` and `stream` are set explicitly
        param = QueryParam(**request_data)
        param.stream = is_stream
        return param


class ReferenceItem(BaseModel):
    """A single reference item in query responses."""

    reference_id: str = Field(description="Unique reference identifier")
    file_path: str = Field(description="Path to the source file")
    content: Optional[List[str]] = Field(
        default=None,
        description="List of chunk contents from this file (only present when include_chunk_content=True)",
    )


class QueryResponse(BaseModel):
    response: str = Field(
        description="The generated response",
    )
    references: Optional[List[ReferenceItem]] = Field(
        default=None,
        description="Reference list (Disabled when include_references=False, /query/data always includes references.)",
    )


class QueryDataResponse(BaseModel):
    status: str = Field(description="Query execution status")
    message: str = Field(description="Status message")
    data: Dict[str, Any] = Field(
        description="Query result data containing entities, relationships, chunks, and references"
    )
    metadata: Dict[str, Any] = Field(
        description="Query metadata including mode, keywords, and processing information"
    )


class StreamChunkResponse(BaseModel):
    """Response model for streaming chunks in NDJSON format"""

    references: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Reference list (only in first chunk when include_references=True)",
    )
    response: Optional[str] = Field(
        default=None, description="Response content chunk or complete response"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if processing fails"
    )


def create_query_routes(rag, api_key: Optional[str] = None, top_k: int = 60):
    combined_auth = get_combined_auth_dependency(api_key)

    @router.post(
        "/query",
        response_model=QueryResponse,
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "Successful RAG query response",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "response": {
                                    "type": "string",
                                    "description": "The generated response from the RAG system",
                                },
                                "references": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "reference_id": {"type": "string"},
                                            "file_path": {"type": "string"},
                                            "content": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": "List of chunk contents from this file (only included when include_chunk_content=True)",
                                            },
                                        },
                                    },
                                    "description": "Reference list (only included when include_references=True)",
                                },
                            },
                            "required": ["response"],
                        },
                        "examples": {
                            "with_references": {
                                "summary": "Response with references",
                                "description": "Example response when include_references=True",
                                "value": {
                                    "response": "Artificial Intelligence (AI) is a branch of computer science that aims to create intelligent machines capable of performing tasks that typically require human intelligence, such as learning, reasoning, and problem-solving.",
                                    "references": [
                                        {
                                            "reference_id": "1",
                                            "file_path": "/documents/ai_overview.pdf",
                                        },
                                        {
                                            "reference_id": "2",
                                            "file_path": "/documents/machine_learning.txt",
                                        },
                                    ],
                                },
                            },
                            "with_chunk_content": {
                                "summary": "Response with chunk content",
                                "description": "Example response when include_references=True and include_chunk_content=True. Note: content is an array of chunks from the same file.",
                                "value": {
                                    "response": "Artificial Intelligence (AI) is a branch of computer science that aims to create intelligent machines capable of performing tasks that typically require human intelligence, such as learning, reasoning, and problem-solving.",
                                    "references": [
                                        {
                                            "reference_id": "1",
                                            "file_path": "/documents/ai_overview.pdf",
                                            "content": [
                                                "Artificial Intelligence (AI) represents a transformative field in computer science focused on creating systems that can perform tasks requiring human-like intelligence. These tasks include learning from experience, understanding natural language, recognizing patterns, and making decisions.",
                                                "AI systems can be categorized into narrow AI, which is designed for specific tasks, and general AI, which aims to match human cognitive abilities across a wide range of domains.",
                                            ],
                                        },
                                        {
                                            "reference_id": "2",
                                            "file_path": "/documents/machine_learning.txt",
                                            "content": [
                                                "Machine learning is a subset of AI that enables computers to learn and improve from experience without being explicitly programmed. It focuses on the development of algorithms that can access data and use it to learn for themselves."
                                            ],
                                        },
                                    ],
                                },
                            },
                            "without_references": {
                                "summary": "Response without references",
                                "description": "Example response when include_references=False",
                                "value": {
                                    "response": "Artificial Intelligence (AI) is a branch of computer science that aims to create intelligent machines capable of performing tasks that typically require human intelligence, such as learning, reasoning, and problem-solving."
                                },
                            },
                            "different_modes": {
                                "summary": "Different query modes",
                                "description": "Examples of responses from different query modes",
                                "value": {
                                    "local_mode": "Focuses on specific entities and their relationships",
                                    "global_mode": "Provides broader context from relationship patterns",
                                    "hybrid_mode": "Combines local and global approaches",
                                    "naive_mode": "Simple vector similarity search",
                                    "mix_mode": "Integrates knowledge graph and vector retrieval",
                                },
                            },
                        },
                    }
                },
            },
            400: {
                "description": "Bad Request - Invalid input parameters",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Query text must be at least 3 characters long"
                        },
                    }
                },
            },
            500: {
                "description": "Internal Server Error - Query processing failed",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Failed to process query: LLM service unavailable"
                        },
                    }
                },
            },
        },
    )
    async def query_text(request: QueryRequest):
        """
        Comprehensive RAG query endpoint with non-streaming response. Parameter "stream" is ignored.

        This endpoint performs Retrieval-Augmented Generation (RAG) queries using various modes
        to provide intelligent responses based on your knowledge base.

        **Query Modes:**
        - **local**: Focuses on specific entities and their direct relationships
        - **global**: Analyzes broader patterns and relationships across the knowledge graph
        - **hybrid**: Combines local and global approaches for comprehensive results
        - **naive**: Simple vector similarity search without knowledge graph
        - **mix**: Integrates knowledge graph retrieval with vector search (recommended)
        - **bypass**: Direct LLM query without knowledge retrieval

        conversation_history parameteris sent to LLM only, does not affect retrieval results.

        **Usage Examples:**

        Basic query:
        ```json
        {
            "query": "What is machine learning?",
            "mode": "mix"
        }
        ```

        Bypass initial LLM call by providing high-level and low-level keywords:
        ```json
        {
            "query": "What is Retrieval-Augmented-Generation?",
            "hl_keywords": ["machine learning", "information retrieval", "natural language processing"],
            "ll_keywords": ["retrieval augmented generation", "RAG", "knowledge base"],
            "mode": "mix"
        }
        ```

        Advanced query with references:
        ```json
        {
            "query": "Explain neural networks",
            "mode": "hybrid",
            "include_references": true,
            "response_type": "Multiple Paragraphs",
            "top_k": 10
        }
        ```

        Conversation with history:
        ```json
        {
            "query": "Can you give me more details?",
            "conversation_history": [
                {"role": "user", "content": "What is AI?"},
                {"role": "assistant", "content": "AI is artificial intelligence..."}
            ]
        }
        ```

        Args:
            request (QueryRequest): The request object containing query parameters:
                - **query**: The question or prompt to process (min 3 characters)
                - **mode**: Query strategy - "mix" recommended for best results
                - **include_references**: Whether to include source citations
                - **response_type**: Format preference (e.g., "Multiple Paragraphs")
                - **top_k**: Number of top entities/relations to retrieve
                - **conversation_history**: Previous dialogue context
                - **max_total_tokens**: Token budget for the entire response

        Returns:
            QueryResponse: JSON response containing:
                - **response**: The generated answer to your query
                - **references**: Source citations (if include_references=True)

        Raises:
            HTTPException:
                - 400: Invalid input parameters (e.g., query too short)
                - 500: Internal processing error (e.g., LLM service unavailable)
        """
        try:
            param = request.to_query_params(
                False
            )  # Ensure stream=False for non-streaming endpoint
            # Force stream=False for /query endpoint regardless of include_references setting
            param.stream = False

            # Unified approach: always use aquery_llm for both cases
            result = await rag.aquery_llm(request.query, param=param)

            # Extract LLM response and references from unified result
            llm_response = result.get("llm_response", {})
            data = result.get("data", {})
            references = data.get("references", [])

            # Get the non-streaming response content
            response_content = llm_response.get("content", "")
            if not response_content:
                response_content = "No relevant context found for the query."

            # Enrich references with chunk content if requested
            if request.include_references and request.include_chunk_content:
                chunks = data.get("chunks", [])
                # Create a mapping from reference_id to chunk content
                ref_id_to_content = {}
                for chunk in chunks:
                    ref_id = chunk.get("reference_id", "")
                    content = chunk.get("content", "")
                    if ref_id and content:
                        # Collect chunk content; join later to avoid quadratic string concatenation
                        ref_id_to_content.setdefault(ref_id, []).append(content)

                # Add content to references
                enriched_references = []
                for ref in references:
                    ref_copy = ref.copy()
                    ref_id = ref.get("reference_id", "")
                    if ref_id in ref_id_to_content:
                        # Keep content as a list of chunks (one file may have multiple chunks)
                        ref_copy["content"] = ref_id_to_content[ref_id]
                    enriched_references.append(ref_copy)
                references = enriched_references

            # Return response with or without references based on request
            if request.include_references:
                return QueryResponse(response=response_content, references=references)
            else:
                return QueryResponse(response=response_content, references=None)
        except Exception as e:
            logger.error(f"Error processing query: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/gossip",
        response_model=QueryResponse,
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "Successful Gossip RAG query response",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "response": {
                                    "type": "string",
                                    "description": "The generated gossip response",
                                },
                                "references": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "reference_id": {"type": "string"},
                                            "file_path": {"type": "string"},
                                            "content": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": "List of chunk contents",
                                            },
                                        },
                                    },
                                    "description": "Reference list",
                                },
                            },
                            "required": ["response"],
                        },
                    }
                },
            },
        },
    )
    async def gossip(request: QueryRequest):
        """
        Gossip-style RAG query endpoint.

        This endpoint generates "gossip" style content about artifacts/items.
        It first tries to retrieve context from the knowledge base, then always
        calls LLM to generate the response - with or without KB context.
        """
        from lightrag.prompt import PROMPTS
        from functools import partial

        try:
            # Step 1: Try to get context from knowledge base
            param = request.to_query_params(False)
            param.only_need_context = True  # Only retrieve context, don't call LLM yet
            param.stream = False

            result = await rag.aquery_llm(request.query, param=param)

            # Extract context and references
            llm_response = result.get("llm_response", {})
            data = result.get("data", {})
            references = data.get("references", [])
            context_content = llm_response.get("content", "")

            # Determine if we have valid context from KB
            # When only_need_context=True, no LLM is called, so we just check:
            # 1. status is not failure (context retrieval succeeded)
            # 2. context_content is not empty
            has_kb_context = (
                result.get("status") != "failure"
                and bool(context_content)
            )

            # Step 2: Build prompt and call LLM (always, regardless of KB context)
            if has_kb_context:
                context_data = context_content
                logger.info("[gossip] Using knowledge base context")
            else:
                context_data = "（知识库中暂无相关信息，请基于你的通用知识为用户提供有趣的八卦内容）"
                references = []  # No references when no KB context
                logger.info("[gossip] No KB context, using LLM built-in knowledge")

            # Format the gossip prompt
            gossip_prompt = PROMPTS["artifact_gossip_response"].format(
                user_prompt=request.query,
                context_data=context_data,
            )

            # Call LLM to generate gossip response
            use_llm_func = rag.llm_model_func
            use_llm_func = partial(use_llm_func, _priority=5)

            response_content = await use_llm_func(
                request.query,
                system_prompt=gossip_prompt,
                stream=False,
            )

            # Enrich references if needed
            if has_kb_context and request.include_references and request.include_chunk_content:
                chunks = data.get("chunks", [])
                ref_id_to_content = {}
                for chunk in chunks:
                    ref_id = chunk.get("reference_id", "")
                    content = chunk.get("content", "")
                    if ref_id and content:
                        ref_id_to_content.setdefault(ref_id, []).append(content)

                enriched_references = []
                for ref in references:
                    ref_copy = ref.copy()
                    ref_id = ref.get("reference_id", "")
                    if ref_id in ref_id_to_content:
                        ref_copy["content"] = ref_id_to_content[ref_id]
                    enriched_references.append(ref_copy)
                references = enriched_references

            return QueryResponse(
                response=response_content,
                references=references if request.include_references else None
            )

        except Exception as e:
            logger.error(f"Error processing gossip: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # 新版智能气泡端点
    # ========================================================================

    class BubbleRequest(BaseModel):
        """智能气泡请求模型"""
        query: str = Field(
            min_length=1,
            description="文物名称",
        )
        artifact_type: Optional[str] = Field(
            default=None,
            description="文物类型（如瓷器、青铜器、书画等）。如不提供，将尝试自动推断。",
        )
        mode: Literal["local", "global", "hybrid", "naive", "mix"] = Field(
            default="mix",
            description="知识库检索模式",
        )
        top_k: Optional[int] = Field(
            ge=1,
            default=10,
            description="检索的实体/关系数量",
        )
        include_references: bool = Field(
            default=True,
            description="是否返回参考来源",
        )
        include_detail: bool = Field(
            default=False,
            description="是否返回详情内容（开发调试用，会增加响应时间）",
        )
        bubble_count: int = Field(
            default=3,
            ge=1,
            le=10,
            description="气泡数量，默认 3，最大 10",
        )

    class BubbleItem(BaseModel):
        """单个气泡"""
        type: str = Field(description="话题类型")
        emoji: str = Field(description="表情符号")
        title: str = Field(description="气泡标题（10-15字）")
        detail: Optional[str] = Field(default=None, description="详情内容（50-100字），需通过 /bubble/detail 接口获取")

    class BubbleResponse(BaseModel):
        """智能气泡响应模型"""
        artifact_name: str = Field(description="文物名称")
        artifact_type: str = Field(description="文物类型")
        bubbles: List[BubbleItem] = Field(description="气泡列表")
        references: Optional[List[Dict[str, Any]]] = Field(
            default=None,
            description="参考来源列表"
        )

    @router.post(
        "/bubble",
        response_model=BubbleResponse,
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "智能气泡响应（默认只包含标题，设置 include_detail=true 可同时返回详情）",
                "content": {
                    "application/json": {
                        "example": {
                            "artifact_name": "越王勾践剑",
                            "artifact_type": "兵器",
                            "bubbles": [
                                {
                                    "type": "实战能力",
                                    "emoji": "⚔️",
                                    "title": "削20层纸不卷刃",
                                    "detail": None
                                },
                                {
                                    "type": "黑科技",
                                    "emoji": "🔬",
                                    "title": "防锈配方至今成谜",
                                    "detail": None
                                },
                                {
                                    "type": "名人八卦",
                                    "emoji": "🎭",
                                    "title": "卧薪尝胆那位的剑",
                                    "detail": None
                                }
                            ],
                            "references": [
                                {"reference_id": "1", "file_path": "/docs/越王勾践剑.pdf"}
                            ]
                        }
                    }
                },
            },
        },
    )
    async def bubble(request: BubbleRequest):
        """
        智能气泡端点 - 根据文物类型智能生成气泡标题

        特性：
        - 根据文物类型（瓷器、青铜器、书画等）智能选择最合适的话题
        - 每个气泡包含简短标题（10-15字）
        - 支持15+种话题类型：值多少钱、谁用过它、现代等价物、鉴定秘籍等
        - bubble_count 控制气泡数量，默认 3，最大 10
        - 默认不返回 detail（用户点击时通过 /bubble/detail 按需获取）
        - 设置 include_detail=true 可同时返回详情（开发调试用，会增加响应时间）
        """
        from lightrag.prompt import (
            PROMPTS,
            select_bubble_topics,
            format_topic_pool_for_prompt,
            ARTIFACT_TOPIC_RULES
        )
        from functools import partial
        import re

        try:
            # Step 1: 确定文物类型
            artifact_type = request.artifact_type
            if not artifact_type:
                # 尝试从知识库推断文物类型，或使用默认值
                artifact_type = "default"
                # 可以通过简单规则推断
                name = request.query
                if any(kw in name for kw in ["剑", "刀", "戟", "矛", "弓", "弩", "戈"]):
                    artifact_type = "兵器"
                elif any(kw in name for kw in ["瓶", "碗", "盘", "壶", "罐", "杯", "盏", "窑"]):
                    artifact_type = "瓷器"
                elif any(kw in name for kw in ["鼎", "簋", "尊", "彝", "觥", "铜"]):
                    artifact_type = "青铜器"
                elif any(kw in name for kw in ["画", "帖", "卷", "图", "书法"]):
                    artifact_type = "书画"
                elif any(kw in name for kw in ["玉", "璧", "琮", "佩"]):
                    artifact_type = "玉器"
                elif any(kw in name for kw in ["金", "银"]):
                    artifact_type = "金银器"
                elif any(kw in name for kw in ["佛", "菩萨", "罗汉", "观音"]):
                    artifact_type = "佛像"
                elif any(kw in name for kw in ["印", "玺"]):
                    artifact_type = "印章"
                elif any(kw in name for kw in ["漆"]):
                    artifact_type = "漆器"
                elif any(kw in name for kw in ["钱", "币", "通宝"]):
                    artifact_type = "钱币"
                elif any(kw in name for kw in ["琴", "瑟", "笛", "箫", "钟", "磬"]):
                    artifact_type = "乐器"

            logger.info(
                f"[bubble] Artifact: {request.query}, Type: {artifact_type}, "
                f"include_detail: {request.include_detail}, bubble_count: {request.bubble_count}"
            )

            # Step 2: 智能选择话题
            bubble_count = request.bubble_count or 3
            selected_topics = select_bubble_topics(artifact_type, num_topics=bubble_count)
            topic_pool_str = format_topic_pool_for_prompt(selected_topics)
            logger.info(f"[bubble] Selected topics: {[t['type'] for t in selected_topics]}")

            # Step 3: 从知识库检索上下文
            param = QueryParam(
                mode=request.mode,
                top_k=request.top_k or 10,
                only_need_context=True,
                stream=False,
            )

            result = await rag.aquery_llm(request.query, param=param)

            # 提取上下文和引用
            llm_response = result.get("llm_response", {})
            data = result.get("data", {})
            references = data.get("references", [])
            context_content = llm_response.get("content", "")

            has_kb_context = (
                result.get("status") != "failure"
                and bool(context_content)
            )

            if has_kb_context:
                context_data = context_content
                logger.info("[bubble] Using knowledge base context")
            else:
                context_data = "（知识库中暂无相关信息，请基于你的通用知识生成内容）"
                references = []
                logger.info("[bubble] No KB context, using LLM built-in knowledge")

            # Step 4: 构建 prompt 并调用 LLM
            # 根据 include_detail 选择不同的 prompt
            prompt_key = "artifact_bubble_response_with_detail" if request.include_detail else "artifact_bubble_response"
            bubble_prompt = PROMPTS[prompt_key].format(
                artifact_name=request.query,
                artifact_type=artifact_type if artifact_type != "default" else "通用文物",
                topic_pool=topic_pool_str,
                context_data=context_data,
                bubble_count=bubble_count,
            )

            use_llm_func = rag.llm_model_func
            use_llm_func = partial(use_llm_func, _priority=5)

            response_content = await use_llm_func(
                request.query,
                system_prompt=bubble_prompt,
                stream=False,
            )

            # Step 5: 解析 JSON 响应
            # 尝试从响应中提取 JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', response_content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析整个响应
                json_str = response_content.strip()
                # 移除可能的前缀文字
                if '{' in json_str:
                    json_str = json_str[json_str.index('{'):]
                if '}' in json_str:
                    json_str = json_str[:json_str.rindex('}')+1]

            try:
                parsed_response = json.loads(json_str)
                bubbles_data = parsed_response.get("bubbles", [])
            except json.JSONDecodeError as e:
                logger.warning(f"[bubble] Failed to parse JSON response: {e}")
                # 如果解析失败，构造一个默认响应（不含 detail，detail 通过 /bubble/detail 接口获取）
                bubbles_data = [
                    {
                        "type": topic["type"],
                        "emoji": topic["emoji"],
                        "title": f"关于{request.query}的{topic['type']}",
                    }
                    for topic in selected_topics
                ]

            # Step 6: 构造响应
            bubbles = [
                BubbleItem(
                    type=b.get("type", "未知"),
                    emoji=b.get("emoji", "💡"),
                    title=b.get("title", ""),
                    # 如果请求了 include_detail，则从 LLM 响应中获取 detail
                    detail=b.get("detail") if request.include_detail else None
                )
                for b in bubbles_data[:bubble_count]  # 最多返回 bubble_count 个气泡
            ]

            return BubbleResponse(
                artifact_name=request.query,
                artifact_type=artifact_type if artifact_type != "default" else "通用文物",
                bubbles=bubbles,
                references=references if request.include_references else None
            )

        except Exception as e:
            logger.error(f"Error processing bubble: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # 气泡详情端点 - 用户点击气泡时按需生成 detail
    # ========================================================================

    class BubbleDetailRequest(BaseModel):
        """气泡详情请求模型"""
        artifact_name: str = Field(
            min_length=1,
            description="文物名称",
        )
        artifact_type: Optional[str] = Field(
            default=None,
            description="文物类型（如瓷器、青铜器、书画等）",
        )
        topic_type: str = Field(
            min_length=1,
            description="话题类型（如：值多少钱、黑科技、名人八卦等）",
        )
        bubble_title: str = Field(
            min_length=1,
            description="气泡标题（用户点击的那个标题）",
        )
        mode: Literal["local", "global", "hybrid", "naive", "mix"] = Field(
            default="mix",
            description="知识库检索模式",
        )
        top_k: Optional[int] = Field(
            ge=1,
            default=10,
            description="检索的实体/关系数量",
        )

    class BubbleDetailResponse(BaseModel):
        """气泡详情响应模型"""
        artifact_name: str = Field(description="文物名称")
        topic_type: str = Field(description="话题类型")
        bubble_title: str = Field(description="气泡标题")
        detail: str = Field(description="详情内容（50-100字）")

    class BubbleDetailStreamChunk(BaseModel):
        """气泡详情流式响应块模型（NDJSON）"""
        artifact_name: Optional[str] = Field(default=None, description="文物名称")
        topic_type: Optional[str] = Field(default=None, description="话题类型")
        bubble_title: Optional[str] = Field(default=None, description="气泡标题")
        detail: Optional[str] = Field(default=None, description="详情内容片段")
        error: Optional[str] = Field(default=None, description="错误信息")

    @router.post(
        "/bubble/detail",
        response_model=BubbleDetailResponse,
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "气泡详情响应",
                "content": {
                    "application/json": {
                        "example": {
                            "artifact_name": "越王勾践剑",
                            "topic_type": "实战能力",
                            "bubble_title": "削20层纸不卷刃",
                            "detail": "1965年出土时，考古人员用它轻松划破20多层纸，2500年前的剑至今锋利无比。剑身的菱形暗纹不是装饰，是古人独创的复合金属工艺，让剑既硬又韧。"
                        }
                    }
                },
            },
        },
    )
    async def bubble_detail(request: BubbleDetailRequest):
        """
        气泡详情端点 - 用户点击气泡后按需生成详情内容

        特性：
        - 根据气泡标题和话题类型生成50-100字的详情内容
        - 只在用户点击时才调用，节省 API 成本
        - 从知识库检索相关上下文，确保内容准确
        """
        from lightrag.prompt import PROMPTS
        from functools import partial

        try:
            # Step 1: 确定文物类型
            artifact_type = request.artifact_type or "通用文物"

            logger.info(f"[bubble/detail] Artifact: {request.artifact_name}, Topic: {request.topic_type}, Title: {request.bubble_title}")

            # Step 2: 从知识库检索上下文
            param = QueryParam(
                mode=request.mode,
                top_k=request.top_k or 10,
                only_need_context=True,
                stream=False,
            )

            result = await rag.aquery_llm(request.artifact_name, param=param)

            # 提取上下文
            llm_response = result.get("llm_response", {})
            context_content = llm_response.get("content", "")

            has_kb_context = (
                result.get("status") != "failure"
                and bool(context_content)
            )

            if has_kb_context:
                context_data = context_content
                logger.info("[bubble/detail] Using knowledge base context")
            else:
                context_data = "（知识库中暂无相关信息，请基于你的通用知识生成内容）"
                logger.info("[bubble/detail] No KB context, using LLM built-in knowledge")

            # Step 3: 构建 prompt 并调用 LLM
            detail_prompt = PROMPTS["artifact_bubble_detail"].format(
                artifact_name=request.artifact_name,
                artifact_type=artifact_type,
                topic_type=request.topic_type,
                bubble_title=request.bubble_title,
                context_data=context_data,
            )

            use_llm_func = rag.llm_model_func
            use_llm_func = partial(use_llm_func, _priority=5)

            detail_content = await use_llm_func(
                request.bubble_title,
                system_prompt=detail_prompt,
                stream=False,
            )

            # 清理响应内容（去除可能的多余空白）
            detail_content = detail_content.strip()

            return BubbleDetailResponse(
                artifact_name=request.artifact_name,
                topic_type=request.topic_type,
                bubble_title=request.bubble_title,
                detail=detail_content
            )

        except Exception as e:
            logger.error(f"Error processing bubble detail: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/bubble/detail/stream",
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "气泡详情流式响应（NDJSON）",
                "content": {
                    "application/x-ndjson": {
                        "schema": {
                            "type": "string",
                            "format": "ndjson",
                            "description": "每行一个 JSON 对象。首行包含元信息，后续为 detail 片段或 error。",
                            "example": (
                                '{"artifact_name":"越王勾践剑","topic_type":"实战能力","bubble_title":"削20层纸不卷刃"}\n'
                                '{"detail":"1965年出土时，考古人员用它轻松划破20多层纸，"}\n'
                                '{"detail":"剑身菱形暗纹并非装饰，而是复合金属工艺的体现。"}'
                            ),
                        }
                    }
                },
            },
            500: {
                "description": "Internal Server Error - Bubble detail streaming failed",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {"detail": "Failed to stream bubble detail"},
                    }
                },
            },
        },
    )
    async def bubble_detail_stream(request: BubbleDetailRequest):
        """
        气泡详情流式端点 - 以 NDJSON 格式返回详情内容片段

        返回格式：
        - 首行：包含 artifact_name/topic_type/bubble_title
        - 后续行：{"detail": "..."} 或 {"error": "..."}
        """
        from lightrag.prompt import PROMPTS
        from functools import partial
        from fastapi.responses import StreamingResponse

        try:
            artifact_type = request.artifact_type or "通用文物"

            logger.info(
                "[bubble/detail/stream] Artifact: %s, Topic: %s, Title: %s",
                request.artifact_name,
                request.topic_type,
                request.bubble_title,
            )

            param = QueryParam(
                mode=request.mode,
                top_k=request.top_k or 10,
                only_need_context=True,
                stream=False,
            )

            result = await rag.aquery_llm(request.artifact_name, param=param)

            llm_response = result.get("llm_response", {})
            context_content = llm_response.get("content", "")

            has_kb_context = (
                result.get("status") != "failure"
                and bool(context_content)
            )

            if has_kb_context:
                context_data = context_content
                logger.info("[bubble/detail/stream] Using knowledge base context")
            else:
                context_data = "（知识库中暂无相关信息，请基于你的通用知识生成内容）"
                logger.info("[bubble/detail/stream] No KB context, using LLM built-in knowledge")

            detail_prompt = PROMPTS["artifact_bubble_detail"].format(
                artifact_name=request.artifact_name,
                artifact_type=artifact_type,
                topic_type=request.topic_type,
                bubble_title=request.bubble_title,
                context_data=context_data,
            )

            use_llm_func = rag.llm_model_func
            use_llm_func = partial(use_llm_func, _priority=5)

            async def stream_generator():
                header = {
                    "artifact_name": request.artifact_name,
                    "topic_type": request.topic_type,
                    "bubble_title": request.bubble_title,
                }
                yield f"{json.dumps(header)}\n"

                try:
                    response = await use_llm_func(
                        request.bubble_title,
                        system_prompt=detail_prompt,
                        stream=True,
                    )

                    if hasattr(response, "__aiter__"):
                        async for chunk in response:
                            if chunk:
                                yield f"{json.dumps({'detail': chunk})}\n"
                    else:
                        detail_content = str(response).strip()
                        if not detail_content:
                            detail_content = "No detail content generated."
                        yield f"{json.dumps({'detail': detail_content})}\n"
                except Exception as e:
                    logger.error(f"Streaming bubble detail error: {str(e)}")
                    yield f"{json.dumps({'error': str(e)})}\n"

            return StreamingResponse(
                stream_generator(),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "application/x-ndjson",
                    "X-Accel-Buffering": "no",
                },
            )
        except Exception as e:
            logger.error(
                f"Error processing bubble detail stream: {str(e)}",
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/query/stream",
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "Flexible RAG query response - format depends on stream parameter",
                "content": {
                    "application/x-ndjson": {
                        "schema": {
                            "type": "string",
                            "format": "ndjson",
                            "description": "Newline-delimited JSON (NDJSON) format used for both streaming and non-streaming responses. For streaming: multiple lines with separate JSON objects. For non-streaming: single line with complete JSON object.",
                            "example": '{"references": [{"reference_id": "1", "file_path": "/documents/ai.pdf"}]}\n{"response": "Artificial Intelligence is"}\n{"response": " a field of computer science"}\n{"response": " that focuses on creating intelligent machines."}',
                        },
                        "examples": {
                            "streaming_with_references": {
                                "summary": "Streaming mode with references (stream=true)",
                                "description": "Multiple NDJSON lines when stream=True and include_references=True. First line contains references, subsequent lines contain response chunks.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/ai_overview.pdf"}, {"reference_id": "2", "file_path": "/documents/ml_basics.txt"}]}\n{"response": "Artificial Intelligence (AI) is a branch of computer science"}\n{"response": " that aims to create intelligent machines capable of performing"}\n{"response": " tasks that typically require human intelligence, such as learning,"}\n{"response": " reasoning, and problem-solving."}',
                            },
                            "streaming_with_chunk_content": {
                                "summary": "Streaming mode with chunk content (stream=true, include_chunk_content=true)",
                                "description": "Multiple NDJSON lines when stream=True, include_references=True, and include_chunk_content=True. First line contains references with content arrays (one file may have multiple chunks), subsequent lines contain response chunks.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/ai_overview.pdf", "content": ["Artificial Intelligence (AI) represents a transformative field...", "AI systems can be categorized into narrow AI and general AI..."]}, {"reference_id": "2", "file_path": "/documents/ml_basics.txt", "content": ["Machine learning is a subset of AI that enables computers to learn..."]}]}\n{"response": "Artificial Intelligence (AI) is a branch of computer science"}\n{"response": " that aims to create intelligent machines capable of performing"}\n{"response": " tasks that typically require human intelligence."}',
                            },
                            "streaming_without_references": {
                                "summary": "Streaming mode without references (stream=true)",
                                "description": "Multiple NDJSON lines when stream=True and include_references=False. Only response chunks are sent.",
                                "value": '{"response": "Machine learning is a subset of artificial intelligence"}\n{"response": " that enables computers to learn and improve from experience"}\n{"response": " without being explicitly programmed for every task."}',
                            },
                            "non_streaming_with_references": {
                                "summary": "Non-streaming mode with references (stream=false)",
                                "description": "Single NDJSON line when stream=False and include_references=True. Complete response with references in one message.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/neural_networks.pdf"}], "response": "Neural networks are computational models inspired by biological neural networks that consist of interconnected nodes (neurons) organized in layers. They are fundamental to deep learning and can learn complex patterns from data through training processes."}',
                            },
                            "non_streaming_without_references": {
                                "summary": "Non-streaming mode without references (stream=false)",
                                "description": "Single NDJSON line when stream=False and include_references=False. Complete response only.",
                                "value": '{"response": "Deep learning is a subset of machine learning that uses neural networks with multiple layers (hence deep) to model and understand complex patterns in data. It has revolutionized fields like computer vision, natural language processing, and speech recognition."}',
                            },
                            "error_response": {
                                "summary": "Error during streaming",
                                "description": "Error handling in NDJSON format when an error occurs during processing.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/ai.pdf"}]}\n{"response": "Artificial Intelligence is"}\n{"error": "LLM service temporarily unavailable"}',
                            },
                        },
                    }
                },
            },
            400: {
                "description": "Bad Request - Invalid input parameters",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Query text must be at least 3 characters long"
                        },
                    }
                },
            },
            500: {
                "description": "Internal Server Error - Query processing failed",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Failed to process streaming query: Knowledge graph unavailable"
                        },
                    }
                },
            },
        },
    )
    async def query_text_stream(request: QueryRequest):
        """
        Advanced RAG query endpoint with flexible streaming response.

        This endpoint provides the most flexible querying experience, supporting both real-time streaming
        and complete response delivery based on your integration needs.

        **Response Modes:**
        - Real-time response delivery as content is generated
        - NDJSON format: each line is a separate JSON object
        - First line: `{"references": [...]}` (if include_references=True)
        - Subsequent lines: `{"response": "content chunk"}`
        - Error handling: `{"error": "error message"}`

        > If stream parameter is False, or the query hit LLM cache, complete response delivered in a single streaming message.

        **Response Format Details**
        - **Content-Type**: `application/x-ndjson` (Newline-Delimited JSON)
        - **Structure**: Each line is an independent, valid JSON object
        - **Parsing**: Process line-by-line, each line is self-contained
        - **Headers**: Includes cache control and connection management

        **Query Modes (same as /query endpoint)**
        - **local**: Entity-focused retrieval with direct relationships
        - **global**: Pattern analysis across the knowledge graph
        - **hybrid**: Combined local and global strategies
        - **naive**: Vector similarity search only
        - **mix**: Integrated knowledge graph + vector retrieval (recommended)
        - **bypass**: Direct LLM query without knowledge retrieval

        conversation_history parameteris sent to LLM only, does not affect retrieval results.

        **Usage Examples**

        Real-time streaming query:
        ```json
        {
            "query": "Explain machine learning algorithms",
            "mode": "mix",
            "stream": true,
            "include_references": true
        }
        ```

        Bypass initial LLM call by providing high-level and low-level keywords:
        ```json
        {
            "query": "What is Retrieval-Augmented-Generation?",
            "hl_keywords": ["machine learning", "information retrieval", "natural language processing"],
            "ll_keywords": ["retrieval augmented generation", "RAG", "knowledge base"],
            "mode": "mix"
        }
        ```

        Complete response query:
        ```json
        {
            "query": "What is deep learning?",
            "mode": "hybrid",
            "stream": false,
            "response_type": "Multiple Paragraphs"
        }
        ```

        Conversation with context:
        ```json
        {
            "query": "Can you elaborate on that?",
            "stream": true,
            "conversation_history": [
                {"role": "user", "content": "What is neural network?"},
                {"role": "assistant", "content": "A neural network is..."}
            ]
        }
        ```

        **Response Processing:**

        ```python
        async for line in response.iter_lines():
            data = json.loads(line)
            if "references" in data:
                # Handle references (first message)
                references = data["references"]
            if "response" in data:
                # Handle content chunk
                content_chunk = data["response"]
            if "error" in data:
                # Handle error
                error_message = data["error"]
        ```

        **Error Handling:**
        - Streaming errors are delivered as `{"error": "message"}` lines
        - Non-streaming errors raise HTTP exceptions
        - Partial responses may be delivered before errors in streaming mode
        - Always check for error objects when processing streaming responses

        Args:
            request (QueryRequest): The request object containing query parameters:
                - **query**: The question or prompt to process (min 3 characters)
                - **mode**: Query strategy - "mix" recommended for best results
                - **stream**: Enable streaming (True) or complete response (False)
                - **include_references**: Whether to include source citations
                - **response_type**: Format preference (e.g., "Multiple Paragraphs")
                - **top_k**: Number of top entities/relations to retrieve
                - **conversation_history**: Previous dialogue context for multi-turn conversations
                - **max_total_tokens**: Token budget for the entire response

        Returns:
            StreamingResponse: NDJSON streaming response containing:
                - **Streaming mode**: Multiple JSON objects, one per line
                  - References object (if requested): `{"references": [...]}`
                  - Content chunks: `{"response": "chunk content"}`
                  - Error objects: `{"error": "error message"}`
                - **Non-streaming mode**: Single JSON object
                  - Complete response: `{"references": [...], "response": "complete content"}`

        Raises:
            HTTPException:
                - 400: Invalid input parameters (e.g., query too short, invalid mode)
                - 500: Internal processing error (e.g., LLM service unavailable)

        Note:
            This endpoint is ideal for applications requiring flexible response delivery.
            Use streaming mode for real-time interfaces and non-streaming for batch processing.
        """
        try:
            # Use the stream parameter from the request, defaulting to True if not specified
            stream_mode = request.stream if request.stream is not None else True
            param = request.to_query_params(stream_mode)

            from fastapi.responses import StreamingResponse

            # Unified approach: always use aquery_llm for all cases
            result = await rag.aquery_llm(request.query, param=param)

            async def stream_generator():
                # Extract references and LLM response from unified result
                references = result.get("data", {}).get("references", [])
                llm_response = result.get("llm_response", {})

                # Enrich references with chunk content if requested
                if request.include_references and request.include_chunk_content:
                    data = result.get("data", {})
                    chunks = data.get("chunks", [])
                    # Create a mapping from reference_id to chunk content
                    ref_id_to_content = {}
                    for chunk in chunks:
                        ref_id = chunk.get("reference_id", "")
                        content = chunk.get("content", "")
                        if ref_id and content:
                            # Collect chunk content
                            ref_id_to_content.setdefault(ref_id, []).append(content)

                    # Add content to references
                    enriched_references = []
                    for ref in references:
                        ref_copy = ref.copy()
                        ref_id = ref.get("reference_id", "")
                        if ref_id in ref_id_to_content:
                            # Keep content as a list of chunks (one file may have multiple chunks)
                            ref_copy["content"] = ref_id_to_content[ref_id]
                        enriched_references.append(ref_copy)
                    references = enriched_references

                if llm_response.get("is_streaming"):
                    # Streaming mode: send references first, then stream response chunks
                    if request.include_references:
                        yield f"{json.dumps({'references': references})}\n"

                    response_stream = llm_response.get("response_iterator")
                    if response_stream:
                        try:
                            async for chunk in response_stream:
                                if chunk:  # Only send non-empty content
                                    yield f"{json.dumps({'response': chunk})}\n"
                        except Exception as e:
                            logger.error(f"Streaming error: {str(e)}")
                            yield f"{json.dumps({'error': str(e)})}\n"
                else:
                    # Non-streaming mode: send complete response in one message
                    response_content = llm_response.get("content", "")
                    if not response_content:
                        response_content = "No relevant context found for the query."

                    # Create complete response object
                    complete_response = {"response": response_content}
                    if request.include_references:
                        complete_response["references"] = references

                    yield f"{json.dumps(complete_response)}\n"

            return StreamingResponse(
                stream_generator(),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "application/x-ndjson",
                    "X-Accel-Buffering": "no",  # Ensure proper handling of streaming response when proxied by Nginx
                },
            )
        except Exception as e:
            logger.error(f"Error processing streaming query: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/query/data",
        response_model=QueryDataResponse,
        dependencies=[Depends(combined_auth)],
        responses={
            200: {
                "description": "Successful data retrieval response with structured RAG data",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "status": {
                                    "type": "string",
                                    "enum": ["success", "failure"],
                                    "description": "Query execution status",
                                },
                                "message": {
                                    "type": "string",
                                    "description": "Status message describing the result",
                                },
                                "data": {
                                    "type": "object",
                                    "properties": {
                                        "entities": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "entity_name": {"type": "string"},
                                                    "entity_type": {"type": "string"},
                                                    "description": {"type": "string"},
                                                    "source_id": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                    "reference_id": {"type": "string"},
                                                },
                                            },
                                            "description": "Retrieved entities from knowledge graph",
                                        },
                                        "relationships": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "src_id": {"type": "string"},
                                                    "tgt_id": {"type": "string"},
                                                    "description": {"type": "string"},
                                                    "keywords": {"type": "string"},
                                                    "weight": {"type": "number"},
                                                    "source_id": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                    "reference_id": {"type": "string"},
                                                },
                                            },
                                            "description": "Retrieved relationships from knowledge graph",
                                        },
                                        "chunks": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "content": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                    "chunk_id": {"type": "string"},
                                                    "reference_id": {"type": "string"},
                                                },
                                            },
                                            "description": "Retrieved text chunks from vector database",
                                        },
                                        "references": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "reference_id": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                },
                                            },
                                            "description": "Reference list for citation purposes",
                                        },
                                    },
                                    "description": "Structured retrieval data containing entities, relationships, chunks, and references",
                                },
                                "metadata": {
                                    "type": "object",
                                    "properties": {
                                        "query_mode": {"type": "string"},
                                        "keywords": {
                                            "type": "object",
                                            "properties": {
                                                "high_level": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                                "low_level": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                        "processing_info": {
                                            "type": "object",
                                            "properties": {
                                                "total_entities_found": {
                                                    "type": "integer"
                                                },
                                                "total_relations_found": {
                                                    "type": "integer"
                                                },
                                                "entities_after_truncation": {
                                                    "type": "integer"
                                                },
                                                "relations_after_truncation": {
                                                    "type": "integer"
                                                },
                                                "final_chunks_count": {
                                                    "type": "integer"
                                                },
                                            },
                                        },
                                    },
                                    "description": "Query metadata including mode, keywords, and processing information",
                                },
                            },
                            "required": ["status", "message", "data", "metadata"],
                        },
                        "examples": {
                            "successful_local_mode": {
                                "summary": "Local mode data retrieval",
                                "description": "Example of structured data from local mode query focusing on specific entities",
                                "value": {
                                    "status": "success",
                                    "message": "Query executed successfully",
                                    "data": {
                                        "entities": [
                                            {
                                                "entity_name": "Neural Networks",
                                                "entity_type": "CONCEPT",
                                                "description": "Computational models inspired by biological neural networks",
                                                "source_id": "chunk-123",
                                                "file_path": "/documents/ai_basics.pdf",
                                                "reference_id": "1",
                                            }
                                        ],
                                        "relationships": [
                                            {
                                                "src_id": "Neural Networks",
                                                "tgt_id": "Machine Learning",
                                                "description": "Neural networks are a subset of machine learning algorithms",
                                                "keywords": "subset, algorithm, learning",
                                                "weight": 0.85,
                                                "source_id": "chunk-123",
                                                "file_path": "/documents/ai_basics.pdf",
                                                "reference_id": "1",
                                            }
                                        ],
                                        "chunks": [
                                            {
                                                "content": "Neural networks are computational models that mimic the way biological neural networks work...",
                                                "file_path": "/documents/ai_basics.pdf",
                                                "chunk_id": "chunk-123",
                                                "reference_id": "1",
                                            }
                                        ],
                                        "references": [
                                            {
                                                "reference_id": "1",
                                                "file_path": "/documents/ai_basics.pdf",
                                            }
                                        ],
                                    },
                                    "metadata": {
                                        "query_mode": "local",
                                        "keywords": {
                                            "high_level": ["neural", "networks"],
                                            "low_level": [
                                                "computation",
                                                "model",
                                                "algorithm",
                                            ],
                                        },
                                        "processing_info": {
                                            "total_entities_found": 5,
                                            "total_relations_found": 3,
                                            "entities_after_truncation": 1,
                                            "relations_after_truncation": 1,
                                            "final_chunks_count": 1,
                                        },
                                    },
                                },
                            },
                            "global_mode": {
                                "summary": "Global mode data retrieval",
                                "description": "Example of structured data from global mode query analyzing broader patterns",
                                "value": {
                                    "status": "success",
                                    "message": "Query executed successfully",
                                    "data": {
                                        "entities": [],
                                        "relationships": [
                                            {
                                                "src_id": "Artificial Intelligence",
                                                "tgt_id": "Machine Learning",
                                                "description": "AI encompasses machine learning as a core component",
                                                "keywords": "encompasses, component, field",
                                                "weight": 0.92,
                                                "source_id": "chunk-456",
                                                "file_path": "/documents/ai_overview.pdf",
                                                "reference_id": "2",
                                            }
                                        ],
                                        "chunks": [],
                                        "references": [
                                            {
                                                "reference_id": "2",
                                                "file_path": "/documents/ai_overview.pdf",
                                            }
                                        ],
                                    },
                                    "metadata": {
                                        "query_mode": "global",
                                        "keywords": {
                                            "high_level": [
                                                "artificial",
                                                "intelligence",
                                                "overview",
                                            ],
                                            "low_level": [],
                                        },
                                    },
                                },
                            },
                            "naive_mode": {
                                "summary": "Naive mode data retrieval",
                                "description": "Example of structured data from naive mode using only vector search",
                                "value": {
                                    "status": "success",
                                    "message": "Query executed successfully",
                                    "data": {
                                        "entities": [],
                                        "relationships": [],
                                        "chunks": [
                                            {
                                                "content": "Deep learning is a subset of machine learning that uses neural networks with multiple layers...",
                                                "file_path": "/documents/deep_learning.pdf",
                                                "chunk_id": "chunk-789",
                                                "reference_id": "3",
                                            }
                                        ],
                                        "references": [
                                            {
                                                "reference_id": "3",
                                                "file_path": "/documents/deep_learning.pdf",
                                            }
                                        ],
                                    },
                                    "metadata": {
                                        "query_mode": "naive",
                                        "keywords": {"high_level": [], "low_level": []},
                                    },
                                },
                            },
                        },
                    }
                },
            },
            400: {
                "description": "Bad Request - Invalid input parameters",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Query text must be at least 3 characters long"
                        },
                    }
                },
            },
            500: {
                "description": "Internal Server Error - Data retrieval failed",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Failed to retrieve data: Knowledge graph unavailable"
                        },
                    }
                },
            },
        },
    )
    async def query_data(request: QueryRequest):
        """
        Advanced data retrieval endpoint for structured RAG analysis.

        This endpoint provides raw retrieval results without LLM generation, perfect for:
        - **Data Analysis**: Examine what information would be used for RAG
        - **System Integration**: Get structured data for custom processing
        - **Debugging**: Understand retrieval behavior and quality
        - **Research**: Analyze knowledge graph structure and relationships

        **Key Features:**
        - No LLM generation - pure data retrieval
        - Complete structured output with entities, relationships, and chunks
        - Always includes references for citation
        - Detailed metadata about processing and keywords
        - Compatible with all query modes and parameters

        **Query Mode Behaviors:**
        - **local**: Returns entities and their direct relationships + related chunks
        - **global**: Returns relationship patterns across the knowledge graph
        - **hybrid**: Combines local and global retrieval strategies
        - **naive**: Returns only vector-retrieved text chunks (no knowledge graph)
        - **mix**: Integrates knowledge graph data with vector-retrieved chunks
        - **bypass**: Returns empty data arrays (used for direct LLM queries)

        **Data Structure:**
        - **entities**: Knowledge graph entities with descriptions and metadata
        - **relationships**: Connections between entities with weights and descriptions
        - **chunks**: Text segments from documents with source information
        - **references**: Citation information mapping reference IDs to file paths
        - **metadata**: Processing information, keywords, and query statistics

        **Usage Examples:**

        Analyze entity relationships:
        ```json
        {
            "query": "machine learning algorithms",
            "mode": "local",
            "top_k": 10
        }
        ```

        Explore global patterns:
        ```json
        {
            "query": "artificial intelligence trends",
            "mode": "global",
            "max_relation_tokens": 2000
        }
        ```

        Vector similarity search:
        ```json
        {
            "query": "neural network architectures",
            "mode": "naive",
            "chunk_top_k": 5
        }
        ```

        Bypass initial LLM call by providing high-level and low-level keywords:
        ```json
        {
            "query": "What is Retrieval-Augmented-Generation?",
            "hl_keywords": ["machine learning", "information retrieval", "natural language processing"],
            "ll_keywords": ["retrieval augmented generation", "RAG", "knowledge base"],
            "mode": "mix"
        }
        ```

        **Response Analysis:**
        - **Empty arrays**: Normal for certain modes (e.g., naive mode has no entities/relationships)
        - **Processing info**: Shows retrieval statistics and token usage
        - **Keywords**: High-level and low-level keywords extracted from query
        - **Reference mapping**: Links all data back to source documents

        Args:
            request (QueryRequest): The request object containing query parameters:
                - **query**: The search query to analyze (min 3 characters)
                - **mode**: Retrieval strategy affecting data types returned
                - **top_k**: Number of top entities/relationships to retrieve
                - **chunk_top_k**: Number of text chunks to retrieve
                - **max_entity_tokens**: Token limit for entity context
                - **max_relation_tokens**: Token limit for relationship context
                - **max_total_tokens**: Overall token budget for retrieval

        Returns:
            QueryDataResponse: Structured JSON response containing:
                - **status**: "success" or "failure"
                - **message**: Human-readable status description
                - **data**: Complete retrieval results with entities, relationships, chunks, references
                - **metadata**: Query processing information and statistics

        Raises:
            HTTPException:
                - 400: Invalid input parameters (e.g., query too short, invalid mode)
                - 500: Internal processing error (e.g., knowledge graph unavailable)

        Note:
            This endpoint always includes references regardless of the include_references parameter,
            as structured data analysis typically requires source attribution.
        """
        try:
            param = request.to_query_params(False)  # No streaming for data endpoint
            response = await rag.aquery_data(request.query, param=param)

            # aquery_data returns the new format with status, message, data, and metadata
            if isinstance(response, dict):
                return QueryDataResponse(**response)
            else:
                # Handle unexpected response format
                return QueryDataResponse(
                    status="failure",
                    message="Invalid response type",
                    data={},
                )
        except Exception as e:
            logger.error(f"Error processing data query: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    return router
