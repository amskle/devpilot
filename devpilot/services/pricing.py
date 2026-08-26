from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from devpilot.services.storage import ArtifactStore


@dataclass(frozen=True)
class ModelPrice:
    prompt_per_million: Decimal
    completion_per_million: Decimal


class PricingCatalog:
    def __init__(self, entries: dict[str, ModelPrice] | None = None):
        self.entries = entries or {}

    @classmethod
    def from_file(cls, path: Path) -> "PricingCatalog":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            {
                name: ModelPrice(Decimal(item["prompt_per_million"]), Decimal(item["completion_per_million"]))
                for name, item in raw.get("models", {}).items()
            }
        )

    def snapshot(self, artifacts: ArtifactStore, task_id: str, run_id: str) -> dict:
        value = {
            "models": {
                name: {
                    "prompt_per_million": str(price.prompt_per_million),
                    "completion_per_million": str(price.completion_per_million),
                }
                for name, price in sorted(self.entries.items())
            }
        }
        return artifacts.put_json(task_id, run_id, "pricing_snapshot", value).to_state_dict()

    def cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> str:
        if model not in self.entries:
            raise ValueError(f"no pricing data for model: {model}")
        price = self.entries[model]
        value = (price.prompt_per_million * prompt_tokens + price.completion_per_million * completion_tokens) / Decimal(1_000_000)
        return format(value.quantize(Decimal("0.0001")), "f")
