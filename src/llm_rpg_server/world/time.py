from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


Season = Literal["spring", "summer", "autumn", "winter"]
DayPeriod = Literal["dawn", "day", "dusk", "night"]


class WorldTimeSnapshot(BaseModel):
    total_game_hours: int = Field(ge=0)
    year: int = Field(ge=0)
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1)
    hour: int = Field(ge=0, le=23)
    season: Season
    period: DayPeriod
    is_day: bool
    observed_at: str
    real_seconds_per_game_hour: float = Field(gt=0)


class WorldClock:
    """A global, restart-stable world clock anchored by content configuration."""

    def __init__(
        self,
        definition: dict[str, Any],
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.epoch = datetime.fromisoformat(definition["epoch_utc"].replace("Z", "+00:00"))
        if self.epoch.tzinfo is None:
            self.epoch = self.epoch.replace(tzinfo=timezone.utc)
        self.epoch = self.epoch.astimezone(timezone.utc)
        self.real_seconds_per_game_hour = float(definition["real_seconds_per_game_hour"])
        self.hours_per_day = int(definition.get("hours_per_day", 24))
        self.days_per_month = int(definition.get("days_per_month", 30))
        self.months_per_year = int(definition.get("months_per_year", 12))
        self.seasons: list[Season] = list(definition.get(
            "seasons", ["spring", "summer", "autumn", "winter"]
        ))
        self.season_names = dict(definition.get("season_names", {}))
        self.period_names = dict(definition.get("period_names", {}))
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        if self.real_seconds_per_game_hour <= 0:
            raise ValueError("World clock speed must be positive")
        if self.months_per_year % len(self.seasons) != 0:
            raise ValueError("Months must divide evenly across configured seasons")

    def snapshot(self) -> WorldTimeSnapshot:
        observed = self._now().astimezone(timezone.utc)
        elapsed_seconds = max(0.0, (observed - self.epoch).total_seconds())
        total_hours = int(elapsed_seconds // self.real_seconds_per_game_hour)
        hours_per_month = self.hours_per_day * self.days_per_month
        hours_per_year = hours_per_month * self.months_per_year
        year, within_year = divmod(total_hours, hours_per_year)
        month_index, within_month = divmod(within_year, hours_per_month)
        day_index, hour = divmod(within_month, self.hours_per_day)
        months_per_season = self.months_per_year // len(self.seasons)
        season = self.seasons[month_index // months_per_season]
        period = self._period(hour)
        return WorldTimeSnapshot(
            total_game_hours=total_hours,
            year=year,
            month=month_index + 1,
            day=day_index + 1,
            hour=hour,
            season=season,
            period=period,
            is_day=period in {"dawn", "day", "dusk"},
            observed_at=observed.isoformat(),
            real_seconds_per_game_hour=self.real_seconds_per_game_hour,
        )

    def label(self, snapshot: WorldTimeSnapshot | None = None) -> str:
        value = snapshot or self.snapshot()
        season = self.season_names.get(value.season, value.season)
        period = self.period_names.get(value.period, value.period)
        return f"{value.year}年{value.month}月{value.day}日 {value.hour:02d}:00 · {season} · {period}"

    @staticmethod
    def matches(condition: dict[str, Any] | None, value: WorldTimeSnapshot) -> bool:
        if not condition:
            return True
        if condition.get("seasons") and value.season not in condition["seasons"]:
            return False
        if condition.get("periods") and value.period not in condition["periods"]:
            return False
        if condition.get("months") and value.month not in condition["months"]:
            return False
        if "hour_start" in condition and "hour_end" in condition:
            start, end = int(condition["hour_start"]), int(condition["hour_end"])
            if start <= end:
                if not start <= value.hour < end:
                    return False
            elif not (value.hour >= start or value.hour < end):
                return False
        return True

    @staticmethod
    def _period(hour: int) -> DayPeriod:
        if 5 <= hour < 7:
            return "dawn"
        if 7 <= hour < 18:
            return "day"
        if 18 <= hour < 20:
            return "dusk"
        return "night"
