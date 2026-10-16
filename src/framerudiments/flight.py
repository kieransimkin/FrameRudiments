"""Seeded paper-glider animation extracted from the original Shorts renderer.

This is an animation model, not CFD. Its state machine makes the energy
exchange visible: a fast glide rises, speed bleeds away, the aircraft mushes,
lift breaks, the nose drops, the dive rebuilds speed, and the plane pulls out
into another long swoop. A separate falling-leaf mode adds the alternating
slip/roll seen in a fully stalled paper glider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random


@dataclass
class FlightState:
    x: float
    y: float
    vx: float
    vy: float
    pitch: float
    pitch_rate: float = 0.0
    roll: float = 0.0
    yaw_scale: float = 1.0
    state: str = "glide"
    state_time: float = 0.0
    age: float = 0.0
    stall_count: int = 0
    history: list[tuple[float, float]] = field(default_factory=list)


class PaperPlaneFlight:
    """Deterministic portrait-space motion with visually explicit stall phases."""

    def __init__(self, seed: int, x: float, y: float, speed: float,
                 sprite_index: int, stall_after: float | None = None,
                 behaviour: str = "cycle"):
        self.rng = random.Random(seed)
        self.sprite_index = sprite_index
        self.stall_after = stall_after
        self.behaviour = behaviour
        self.phase = self.rng.random() * math.tau
        self.base_scale = self.rng.uniform(0.78, 1.08)
        self.depth = self.rng.uniform(0.72, 1.0)
        self.cruise_speed = speed
        self.next_cycle = stall_after if stall_after is not None else self.rng.uniform(2.0, 3.1)
        self.s = FlightState(x=x, y=y, vx=speed, vy=-0.025, pitch=math.radians(-7))

    @property
    def flight_angle(self) -> float:
        return math.atan2(self.s.vy, max(1e-6, self.s.vx))

    @property
    def airspeed(self) -> float:
        return math.hypot(self.s.vx, self.s.vy)

    @property
    def angle_of_attack(self) -> float:
        return self.flight_angle - self.s.pitch

    def _enter(self, state: str) -> None:
        self.s.state = state
        self.s.state_time = 0.0
        if state == "stall":
            self.s.stall_count += 1

    def _transition(self) -> None:
        s = self.s
        if s.state == "glide" and s.age >= self.next_cycle and self.behaviour != "stable":
            self._enter("climb")
        elif s.state == "climb" and s.state_time >= 1.02:
            self._enter("mush")
        elif s.state == "mush" and s.state_time >= 0.32:
            self._enter("stall")
        elif s.state == "stall" and s.state_time >= 0.22:
            self._enter("drop")
        elif s.state == "drop" and s.state_time >= (1.10 if self.behaviour == "leaf" else 0.78):
            self._enter("recovery")
        elif s.state == "recovery" and s.state_time >= 0.68:
            self._enter("swoop")
        elif s.state == "swoop" and s.state_time >= 0.92:
            self._enter("glide")
            self.next_cycle = s.age + self.rng.uniform(1.05, 1.65)

    def step(self, dt: float, global_time: float) -> FlightState:
        s = self.s
        s.age += dt
        s.state_time += dt
        self._transition()

        targets = {
            "glide":    (self.cruise_speed, -0.018, -6.0),
            "climb":    (self.cruise_speed * 0.68, -0.105, -25.0),
            "mush":     (self.cruise_speed * 0.38, -0.012, -20.0),
            "stall":    (self.cruise_speed * 0.25,  0.055, -14.0),
            "drop":     (self.cruise_speed * 0.76,  0.205,  36.0),
            "recovery": (self.cruise_speed * 1.18,  0.095,  17.0),
            "swoop":    (self.cruise_speed * 1.02, -0.090, -18.0),
        }
        target_vx, target_vy, target_pitch_deg = targets[s.state]
        velocity_scale = self.cruise_speed / 0.22
        target_vy *= velocity_scale

        if self.behaviour == "stable":
            target_vy = (-0.010 + 0.018 * math.sin(global_time * 0.72 + self.phase)) * velocity_scale
            target_pitch_deg = math.degrees(math.atan2(target_vy, target_vx)) - 2.5

        leaf_weight = 0.0
        if self.behaviour == "leaf" and s.state in {"stall", "drop"}:
            leaf_weight = min(1.0, s.state_time / 0.32)
            target_vx *= 0.58
            target_vy = 0.155 * velocity_scale
            s.x += math.sin(global_time * 5.0 + self.phase) * 0.0018 * leaf_weight

        response = 1.9 if s.state in {"climb", "swoop"} else 3.2
        if s.state == "stall":
            response = 5.0
        s.vx += (target_vx - s.vx) * min(1.0, dt * response)
        s.vy += (target_vy - s.vy) * min(1.0, dt * response)

        target_pitch = math.radians(target_pitch_deg)
        pitch_response = 2.2 if s.state in {"mush", "stall"} else 4.0
        s.pitch_rate += ((target_pitch - s.pitch) * pitch_response - s.pitch_rate * 3.0) * dt
        s.pitch += s.pitch_rate * dt

        # Bank belongs to the manoeuvre, not a permanent oscillation. Stable
        # flight stays almost level; climbs and pull-outs use one smooth lean.
        bank_by_state = {
            "glide": 0.0, "climb": -7.0, "mush": -3.0,
            "stall": 2.0, "drop": 8.0, "recovery": 5.0, "swoop": -8.0,
        }
        bank = bank_by_state[s.state]
        if leaf_weight:
            bank = 31.0 * math.sin(global_time * 5.0 + self.phase) * leaf_weight
        s.roll += (bank - s.roll) * min(1.0, dt * 5.2)
        s.yaw_scale = 1.0 - 0.38 * leaf_weight * abs(math.sin(global_time * 5.0 + self.phase))

        s.x += s.vx * dt
        s.y += s.vy * dt
        if not s.history or math.hypot(s.x - s.history[-1][0], s.y - s.history[-1][1]) > 0.003:
            s.history.append((s.x, s.y))
            del s.history[:-70]
        return s


def simulate_stall(seed: int = 7, seconds: float = 10.0,
                   dt: float = 1 / 120) -> list[tuple[float, str, float, float, float]]:
    plane = PaperPlaneFlight(seed, 0.05, 0.42, 0.15, 0, stall_after=0.5)
    samples = []
    t = 0.0
    while t < seconds:
        plane.step(dt, t)
        samples.append((t, plane.s.state, plane.airspeed, plane.s.y, plane.s.pitch))
        t += dt
    return samples
