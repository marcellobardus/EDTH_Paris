"""
Pydantic schema for scenario YAML config.
Load with: ScenarioConfig.from_yaml("config/scenario_default.yaml")
"""

from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class RadarConfig(BaseModel):
    position: tuple[float, float, float]
    range: float  # metres
    fov_deg: float  # 360 = omnidirectional
    noise_std: float  # metres, 1-sigma


class ShahedConfig(BaseModel):
    count: int
    speed_mps: tuple[float, float]  # [min, max] — sampled uniformly
    spawn_radius: float  # metres from target
    spawn_angle_spread_deg: float  # 360 = full circle


class InterceptorConfig(BaseModel):
    count: int
    speed_mps: float
    max_turn_rate_deg_s: float
    range_m: float  # max engagement range
    launch_position: tuple[float, float, float]


class GuidanceConfig(BaseModel):
    update_rate_hz: float = 10.0  # PN recompute / waypoint publish rate (FR-6.2)
    nav_constant: float = 4.0  # PN gain N (3-5)
    lookahead_s: float = 1.0  # carrot horizon: waypoint = speed * lookahead ahead


class CommsConfig(BaseModel):
    publish_rate_hz: float = 5.0
    packet_loss_prob: float = 0.10  # probability a single message is dropped
    consensus_window_ms: float = 400.0  # (legacy claim-and-confirm window; unused by CBAA)
    max_claim_rounds: int = 2  # (legacy greedy-fallback rounds; unused by CBAA)
    staleness_timeout_s: float = 1.5  # peer silent longer than this is "stale" (Q2, diagnostic)


class RetaskingConfig(BaseModel):
    """CBAA decentralised re-tasking (agent/retasking.py). Defaults match the
    pseudo-code spec; everything is optional so older YAML still loads."""

    decision_period_s: float = 0.2  # decision + broadcast cycle (5 Hz)
    lock_threshold_s: float = 5.0  # intercept_time < this => monotone lock
    bucket_size_s: float = 2.0  # intercept_time quantisation step
    bucket_hysteresis_s: float = 0.2  # sticky margin at bucket boundaries
    incumbency_margin: float = 1e-3  # a challenger must beat the holder by this
    change_repeat: int = 3  # re-emissions of a changed state (loss robustness)
    heartbeat_period_s: float = 0.2  # presence beacon period (5 Hz)
    silence_timeout_s: float = 0.6  # 3 missed beats => peer presumed gone


class ScenarioMeta(BaseModel):
    """Top-level scenario metadata — the `scenario:` block in the YAML."""

    seed: int
    target_position: tuple[float, float, float]
    duration_max: float  # seconds
    situation: Literal["A", "B"]


class ScenarioConfig(BaseModel):
    scenario: ScenarioMeta
    radars: list[RadarConfig]
    shaheds: ShahedConfig
    interceptors: InterceptorConfig
    comms: CommsConfig = CommsConfig()
    guidance: GuidanceConfig = GuidanceConfig()
    retasking: RetaskingConfig = RetaskingConfig()

    @model_validator(mode="after")
    def check_counts(self) -> ScenarioConfig:
        if self.interceptors.count < 1:
            raise ValueError("Need at least one interceptor")
        if self.shaheds.count < 1:
            raise ValueError("Need at least one Shahed")
        return self

    @classmethod
    def from_yaml(cls, path: str) -> ScenarioConfig:
        with open(path) as f:
            raw = yaml.safe_load(f)
        return cls.model_validate(raw)
