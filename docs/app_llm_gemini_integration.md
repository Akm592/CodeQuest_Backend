# `app/llm/gemini_integration.py` Documentation

## Overview

The `app/llm/gemini_integration.py` module handles all interactions with the Gemini Large Language Model (LLM). It provides functions for generating chat responses, streaming responses, and generating visualization data.

## Key Components

### Model Configurations
- **`visualization_config`**: A dictionary containing the generation configuration for the visualization model.
- **`chat_config`**: A dictionary containing the generation configuration for the chat model.

### Model Initialization
- **`visualization_model`**: An instance of the `GenerativeModel` for generating visualization data.
- **`chat_model`**: An instance of the `GenerativeModel` for generating chat responses.

### `clean_json_response(raw_text: str) -> str`
- **Purpose**: Extracts a JSON string from the model's raw text response.

### `get_visualization_data(user_query: str) -> Optional[Dict[str, Any]]`
- **Purpose**: Generates visualization data based on a user query.

### `get_chat_response(...)`
- **Purpose**: Generates a full text response from the chat model (non-streaming).

### `stream_chat_response(...)`
- **Purpose**: Streams a text response from the chat model.

### `get_contextual_visualization_data(...)`
- **Purpose**: Generates visualization data with conversation and example context.
