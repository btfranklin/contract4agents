"""Runtime error types for Contract4Agents internals."""

from __future__ import annotations


class ContractRuntimeError(Exception):
    pass


class MissingContextSlot(ContractRuntimeError):
    def __init__(self, type_name: str) -> None:
        super().__init__(f"Missing context slot: {type_name}")
        self.type_name = type_name


class AmbiguousDatasource(ContractRuntimeError):
    def __init__(self, type_name: str, candidates: list[str]) -> None:
        super().__init__(f"Ambiguous datasources for {type_name}: {', '.join(candidates)}")
        self.type_name = type_name
        self.candidates = candidates


class DatasourcePermissionDenied(ContractRuntimeError):
    def __init__(self, type_name: str) -> None:
        super().__init__(f"Datasource permission denied for context slot: {type_name}")
        self.type_name = type_name


class DatasourceResolutionCycle(ContractRuntimeError):
    def __init__(self, cycle: tuple[str, ...]) -> None:
        super().__init__(f"Datasource resolution cycle: {' -> '.join(cycle)}")
        self.cycle = cycle


class DatasourceExecutionFailed(ContractRuntimeError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"Datasource {name} failed: {reason}")
        self.name = name
        self.reason = reason


class ToolExecutionFailed(ContractRuntimeError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"Tool {name} failed: {reason}")
        self.name = name
        self.reason = reason


class ToolPermissionDenied(ContractRuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Tool permission denied: {name}")
        self.name = name


__all__ = [
    "AmbiguousDatasource",
    "ContractRuntimeError",
    "DatasourceExecutionFailed",
    "DatasourcePermissionDenied",
    "DatasourceResolutionCycle",
    "MissingContextSlot",
    "ToolExecutionFailed",
    "ToolPermissionDenied",
]
