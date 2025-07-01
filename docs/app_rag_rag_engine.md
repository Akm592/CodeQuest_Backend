# `app/rag/rag_engine.py` Documentation

## Overview

The `app/rag/rag_engine.py` module provides a basic implementation of a Retrieval-Augmented Generation (RAG) engine. It includes functions for retrieving relevant context from a knowledge base and generating a response using that context.

## Key Components

### `KNOWLEDGE_BASE`
- **Purpose**: A simple in-memory dictionary that serves as the knowledge base for the RAG engine.

### `retrieve_relevant_context(query: str) -> Optional[str]`
- **Purpose**: Retrieves relevant context from the knowledge base based on a user query.

### `generate_rag_response(...)`
- **Purpose**: Generates a response using the RAG prompt, retrieved context, and conversation history.
