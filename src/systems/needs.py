"""生理需求系统（饥饿、恐惧代谢等）。"""

from __future__ import annotations
from typing import List
from ..entities.base_entity import BaseEntity, EntityState


class NeedsSystem:
    """计算生物能量消耗、饥饿状态与生理需求。"""

    def __init__(self, energy_decay_rate: float = 0.5) -> None:
        self.energy_decay_rate = energy_decay_rate

    def update(self, entities: List[BaseEntity], dt: float) -> None:
        for entity in entities:
            if not entity.is_alive:
                continue

            # 基础代谢率：恐慌狂奔时消耗加倍
            burn_rate = self.energy_decay_rate
            if entity.state == EntityState.PANIC:
                burn_rate *= 3.0
            elif entity.state == EntityState.IDLE:
                burn_rate *= 0.5

            entity.consume_energy(burn_rate * dt)
