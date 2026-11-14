"""生命周期演进系统（卵鞘破壳孵化、若虫蜕皮演进与衰老死亡）。"""

from __future__ import annotations
import random
import logging
from typing import List, Tuple

from ..entities.base_entity import BaseEntity, LifeStage, EntityState
from ..entities.cockroach import Cockroach
from ..entities.ootheca import Ootheca
from ..utils.math_utils import Vector2D

logger = logging.getLogger("roachHub.lifecycle")


class LifecycleSystem:
    """管理从卵鞘孵化、若虫阶段演化到成虫羽化和自然寿命终结。"""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.egg_incubation_time: float = float(cfg.get("egg_incubation_time", 15.0))
        self.nymphs_min: int = int(cfg.get("nymphs_per_ootheca_min", 1))
        self.nymphs_max: int = int(cfg.get("nymphs_per_ootheca_max", 3))
        self.max_instars: int = int(cfg.get("max_instars", 4))
        self.instar_duration: float = float(cfg.get("instar_duration", 18.0))
        self.adult_lifespan: float = float(cfg.get("adult_lifespan", 300.0))
        self.female_ratio: float = float(cfg.get("female_ratio", 0.5))

    def update_config(self, cfg: dict) -> None:
        self.egg_incubation_time = float(cfg.get("egg_incubation_time", self.egg_incubation_time))
        self.nymphs_min = int(cfg.get("nymphs_per_ootheca_min", self.nymphs_min))
        self.nymphs_max = int(cfg.get("nymphs_per_ootheca_max", self.nymphs_max))
        self.max_instars = int(cfg.get("max_instars", self.max_instars))
        self.instar_duration = float(cfg.get("instar_duration", self.instar_duration))
        self.adult_lifespan = float(cfg.get("adult_lifespan", self.adult_lifespan))
        self.female_ratio = float(cfg.get("female_ratio", self.female_ratio))

    def update(
        self,
        entities: List[BaseEntity],
        dt: float,
        roach_config: dict,
        max_population: int,
    ) -> List[BaseEntity]:
        """执行生命周期检查，返回本帧新破壳诞生的若虫列表。"""
        newborn_nymphs: List[BaseEntity] = []

        all_roaches = [e for e in entities if isinstance(e, Cockroach) and e.is_alive]
        current_pop = len(all_roaches)

        for entity in entities:
            if not entity.is_alive:
                continue

            # 1. 卵鞘孵化判定
            if isinstance(entity, Ootheca):
                if entity.is_hatched:
                    entity.is_alive = False
                    if entity.parent and isinstance(entity.parent, Cockroach):
                        entity.parent.is_carrying_ootheca = False
                        entity.parent.carried_ootheca = None

                    # 种群容积允许时破壳孵化
                    if current_pop < max_population:
                        n_spawn = random.randint(self.nymphs_min, self.nymphs_max)
                        n_spawn = min(n_spawn, max_population - current_pop)

                        for _ in range(n_spawn):
                            offset = Vector2D.random_direction() * random.uniform(10.0, 24.0)
                            spawn_pos = entity.pos + offset
                            # 依雌雄比例随机分配性别
                            gender = "female" if random.random() < self.female_ratio else "male"

                            nymph = Cockroach(
                                x=spawn_pos.x,
                                y=spawn_pos.y,
                                stage=LifeStage.NYMPH,
                                gender=gender,
                                instar=1,
                                max_instars=self.max_instars,
                                config=roach_config,
                            )
                            nymph.instar_duration = self.instar_duration
                            newborn_nymphs.append(nymph)
                            current_pop += 1
                            logger.info("卵鞘破壳：诞生 1 龄若虫 ID=%s (性别: %s)", nymph.id, gender)

            # 2. 若虫与成虫生命周期
            elif isinstance(entity, Cockroach):
                # 若虫蜕皮演化
                if entity.stage == LifeStage.NYMPH:
                    if entity.instar_age >= self.instar_duration and entity.state != EntityState.PANIC:
                        old_instar = entity.instar
                        entity.advance_instar()
                        if entity.stage == LifeStage.ADULT:
                            logger.info("🎉 蟑螂 ID=%s (%s) 完成终龄蜕皮，羽化为成虫并长出翅膀！", entity.id, entity.gender)
                        else:
                            logger.info("✨ 若虫 ID=%s 蜕皮成长：%d龄 -> %d龄", entity.id, old_instar, entity.instar)

                # 成虫自然衰老
                elif entity.stage == LifeStage.ADULT:
                    if self.adult_lifespan > 0 and entity.age >= self.adult_lifespan:
                        entity.die("natural_lifespan")
                        logger.info("成虫 ID=%s 寿命终结自然老死", entity.id)

        return newborn_nymphs
