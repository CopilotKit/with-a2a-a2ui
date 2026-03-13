# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a full-stack AI agent application combining **CopilotKit**, **A2A (Agent-to-Agent)**, and **A2UI (Agent-to-UI)** frameworks. The application demonstrates a restaurant finder agent that dynamically generates UI components based on LLM responses.

**Stack:**
- Frontend: Next.js 16 with React 19, Tailwind CSS 4
- Backend Agent: Python 3.13+ with Google ADK (Agent Development Kit)
- LLM Provider: OpenAI o1-mini (reasoning model with tool support)
- Agent Framework: A2A SDK for agent communication
- UI Protocol: A2UI for declarative UI generation
- Package Manager: Any (pnpm/npm/yarn/bun) for Node.js, `uv` for Python

## Development Commands

### Starting the Application

```bash
# Start both UI and agent servers concurrently (recommended)
pnpm dev

# Start with debug logging
pnpm dev:debug

# Start only the Next.js UI (port 3000)
pnpm dev:ui

# Start only the Python agent server (port 10002)
pnpm dev:agent
```

### Building and Deployment

```bash
# Build Next.js for production
pnpm build

# Start production server
pnpm start

# Lint code
pnpm lint
```

### Python Agent Development

```bash
# Install/sync Python dependencies manually
cd agent
uv sync

# Run agent directly
uv run .

# Install Python deps from root (handled by postinstall)
pnpm install:agent
```

## Architecture

### Two-Server Architecture

The application runs two concurrent servers:

1. **Next.js Frontend** (port 3000)
   - Located in `app/` directory
   - Main entry: `app/page.tsx` - renders CopilotChat with A2UI renderer
   - API route: `app/api/copilotkit/[[...slug]]/route.tsx` - CopilotKit endpoint that connects to A2A agent

2. **Python Agent Server** (port 10002)
   - Located in `agent/` directory
   - Entry point: `agent/__main__.py` - A2A server with Starlette/uvicorn
   - Agent logic: `agent/agent.py` - RestaurantAgent with Google ADK/Gemini
   - Tools: `agent/tools.py` - get_restaurants tool
   - UI templates: `agent/prompt_builder.py` - A2UI component schemas and examples

### Communication Flow

```
User → Next.js UI → CopilotKit Runtime → A2A Client (localhost:10002)
  → A2A Server → RestaurantAgentExecutor → RestaurantAgent (Google ADK + OpenAI o1-mini)
    → LLM generates A2UI JSON → Validated against schema → Rendered in UI
```

### Key Components

**Frontend (`app/`):**
- `page.tsx`: CopilotKitProvider with A2UIMessageRenderer
- `api/copilotkit/[[...slug]]/route.tsx`: Creates A2AAgent pointing to localhost:10002
- `theme.ts`: A2UI theme configuration (colors, fonts, spacing)

**Agent (`agent/`):**
- `__main__.py`: A2A server setup with CORS, static file serving, agent card definition
- `agent_executor.py`: RestaurantAgentExecutor - handles UI/text mode switching, processes user actions (book_restaurant, submit_booking)
- `agent.py`: RestaurantAgent - wraps Google ADK LlmAgent with OpenAI o1-mini, validates A2UI JSON responses, implements retry logic
- `prompt_builder.py`: Contains A2UI_SCHEMA and RESTAURANT_UI_EXAMPLES (single column list, two column list, booking form, confirmation)
- `tools.py`: get_restaurants tool that loads from restaurant_data.json
- `restaurant_data.json`: Mock restaurant data

**A2UI Extension (`a2ui_extension/`):**
- Custom A2UI extension for the agent (workspace dependency)

### A2UI Architecture

The agent generates declarative UI using A2UI protocol:

1. **UI Templates**: Defined in `agent/prompt_builder.py` as examples (SINGLE_COLUMN_LIST_EXAMPLE, TWO_COLUMN_LIST_EXAMPLE, BOOKING_FORM_EXAMPLE, CONFIRMATION_EXAMPLE)
2. **Schema Validation**: A2UI_SCHEMA defines valid component types (Text, Image, Button, Card, Row, Column, List, etc.)
3. **Component Generation**: LLM generates JSON matching schema with three message types:
   - `beginRendering`: Initialize surface with root component
   - `surfaceUpdate`: Define components with IDs and hierarchical structure
   - `dataModelUpdate`: Populate data model with actual content
4. **Rendering**: Frontend A2UIMessageRenderer converts JSON to React components

