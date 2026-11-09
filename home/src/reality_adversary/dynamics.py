"""Synthetic bounded scalar inertia, drift, disturbance and sensor latency."""
import random
from dataclasses import dataclass, field


@dataclass
class ScalarDynamics:
    value: float = 70
    actuator_effect: float = -2
    drift: float = .1
    noise: float = .2
    inertia: float = .7
    interval: float = 1
    latency: int = 0
    seed: int = 0
    minimum: float = 0
    maximum: float = 100
    velocity: float = field(default=0, init=False)

    def __post_init__(self):
        if not 0 <= self.inertia < 1 or self.interval <= 0 or self.latency < 0 or self.minimum > self.maximum:
            raise ValueError('Invalid synthetic dynamics bounds')
        self.rng = random.Random(self.seed)
        self.samples = [self.value]*self.latency

    def step(self, active, disturbance=0):
        force = self.drift+(self.actuator_effect if active else 0)
        self.velocity = self.inertia*self.velocity+(1-self.inertia)*force
        self.value = min(self.maximum, max(self.minimum, self.value+self.velocity*self.interval+disturbance+
                                         self.rng.uniform(-self.noise, self.noise)))
        self.samples.append(self.value)
        return self.samples.pop(0)
