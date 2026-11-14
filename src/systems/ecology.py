"""生态调度、种群容量与清理系统。"""

from __future__ import annotations
import logging
from typing import List, Callable
from ..entities.base_entity import BaseEntity

logger = logging.getLogger("roachHub.ecology")


class EcologySystem:
    """管理整体种群容量、环境承载力与已死亡物体的垃圾回收。"""

    def __init__(self, max_population: int = 20) -> None:
        self.max_population = max_population

    def update(
        self,
        entities: List[BaseEntity],
        on_entity_removed: Callable[[str], None] | None = None,
    ) -> List[BaseEntity]:
        """过滤清理已彻底死亡并过期的实体。"""
        survivors: List[BaseEntity] = []
        for entity in entities:
            if entity.is_alive:
                survivors.append(entity)
            else:
                if on_entity_removed:
                    on_entity_removed(entity.id)
                logger.info("清理已死亡实体: ID=%s", entity.id)
        return survivors