### Agent Response Format

Agent responses split into two parts with `---a2ui_JSON---` delimiter:
1. Text response (conversational)
2. JSON array of A2UI messages (validated against schema)

## Environment Configuration

Create `agent/.env`:
```
# OpenRouter API Key (recommended - access to multiple providers)
# Get your API key from: https://openrouter.ai/keys
OPENROUTER_API_KEY=your-openrouter-api-key-here

# OpenAI API Key (optional - for direct OpenAI access)
# Get your API key from: https://platform.openai.com/api-keys
OPENAI_API_KEY=your-openai-api-key-here

# Google API Key (optional - for direct Google access)
# Get your API key from: https://aistudio.google.com/apikey
GOOGLE_API_KEY=your-google-api-key-here

# Model selection (default: openrouter/google/gemini-2.0-flash-thinking-exp:free)
#
# OpenRouter models (use openrouter/ prefix):
# Free models with TOOL CALLING support (REQUIRED for this app):
#   - openrouter/google/gemini-2.0-flash-thinking-exp:free (RECOMMENDED - Google, reasoning + tools)
#   - openrouter/qwen/qwq-32b-preview:free (Qwen reasoning model with tools)
#   - openrouter/mistralai/mistral-small-3.1:free (Mistral Small, function calling)
#   - openrouter/google/gemini-2.0-flash-exp:free (Google Gemini 2.0, may have rate limits)
#
# ⚠️ Models WITHOUT tool support (DO NOT USE):
#   - openrouter/deepseek/deepseek-r1-* (no tool calling support)
#   - openrouter/deepseek/deepseek-chat:free (no tool calling support)
# Paid models:
#   - openrouter/openai/gpt-4o (OpenAI GPT-4o via OpenRouter)
#   - openrouter/anthropic/claude-3.5-sonnet (Claude 3.5 Sonnet)
#   - openrouter/meta-llama/llama-3.1-70b-instruct (Meta Llama 3.1)
#
# Direct provider models (requires respective API keys):
#   - OpenAI: o1-mini, gpt-4o, gpt-4o-mini, gpt-4-turbo
#   - Google: gemini/gemini-2.0-flash-exp, gemini/gemini-2.5-flash-lite
#   - Perplexity (NO tool support): perplexity/llama-3.1-sonar-large-128k-online
LITELLM_MODEL=openrouter/google/gemini-2.0-flash-exp:free

# Retry and timeout configuration
LITELLM_NUM_RETRIES=3
LITELLM_TIMEOUT=60
```

> **Note:** Get your OpenRouter API key from [https://openrouter.ai/keys](https://openrouter.ai/keys) (recommended) or OpenAI key from [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)

## File Locations

- Next.js pages/components: `app/`
- Agent server: `agent/`
- Python dependencies: `agent/pyproject.toml`
- Node dependencies: `package.json`
- Scripts: `scripts/` (setup-agent.sh, run-agent.sh)
- A2UI extension: `a2ui_extension/src/a2ui/`
- Static assets (restaurant images): `agent/images/`

## Workspace Structure

UV workspace with two members:
- `agent/` - Main agent package (a2ui-restaurant-finder)
- `a2ui_extension/` - Custom A2UI extension

Both managed via root `pyproject.toml` workspace configuration.

## Common Workflows

### Adding New UI Components

1. Design component structure in A2UI format
2. Add example template to `agent/prompt_builder.py` in RESTAURANT_UI_EXAMPLES
3. Update agent instructions in `agent/agent.py` to use new template
4. Optionally use [A2UI Composer](https://a2ui-editor.ag-ui.com) to generate components

### Adding New Agent Tools

1. Define tool function in `agent/tools.py` with Google ADK signature
2. Add tool to `tools` list in `agent/agent.py` LlmAgent initialization
3. Update AGENT_INSTRUCTION to document tool usage

### Modifying Theme

Edit `app/theme.ts` to customize:
- Colors (primary, secondary, accent, background)
- Fonts
- Spacing
- Component styling

## Troubleshooting

**Agent connection errors:**
- Verify agent server is running on port 10002
- Check OPENAI_API_KEY is set in `agent/.env`
- Verify LITELLM_MODEL is set to a valid model (default: o1-mini)
- Ensure both servers started successfully

**Python import errors:**
```bash
cd agent
uv sync
```

**Port conflicts:**
- UI runs on port 3000 (configurable with Next.js)
- Agent runs on port 10002 (configurable with --port flag in `agent/__main__.py`)
