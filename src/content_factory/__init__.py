"""content_factory — unified School 21 content platform.

Subpackages:
  - api:        FastAPI application (routers, db, services, integrations)
  - generation: content generation engine (ex "content_gen")
  - utils:      shared helpers (token counting, excel io)
  - config:     packaged configuration data (model_registry.yaml, ...)

Audit (ex-Proverka) and catalog (ex-Spravochnik) are folded in during later
migration phases.
"""

__version__ = "0.1.0"
