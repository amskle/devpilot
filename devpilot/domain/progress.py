from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProgressSignals:
    made_progress: bool
    same_symptom: bool
    repeated_change: bool
    symptom_aba: bool
    change_aba: bool


def evaluate_progress_signals(entries: list[dict[str, Any]], current: dict[str, Any]) -> ProgressSignals:
    symptom = current.get("symptom_fingerprint")
    change = current.get("change_fingerprint")
    same_symptom = bool(entries) and entries[-1].get("symptom_fingerprint") == symptom
    repeated_change = bool(entries) and entries[-1].get("change_fingerprint") == change
    with_current = [*entries, current]
    symptom_aba = len(with_current) >= 3 and with_current[-1].get("symptom_fingerprint") == with_current[-3].get("symptom_fingerprint")
    change_aba = len(with_current) >= 3 and with_current[-1].get("change_fingerprint") == with_current[-3].get("change_fingerprint")
    made_progress = bool(entries) and not same_symptom and not symptom_aba and not change_aba
    return ProgressSignals(made_progress, same_symptom, repeated_change, symptom_aba, change_aba)
