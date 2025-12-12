# LightRAG Docker Deployment Guide (PostgreSQL + Local Models)

This guide explains how to deploy LightRAG with PostgreSQL storage and local Embedding/Rerank models using Docker Compose.

## Prerequisites

1.  **Docker & Docker Compose**: Ensure they are installed on your GPU server.
2.  **Local Model Server**: You must have a local model server running (e.g., Infinity, Xinference, or similar) that provides:
    -   **Embeddings Endpoint**: `http://localhost:9997/v1/embeddings` (Model: `BAAI/bge-m3`)
    -   **Rerank Endpoint**: `http://localhost:9997/v1/rerank` (Model: `BAAI/bge-reranker-v2-m3`)
    -   *Note*: The `docker-compose.yml` assumes these services are running on the host machine (`host.docker.internal`).

## Configuration

The `docker-compose.yml` file has been configured to:
-   Use `daoyu/pg_vector_age:latest` for PostgreSQL (supports Vector and Graph storage).
-   Initialize PostgreSQL with `vector` and `age` extensions.
-   Configure LightRAG to use PostgreSQL for all storage types (KV, Vector, Graph, DocStatus).
-   Point LightRAG to your local model endpoints.

**Note on `.env` file**:
The setup reads the existing `.env` file for LLM configuration (`LLM_BINDING`, `LLM_API_KEY`, etc.). Ensure your `.env` file contains valid LLM settings. If you are also using a local LLM, make sure `LLM_BINDING_HOST` in `.env` points to a reachable address (e.g., `http://host.docker.internal:11434` for Ollama).

## Deployment Steps

1.  **Start Services**:
    Run the following command in the project root:
    ```bash
    docker-compose up -d --build
    ```

2.  **Verify Deployment**:
    -   Check logs to ensure LightRAG started successfully:
        ```bash
        docker-compose logs -f lightrag
        ```
    -   Access the API at `http://localhost:9621/docs`.

3.  **Data Persistence**:
    -   PostgreSQL data is stored in `./postgresql-data`.
    -   LightRAG local files (if any) are stored in `./rag_storage`.

## Troubleshooting

-   **Connection Refused (Model Server)**:
    If LightRAG cannot connect to the model server (port 9997), ensure the server is listening on all interfaces or that `host.docker.internal` is correctly resolving. On Linux, `extra_hosts` in `docker-compose.yml` handles this, but your firewall must allow the connection.

-   **PostgreSQL Extensions**:
    The `init-db.sql` script initializes `vector` and `age` extensions. If you see extension errors, check the postgres logs:
    ```bash
    docker-compose logs postgres
    ```
