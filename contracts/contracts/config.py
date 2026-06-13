"""
Pydantic schema for scenario YAML config.
Load with: ScenarioConfig.from_yaml("config/scenario_default.yaml")
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, model_validator
import yaml


class RadarConfig(BaseModel):
    position: tuple[float, float, float]
    range: float                  # metres
    fov_deg: float                # 360 = omnidirectional
    noise_std: float              # metres, 1-sigma


class ShahedConfig(BaseModel):
    count: int
    speed_mps: tuple[float, float]        # [min, max] — sampled uniformly
    spawn_radius: float                   # metres from target
    spawn_angle_spread_deg: float         # 360 = full circle


class InterceptorConfig(BaseModel):
    count: int
    speed_mps: float
    max_turn_rate_deg_s: float
    range_m: float                        # max engagement range
    launch_position: tuple[float, float, float]


class CommsConfig(BaseModel):
    publish_rate_hz: float = 5.0
    packet_loss_prob: float = 0.10        # probability a single message is dropped
    consensus_window_ms: float = 400.0    # claim-and-confirm wait window
    max_claim_rounds: int = 2             # rounds before greedy fallback


class ScenarioConfig(BaseModel):
    seed: int
    target_position: tuple[float, float, float]
    duration_max: float                   # seconds
    situation: Literal["A", "B"]
    radars: list[RadarConfig]
    shaheds: ShahedConfig
    interceptors: InterceptorConfig
    comms: CommsConfig = CommsConfig()

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
        # The YAML groups the scenario-level fields under a `scenario:` block
        # (see config/scenario_default.yaml and the docs); flatten it so the
        # flat model fields (seed, situation, ...) resolve.
        scenario = raw.pop("scenario", {})
        return cls(**scenario, **raw)
