from datetime import timedelta
from propcache.api import cached_property
from typing import Optional, Any

from homeassistant.components.timer import TimerEntity, TimerState, TimerEntityFeature
from homeassistant.util import dt as dt_util
from gehomesdk import ErdCodeType

from ...const import DOMAIN
from ...devices import ApplianceApi
from .ge_entity import GeEntity


class GeApplianceCycleTimer(GeEntity, TimerEntity):
    """Timer entity that snapshots cycle duration when an appliance starts running."""

    def __init__(
        self,
        api: ApplianceApi,
        state_erd_code: ErdCodeType,
        time_remaining_erd_code: ErdCodeType,
        name_suffix: str = "Cycle Timer",
        unique_suffix: str = "cycle_timer",
    ):
        super().__init__(api)
        self._state_erd_code = api.appliance.translate_erd_code(state_erd_code)
        self._time_remaining_erd_code = api.appliance.translate_erd_code(time_remaining_erd_code)
        self._name_suffix = name_suffix
        self._unique_suffix = unique_suffix

        self._state = TimerState.IDLE
        self._duration: Optional[timedelta] = None
        self._started_at = None
        self._last_source_remaining: Optional[timedelta] = None

        self._attr_supported_features = TimerEntityFeature(0)

    @cached_property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.serial_or_mac}_{self._unique_suffix}"

    @cached_property
    def name(self) -> str:
        return f"{self.serial_or_mac} {self._name_suffix}"

    @property
    def icon(self) -> str | None:
        return "mdi:timer-play-outline"

    @property
    def state(self) -> TimerState | None:
        self._refresh_from_appliance()
        return self._state

    @property
    def duration(self) -> timedelta | None:
        self._refresh_from_appliance()
        return self._duration

    @property
    def remaining(self) -> timedelta | None:
        self._refresh_from_appliance()
        if self._state != TimerState.ACTIVE or self._duration is None or self._started_at is None:
            return None

        elapsed = dt_util.utcnow() - self._started_at
        remaining = self._duration - elapsed
        return max(remaining, timedelta(seconds=0))

    @property
    def finishes_at(self):
        rem = self.remaining
        if rem is None:
            return None
        return dt_util.utcnow() + rem

    async def async_start(self, duration: timedelta | None = None) -> None:
        if duration is not None:
            self._duration = duration
        if self._duration is None:
            return
        self._started_at = dt_util.utcnow()
        self._state = TimerState.ACTIVE

    async def async_pause(self) -> None:
        if self._state != TimerState.ACTIVE:
            return
        remaining = self.remaining
        self._duration = remaining
        self._started_at = None
        self._state = TimerState.PAUSED

    async def async_cancel(self) -> None:
        self._state = TimerState.IDLE
        self._duration = None
        self._started_at = None

    async def async_finish(self) -> None:
        self._state = TimerState.IDLE
        self._duration = None
        self._started_at = None

    def _refresh_from_appliance(self) -> None:
        cycle_state = self.api.try_get_erd_value(self._state_erd_code)
        source_remaining = self._coerce_to_timedelta(
            self.api.try_get_erd_value(self._time_remaining_erd_code)
        )
        is_running = self._is_running_state(cycle_state)

        if is_running and source_remaining and source_remaining > timedelta(seconds=0):
            should_restart = (
                self._state != TimerState.ACTIVE
                or self._duration is None
                or self._started_at is None
                or (
                    self._last_source_remaining is not None
                    and source_remaining > self._last_source_remaining + timedelta(seconds=60)
                )
            )
            if should_restart:
                self._duration = source_remaining
                self._started_at = dt_util.utcnow()
                self._state = TimerState.ACTIVE
        elif not is_running:
            self._state = TimerState.IDLE
            self._duration = None
            self._started_at = None

        self._last_source_remaining = source_remaining

    def _coerce_to_timedelta(self, value: Any) -> Optional[timedelta]:
        if value is None:
            return None
        if isinstance(value, timedelta):
            return value
        if isinstance(value, (int, float)):
            return timedelta(minutes=float(value))
        return None

    def _is_running_state(self, value: Any) -> bool:
        if value is None:
            return False

        raw = value.name if hasattr(value, "name") else str(value)
        state = raw.strip().upper()
        if not state:
            return False

        not_running_tokens = [
            "IDLE",
            "OFF",
            "PAUSE",
            "PAUSED",
            "COMPLETE",
            "COMPLETED",
            "END",
            "DONE",
            "FINISH",
            "CANCEL",
            "STOP",
            "READY",
            "DELAY",
            "ERROR",
        ]
        if any(token in state for token in not_running_tokens):
            return False

        running_tokens = [
            "RUN",
            "START",
            "RESUME",
            "ACTIVE",
            "WASH",
            "RINSE",
            "SPIN",
            "DRY",
            "TUMBLE",
            "HEAT",
            "CYCLE",
        ]
        return any(token in state for token in running_tokens)
