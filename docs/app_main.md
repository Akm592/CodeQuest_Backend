# `app/main.py` Documentation

## Overview

The `app/main.py` file is the entry point for the FastAPI application. It initializes the web service, configures middleware, includes API routers, and manages application lifecycle events.

## Key Components

### `app = FastAPI(title="CodeQuest101 Chatbot Backend")`
- **Purpose**: Initializes the FastAPI application instance.

### `CORSMiddleware`
- **Purpose**: Enables Cross-Origin Resource Sharing (CORS) to allow web browsers to make requests from different origins.

### API Routers
- **Purpose**: Integrates different parts of the API, defined in separate modules, into the main application.

### `@app.on_event("startup")`
- **Purpose**: An event handler that executes code when the application starts up.
- **Details**:
    - It initializes the Supabase client.

### `@app.on_event("shutdown")`
- **Purpose**: An event handler that executes code when the application is shutting down.
