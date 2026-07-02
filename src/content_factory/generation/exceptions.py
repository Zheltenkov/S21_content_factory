"""Backwards-compat shim. The exception hierarchy moved to
content_factory.platform.exceptions during the platform-core unification.
Kept so existing `content_factory.generation.exceptions` importers keep working;
scheduled for removal in the final cleanup phase.
"""

from content_factory.platform.exceptions import *  # noqa: F401,F403
from content_factory.platform.exceptions import (  # noqa: F401  explicit for type checkers
    AgentError,
    AgentGenerationError,
    AgentValidationError,
    ConfigurationError,
    ContentGenerationError,
    LLMAPIError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    ValidationError,
)
