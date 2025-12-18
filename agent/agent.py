# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os
import time
from collections.abc import AsyncIterable
from typing import Any

import jsonschema
import litellm
from google.adk.agents.llm_agent import LlmAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from prompt_builder import (
    A2UI_SCHEMA,
    RESTAURANT_UI_EXAMPLES,
    get_text_prompt,
    get_ui_prompt,
)
from tools import get_restaurants

logger = logging.getLogger(__name__)

# Error handling configuration
RATE_LIMIT_RETRY_DELAY = 30  # seconds to wait before retrying after rate limit
MAX_RATE_LIMIT_RETRIES = 3  # maximum number of retries for rate limit errors
GENERAL_ERROR_RETRY_DELAY = 5  # seconds to wait for general errors

AGENT_INSTRUCTION = """
    You are a helpful restaurant finding assistant. Your goal is to help users find and book restaurants using a rich UI.

    To achieve this, you MUST follow this logic:

    1.  **For finding restaurants:**
        a. You MUST call the `get_restaurants` tool. Extract the cuisine, location, and a specific number (`count`) of restaurants from the user's query (e.g., for "top 5 chinese places", count is 5).
        b. After receiving the data, you MUST follow the instructions precisely to generate the final a2ui UI JSON, using the appropriate UI example from the `prompt_builder.py` based on the number of restaurants.

    2.  **For booking a table (when you receive a query like 'USER_WANTS_TO_BOOK...'):**
        a. You MUST use the appropriate UI example from `prompt_builder.py` to generate the UI, populating the `dataModelUpdate.contents` with the details from the user's query.

    3.  **For confirming a booking (when you receive a query like 'User submitted a booking...'):**
        a. You MUST use the appropriate UI example from `prompt_builder.py` to generate the confirmation UI, populating the `dataModelUpdate.contents` with the final booking details.
"""


