"""Central contract registry for AgentFlow nodes.

The executable flow, model registry, prompts, validators, and fallback policies
live in different packages by design. This module provides the compact source
of truth that ties those pieces together per node and lets tests detect drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_ROOT = Path(__file__).resolve().parent / "config"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = CONFIG_ROOT / "node_contracts.yaml"
DEFAULT_MODEL_REGISTRY_PATH = REPO_ROOT / "config" / "model_registry.yaml"


class FallbackEventContract(BaseModel):
    """Declared fallback event shape for one node degradation path."""

    model_config = ConfigDict(extra="forbid")

    fallback_type: str = Field(min_length=1)
    quality_risk: str = Field(pattern="^(none|low|medium|high)$")
    visible_to_user: bool = False
    reason: str = Field(min_length=1)


class NodeContract(BaseModel):
    """Machine-readable contract for one runtime generation node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    role: str
    input_schema: list[str] = Field(default_factory=list)
    output_schema: list[str] = Field(default_factory=list)
    prompt_id: str
    prompt_version: str
    model_role: str
    validators: list[str] = Field(default_factory=list)
    repair_policy: str
    fallback_policy: str
    fallback_events: list[FallbackEventContract] = Field(default_factory=list)
    observability_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_operational_fields(self) -> "NodeContract":
        """Fail fast when a node is documented without executable policies."""
        missing: list[str] = []
        if not self.validators:
            missing.append("validators")
        if not self.repair_policy.strip():
            missing.append("repair_policy")
        if not self.fallback_policy.strip():
            missing.append("fallback_policy")
        if not self.fallback_events:
            missing.append("fallback_events")
        if not self.observability_tags:
            missing.append("observability_tags")
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Node contract '{self.node_id}' is missing: {joined}")
        fallback_types = [event.fallback_type for event in self.fallback_events]
        duplicates = sorted({item for item in fallback_types if fallback_types.count(item) > 1})
        if duplicates:
            raise ValueError(f"Node contract '{self.node_id}' has duplicate fallback events: {', '.join(duplicates)}")
        return self

    def trace_metadata(self) -> dict[str, Any]:
        """Return stable metadata that can be attached to node traces."""
        return {
            "node_contract_id": self.node_id,
            "node_role": self.role,
            "prompt_id": self.prompt_id,
            "model_role": self.model_role,
            "validators": list(self.validators),
            "repair_policy": self.repair_policy,
            "fallback_policy": self.fallback_policy,
            "fallback_events": [event.model_dump(mode="json") for event in self.fallback_events],
            "observability_tags": list(self.observability_tags),
            "declared_input_schema": list(self.input_schema),
            "declared_output_schema": list(self.output_schema),
        }


class NodeContractLibrary(BaseModel):
    """Collection of all node contracts for a generation flow."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    contracts: dict[str, NodeContract]

    @model_validator(mode="after")
    def _contract_keys_match_node_ids(self) -> "NodeContractLibrary":
        """Keep YAML keys and embedded node IDs in sync."""
        mismatched = [
            f"{key}!={contract.node_id}"
            for key, contract in self.contracts.items()
            if key != contract.node_id
        ]
        if mismatched:
            raise ValueError(f"Node contract key mismatch: {', '.join(mismatched)}")
        return self


def load_node_contract_library(path: Path | None = None) -> NodeContractLibrary:
    """Load and validate the node contract registry."""
    contract_path = path or DEFAULT_CONTRACT_PATH
    if not contract_path.exists():
        raise FileNotFoundError(f"Node contract config not found: {contract_path}")
    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    return NodeContractLibrary.model_validate(raw)


def load_node_contracts(path: Path | None = None) -> dict[str, NodeContract]:
    """Return node contracts keyed by node_id."""
    return load_node_contract_library(path).contracts


def load_model_roles(path: Path | None = None) -> set[str]:
    """Return model roles defined in the provider registry."""
    registry_path = path or DEFAULT_MODEL_REGISTRY_PATH
    if not registry_path.exists():
        raise FileNotFoundError(f"Model registry config not found: {registry_path}")
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return set((raw.get("roles") or {}).keys())


def validate_contracts_against_flow(flow_nodes: list[Any], contracts: dict[str, NodeContract]) -> list[str]:
    """Compare node contracts with an already loaded FlowDefinition.nodes list."""
    errors: list[str] = []
    flow_by_id = {node.id: node for node in flow_nodes}
    flow_ids = set(flow_by_id)
    contract_ids = set(contracts)

    for missing in sorted(flow_ids - contract_ids):
        errors.append(f"Missing node contract for flow node '{missing}'")
    for extra in sorted(contract_ids - flow_ids):
        errors.append(f"Node contract '{extra}' has no matching flow node")

    for node_id in sorted(flow_ids & contract_ids):
        node = flow_by_id[node_id]
        contract = contracts[node_id]
        if list(node.inputs) != contract.input_schema:
            errors.append(
                f"Node '{node_id}' input_schema drift: flow={list(node.inputs)!r}, "
                f"contract={contract.input_schema!r}"
            )
        if list(node.outputs) != contract.output_schema:
            errors.append(
                f"Node '{node_id}' output_schema drift: flow={list(node.outputs)!r}, "
                f"contract={contract.output_schema!r}"
            )
    return errors


def validate_contract_hardening(
    *,
    flow_nodes: list[Any],
    contracts: dict[str, NodeContract],
    model_roles: set[str],
) -> list[str]:
    """Run all static consistency checks that make node contracts production-useful."""
    errors = validate_contracts_against_flow(flow_nodes, contracts)
    for node_id, contract in sorted(contracts.items()):
        if contract.model_role not in model_roles:
            errors.append(f"Node '{node_id}' uses unknown model_role '{contract.model_role}'")
        if "llm" in contract.observability_tags:
            declared = {event.fallback_type for event in contract.fallback_events}
            if "llm_provider_route_fallback" not in declared:
                errors.append(f"Node '{node_id}' has llm tag but no llm_provider_route_fallback contract")
        if not any(tag in contract.observability_tags for tag in ("deterministic", "llm", "workflow")):
            errors.append(f"Node '{node_id}' has no execution-mode observability tag")
    return errors
