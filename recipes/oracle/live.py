"""Configured read-only Terac study attachment for Oracle."""

from collections.abc import Callable

from adapters.terac_study_contract import StudyReadError
from recipes.oracle.live_contract import (
    OracleLiveBinding,
    is_safe_provider_observation,
    validate_live_progress,
)
from runtime.runner import RunState


class OracleLiveStudyAdapter:
    """Read progress for one exact study; it cannot create work or deliver feedback."""

    def __init__(
        self,
        binding: OracleLiveBinding,
        credential_provider: Callable[[], str | None],
        *,
        transport_factory: Callable[[], object] | None = None,
    ):
        if not isinstance(binding, OracleLiveBinding) or not callable(credential_provider):
            raise StudyReadError("invalid_binding")
        if transport_factory is not None and not callable(transport_factory):
            raise StudyReadError("invalid_binding")
        self.binding = binding
        self.credential_provider = credential_provider
        self.transport_factory = transport_factory

    def attach(self, brief: str) -> dict:
        import hashlib

        if (
            not isinstance(brief, str)
            or hashlib.sha256(brief.encode("utf-8")).hexdigest() != self.binding.brief_sha256
        ):
            raise StudyReadError("binding_mismatch")
        return {"binding": self.binding.checkpoint_value(), "observation": None}

    def observe(self, state: RunState) -> dict:
        if state.adapter_payload.get("binding") != self.binding.checkpoint_value():
            raise StudyReadError("binding_mismatch")
        try:
            token = self.credential_provider()
        except Exception:
            raise StudyReadError("credential_unavailable") from None
        if not isinstance(token, str) or not token:
            raise StudyReadError("credential_unavailable")
        try:
            from adapters.terac_study_read import read_study_sync

            transport = self.transport_factory() if self.transport_factory is not None else None
            observation = read_study_sync(
                self.binding.provider_binding(), token, transport=transport
            )
        except StudyReadError:
            raise
        except Exception:
            raise StudyReadError("transport_or_protocol") from None
        return self._safe_observation(observation)

    @staticmethod
    def _safe_observation(observation) -> dict:
        return {
            "status": observation.status,
            "num_participants": observation.num_participants,
            "total_cost_cents": observation.total_cost_cents,
            "cost_per_participant_cents": observation.cost_per_participant_cents,
            "currency": observation.currency,
        }

    def validate_progress(self, state: RunState) -> None:
        validate_live_progress(state)
        if state.adapter_payload["binding"] != self.binding.checkpoint_value():
            raise ValueError("Configured study does not match the saved run")

    @staticmethod
    def is_safe_observation(value: object) -> bool:
        return is_safe_provider_observation(value)