class RestaurantAgent:
    """An agent that finds restaurants based on user criteria."""

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(self, base_url: str, use_ui: bool = False):
        self.base_url = base_url
        self.use_ui = use_ui
        self._agent = self._build_agent(use_ui)
        self._user_id = "remote_agent"
        self._runner = Runner(
            app_name=self._agent.name,
            agent=self._agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

        # --- MODIFICATION: Wrap the schema ---
        # Load the A2UI_SCHEMA string into a Python object for validation
        try:
            # First, load the schema for a *single message*
            single_message_schema = json.loads(A2UI_SCHEMA)

            # The prompt instructs the LLM to return a *list* of messages.
            # Therefore, our validation schema must be an *array* of the single message schema.
            self.a2ui_schema_object = {"type": "array", "items": single_message_schema}
            logger.info(
                "A2UI_SCHEMA successfully loaded and wrapped in an array validator."
            )
        except json.JSONDecodeError as e:
            logger.error(f"CRITICAL: Failed to parse A2UI_SCHEMA: {e}")
            self.a2ui_schema_object = None
        # --- END MODIFICATION ---

    def get_processing_message(self) -> str:
        return "Finding restaurants that match your criteria..."

    def _build_agent(self, use_ui: bool) -> LlmAgent:
        """Builds the LLM agent for the restaurant agent."""
        LITELLM_MODEL = os.getenv("LITELLM_MODEL", "openrouter/google/gemini-2.0-flash-exp:free")

        if use_ui:
            # Construct the full prompt with UI instructions, examples, and schema
            instruction = AGENT_INSTRUCTION + get_ui_prompt(
                self.base_url, RESTAURANT_UI_EXAMPLES
            )
        else:
            instruction = get_text_prompt()

        return LlmAgent(
            model=LiteLlm(model=LITELLM_MODEL),
            name="restaurant_agent",
            description="An agent that finds restaurants and helps book tables.",
            instruction=instruction,
            tools=[get_restaurants],
        )

    async def stream(self, query, session_id) -> AsyncIterable[dict[str, Any]]:
        session_state = {"base_url": self.base_url}

        session = await self._runner.session_service.get_session(
            app_name=self._agent.name,
            user_id=self._user_id,
            session_id=session_id,
        )
        if session is None:
            session = await self._runner.session_service.create_session(
                app_name=self._agent.name,
                user_id=self._user_id,
                state=session_state,
                session_id=session_id,
            )
        elif "base_url" not in session.state:
            session.state["base_url"] = self.base_url

        # --- Begin: UI Validation and Retry Logic ---
        max_retries = 1  # Total 2 attempts
        attempt = 0
        current_query_text = query
        rate_limit_retry_count = 0

        # Ensure schema was loaded
        if self.use_ui and self.a2ui_schema_object is None:
            logger.error(
                "--- RestaurantAgent.stream: A2UI_SCHEMA is not loaded. "
                "Cannot perform UI validation. ---"
            )
            yield {
                "is_task_complete": True,
                "content": (
                    "I'm sorry, I'm facing an internal configuration error with my UI components. "
                    "Please contact support."
                ),
            }
            return

        while attempt <= max_retries:
            attempt += 1
            logger.info(
                f"--- RestaurantAgent.stream: Attempt {attempt}/{max_retries + 1} "
                f"for session {session_id} ---"
            )

            current_message = types.Content(
                role="user", parts=[types.Part.from_text(text=current_query_text)]
            )
            final_response_content = None

            try:
                async for event in self._runner.run_async(
                    user_id=self._user_id,
                    session_id=session.id,
                    new_message=current_message,
                ):
                    logger.info(f"Event from runner: {event}")
                    if event.is_final_response():
                        if (
                            event.content
                            and event.content.parts
                            and event.content.parts[0].text
                        ):
                            final_response_content = "\n".join(
                                [p.text for p in event.content.parts if p.text]
                            )
                        break  # Got the final response, stop consuming events
                    else:
                        logger.info(f"Intermediate event: {event}")
                        # Yield intermediate updates on every attempt
                        yield {
                            "is_task_complete": False,
                            "updates": self.get_processing_message(),
                        }

                # Reset rate limit counter on success
                rate_limit_retry_count = 0

            except litellm.RateLimitError as e:
                rate_limit_retry_count += 1
                logger.error(
                    f"--- RestaurantAgent.stream: Rate limit error (attempt {rate_limit_retry_count}/{MAX_RATE_LIMIT_RETRIES}): {e} ---"
                )

                if rate_limit_retry_count <= MAX_RATE_LIMIT_RETRIES:
                    retry_after = RATE_LIMIT_RETRY_DELAY

                    # Try to extract retry-after from error message if available
                    error_msg = str(e)
                    if "retry after" in error_msg.lower():
                        try:
                            # Try to parse retry-after time from error message
                            import re
                            match = re.search(r'retry.*?(\d+)', error_msg, re.IGNORECASE)
                            if match:
                                retry_after = int(match.group(1))
                        except:
                            pass

                    yield {
                        "is_task_complete": False,
                        "updates": (
                            f"The AI service is currently experiencing high demand. "
                            f"Retrying in {retry_after} seconds... "
                            f"(Attempt {rate_limit_retry_count}/{MAX_RATE_LIMIT_RETRIES})"
                        ),
                    }

                    logger.info(f"Waiting {retry_after} seconds before retry...")
                    time.sleep(retry_after)

                    # Don't increment attempt counter for rate limits, just retry
                    attempt -= 1
                    continue
                else:
                    logger.error("--- Max rate limit retries exceeded ---")
                    yield {
                        "is_task_complete": True,
                        "content": (
                            "I apologize, but the AI service is currently experiencing very high demand "
                            "and is temporarily rate-limited. Please try again in a few minutes. "
                            "\n\nAlternatively, you can:\n"
                            "1. Wait a few minutes and try again\n"
                            "2. Check if your API provider has rate limit restrictions\n"
                            "3. Consider using a different model in your .env configuration"
                        ),
                    }
                    return

            except litellm.APIConnectionError as e:
                logger.error(f"--- RestaurantAgent.stream: API connection error: {e} ---")
                yield {
                    "is_task_complete": True,
                    "content": (
                        "I'm sorry, I'm having trouble connecting to the AI service. "
                        "Please check your internet connection and try again."
                    ),
                }
                return

            except litellm.AuthenticationError as e:
                logger.error(f"--- RestaurantAgent.stream: Authentication error: {e} ---")
                yield {
                    "is_task_complete": True,
                    "content": (
                        "Authentication error: Please check your API key configuration in the .env file. "
                        "Make sure OPENROUTER_API_KEY or OPENAI_API_KEY is set correctly."
                    ),
                }
                return

            except litellm.InvalidRequestError as e:
                logger.error(f"--- RestaurantAgent.stream: Invalid request error: {e} ---")
                yield {
                    "is_task_complete": True,
                    "content": (
                        "I'm sorry, there was an issue with the request. "
                        "This might be due to an invalid model configuration or request parameters. "
                        f"Error: {str(e)}"
                    ),
                }
                return

            except litellm.ContextWindowExceededError as e:
                logger.error(f"--- RestaurantAgent.stream: Context window exceeded: {e} ---")
                yield {
                    "is_task_complete": True,
                    "content": (
                        "I'm sorry, the conversation has become too long for the AI model to process. "
                        "Please start a new conversation or try with a shorter query."
                    ),
                }
                return

            except (litellm.APIError, litellm.ServiceUnavailableError, litellm.InternalServerError) as e:
                logger.error(f"--- RestaurantAgent.stream: API service error: {e} ---")

                if attempt <= max_retries:
                    yield {
                        "is_task_complete": False,
                        "updates": (
                            f"The AI service encountered a temporary error. "
                            f"Retrying in {GENERAL_ERROR_RETRY_DELAY} seconds..."
                        ),
                    }
                    time.sleep(GENERAL_ERROR_RETRY_DELAY)
                    continue
                else:
                    yield {
                        "is_task_complete": True,
                        "content": (
                            "I'm sorry, the AI service is currently experiencing technical difficulties. "
                            "Please try again in a few moments."
                        ),
                    }
                    return

            except Exception as e:
                logger.error(f"--- RestaurantAgent.stream: Unexpected error: {type(e).__name__}: {e} ---")
                yield {
                    "is_task_complete": True,
                    "content": (
                        f"I'm sorry, an unexpected error occurred: {type(e).__name__}. "
                        "Please try again or contact support if the issue persists."
                    ),
                }
                return

            if final_response_content is None:
                logger.warning(
                    f"--- RestaurantAgent.stream: Received no final response content from runner "
                    f"(Attempt {attempt}). ---"
                )
                if attempt <= max_retries:
                    current_query_text = (
                        "I received no response. Please try again."
                        f"Please retry the original request: '{query}'"
                    )
                    continue  # Go to next retry
                else:
                    # Retries exhausted on no-response
                    final_response_content = "I'm sorry, I encountered an error and couldn't process your request."
                    # Fall through to send this as a text-only error

            is_valid = False
            error_message = ""

            if self.use_ui:
                logger.info(
                    f"--- RestaurantAgent.stream: Validating UI response (Attempt {attempt})... ---"
                )
                try:
                    if "---a2ui_JSON---" not in final_response_content:
                        raise ValueError("Delimiter '---a2ui_JSON---' not found.")

                    text_part, json_string = final_response_content.split(
                        "---a2ui_JSON---", 1
                    )

                    if not json_string.strip():
                        raise ValueError("JSON part is empty.")

                    json_string_cleaned = (
                        json_string.strip().lstrip("```json").rstrip("```").strip()
                    )

                    if not json_string_cleaned:
                        raise ValueError("Cleaned JSON string is empty.")

                    # --- New Validation Steps ---
                    # 1. Check if it's parsable JSON
                    parsed_json_data = json.loads(json_string_cleaned)

                    # 2. Check if it validates against the A2UI_SCHEMA
                    # This will raise jsonschema.exceptions.ValidationError if it fails
                    logger.info(
                        "--- RestaurantAgent.stream: Validating against A2UI_SCHEMA... ---"
                    )
                    jsonschema.validate(
                        instance=parsed_json_data, schema=self.a2ui_schema_object
                    )
                    # --- End New Validation Steps ---

                    logger.info(
                        f"--- RestaurantAgent.stream: UI JSON successfully parsed AND validated against schema. "
                        f"Validation OK (Attempt {attempt}). ---"
                    )
                    is_valid = True

                except (
                    ValueError,
                    json.JSONDecodeError,
                    jsonschema.exceptions.ValidationError,
                ) as e:
                    logger.warning(
                        f"--- RestaurantAgent.stream: A2UI validation failed: {e} (Attempt {attempt}) ---"
                    )
                    logger.warning(
                        f"--- Failed response content: {final_response_content[:500]}... ---"
                    )
                    error_message = f"Validation failed: {e}."

            else:  # Not using UI, so text is always "valid"
                is_valid = True

            if is_valid:
                logger.info(
                    f"--- RestaurantAgent.stream: Response is valid. Sending final response (Attempt {attempt}). ---"
                )
                logger.info(f"Final response: {final_response_content}")
                yield {
                    "is_task_complete": True,
                    "content": final_response_content,
                }
                return  # We're done, exit the generator

            # --- If we're here, it means validation failed ---

            if attempt <= max_retries:
                logger.warning(
                    f"--- RestaurantAgent.stream: Retrying... ({attempt}/{max_retries + 1}) ---"
                )
                # Prepare the query for the retry
                current_query_text = (
                    f"Your previous response was invalid. {error_message} "
                    "You MUST generate a valid response that strictly follows the A2UI JSON SCHEMA. "
                    "The response MUST be a JSON list of A2UI messages. "
                    "Ensure the response is split by '---a2ui_JSON---' and the JSON part is well-formed. "
                    f"Please retry the original request: '{query}'"
                )
                # Loop continues...

        # --- If we're here, it means we've exhausted retries ---
        logger.error(
            "--- RestaurantAgent.stream: Max retries exhausted. Sending text-only error. ---"
        )
        yield {
            "is_task_complete": True,
            "content": (
                "I'm sorry, I'm having trouble generating the interface for that request right now. "
                "Please try again in a moment."
            ),
        }
        # --- End: UI Validation and Retry Logic ---
