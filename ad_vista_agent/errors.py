"""Stable error categories used at application boundaries."""

from __future__ import annotations


class AdVistaError(RuntimeError):
    code = "internal_error"
    retryable = True


class PlanError(AdVistaError):
    code = "output_invalid"


class GroundingError(AdVistaError):
    code = "grounding_failed"


class ToolError(AdVistaError):
    code = "tool_failed"


class ModelUnavailableError(AdVistaError):
    code = "model_unavailable"


class ModelTimeoutError(ModelUnavailableError):
    code = "model_timeout"


class InvalidVideoError(AdVistaError):
    code = "invalid_video"
    retryable = False
