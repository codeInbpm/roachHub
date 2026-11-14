"""高拟真生物繁殖系统 (自动繁殖与轻量有性繁殖)。

支持：
- 自动繁殖模式 (无性别门槛直接产卵)；
- 轻量有性繁殖模式 (雌雄相遇寻偶、尾对尾交尾、生物分泌、受精产卵)；
- 能量阈值、安全状态、种群承载力限制与卵鞘交付方式。
"""

from __future__ import annotations
import math
import random
import logging
from typing import List, Optional, Tuple, Dict

from ..entities.base_entity import BaseEntity, LifeStage, EntityState
from ..entities.cockroach import Cockroach
from ..entities.ootheca import Ootheca
from ..utils.math_utils import Vector2D, angle_diff, normalize_angle

logger = logging.getLogger("roachHub.reproduction")


class ReproductionSystem:
    """全面管理蟑螂的繁衍、交配寻偶与卵鞘生产。"""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.reproduce_mode = str(cfg.get("reproduce_mode", "auto"))
        self.female_ratio = float(cfg.get("female_ratio", 0.5))
        self.mating_radius = float(cfg.get("mating_search_radius", 180.0))
        self.min_energy = float(cfg.get("reproduce_min_energy", 55.0))
        self.cost_energy = float(cfg.get("reproduce_cost_energy", 25.0))
        self.reproduce_cooldown = float(cfg.get("reproduce_cooldown", 28.0))
        self.allow_near_cap = bool(cfg.get("allow_reproduce_near_cap", False))
        self.ootheca_mode = str(cfg.get("ootheca_mode", "carried"))
        self.egg_incubation_time = float(cfg.get("egg_incubation_time", 15.0))

    def update_config(self, cfg: dict) -> None:
        self.reproduce_mode = str(cfg.get("reproduce_mode", self.reproduce_mode))
        self.female_ratio = float(cfg.get("female_ratio", self.female_ratio))
        self.mating_radius = float(cfg.get("mating_search_radius", self.mating_radius))
        self.min_energy = float(cfg.get("reproduce_min_energy", self.min_energy))
        self.cost_energy = float(cfg.get("reproduce_cost_energy", self.cost_energy))
        self.reproduce_cooldown = float(cfg.get("reproduce_cooldown", self.reproduce_cooldown))
        self.allow_near_cap = bool(cfg.get("allow_reproduce_near_cap", self.allow_near_cap))
        self.ootheca_mode = str(cfg.get("ootheca_mode", self.ootheca_mode))
        self.egg_incubation_time = float(cfg.get("egg_incubation_time", self.egg_incubation_time))

    def update(
        self,
        entities: List[BaseEntity],
        dt: float,
        max_population: int,
    ) -> List[BaseEntity]:
        """执行繁殖周期循环，返回本帧新产生的所有卵鞘。"""
        new_oothecas: List[BaseEntity] = []

        all_roaches = [e for e in entities if e.is_alive and isinstance(e, Cockroach)]
        current_pop = len(all_roaches)

        # 检查种群容量限制
        if not self.allow_near_cap and current_pop >= max_population:
            return new_oothecas

        adults = [r for r in all_roaches if r.stage == LifeStage.ADULT and not r.is_carrying_ootheca]

        if self.reproduce_mode == "auto":
            # 1. 自动繁殖模式
            for roach in adults:
                if self._can_auto_reproduce(roach):
                    ootheca = self._spawn_ootheca(roach)
                    new_oothecas.append(ootheca)
        else:
            # 2. 轻量有性繁殖模式
            new_oothecas.extend(self._handle_sexual_reproduction(adults, all_roaches, dt))

        return new_oothecas

    def _can_auto_reproduce(self, roach: Cockroach) -> bool:
        return (
            roach.is_alive
            and roach.stage == LifeStage.ADULT
            and not roach.is_carrying_ootheca
            and roach.reproduce_timer <= 0.0
            and roach.energy >= self.min_energy
            and roach.state not in (EntityState.PANIC, EntityState.FREEZE, EntityState.MOLTING)
        )

    def _handle_sexual_reproduction(
        self,
        adults: List[Cockroach],
        all_roaches: List[Cockroach],
        dt: float,
    ) -> List[BaseEntity]:
        """有性模式：雌雄寻偶相遇、尾对尾交尾与受精产卵。"""
        new_oothecas: List[BaseEntity] = []
        roach_map: Dict[str, Cockroach] = {r.id: r for r in all_roaches}

        females = [r for r in adults if r.gender == "female"]
        males = [r for r in adults if r.gender == "male"]

        for female in females:
            # 1. 正在交配中状态处理
            if female.state == EntityState.MATING:
                partner_id = female.mating_partner_id
                partner = roach_map.get(partner_id) if partner_id else None

                # 检查中途是否受惊打断
                if not partner or not partner.is_alive or partner.state in (EntityState.PANIC, EntityState.FREEZE):
                    female.interrupt_mating()
                    if partner:
                        partner.interrupt_mating()
                    continue

                # 交尾倒计时完成 -> 产下受精卵鞘！
                if female.mating_progress >= female.mating_duration:
                    ootheca = self._spawn_ootheca(female)
                    new_oothecas.append(ootheca)

                    # 双方恢复正常状态并进入冷却
                    female.finish_mating()
                    partner.finish_mating()

                    female.reproduce_timer = self.reproduce_cooldown
                    partner.consume_energy(self.cost_energy * 0.6)
                    partner.reproduce_timer = self.reproduce_cooldown * 0.8
                    logger.info("🎉 雌性 %s 成功完成受精并产下卵鞘！", female.id)
                continue

            # 2. 准备寻找伴侣
            if female.reproduce_timer > 0.0 or female.energy < self.min_energy:
                continue
            if female.state in (EntityState.PANIC, EntityState.FREEZE, EntityState.MOLTING):
                continue

            # 扫描感应范围内的健康成年雄性
            nearest_male: Optional[Cockroach] = None
            min_dist = self.mating_radius

            for male in males:
                if male.state in (EntityState.PANIC, EntityState.FREEZE, EntityState.MOLTING, EntityState.MATING):
                    continue
                if male.energy < self.min_energy:
                    continue
                d = female.pos.distance_to(male.pos)
                if d < min_dist:
                    min_dist = d
                    nearest_male = male

            if nearest_male:
                dist = female.pos.distance_to(nearest_male.pos)

                if dist < 26.0:
                    # 距离足够近，开始尾对尾交尾对接
                    diff_vec = nearest_male.pos - female.pos
                    female.target_angle = diff_vec.angle() + math.pi
                    nearest_male.target_angle = diff_vec.angle()

                    female.start_mating(nearest_male.id, duration=2.2)
                    nearest_male.start_mating(female.id, duration=2.2)
                    logger.info("💕 蟑螂开启有性交尾: 雌 %s <-> 雄 %s", female.id, nearest_male.id)
                else:
                    # 相互趋向靠近
                    to_male = nearest_male.pos - female.pos
                    female.target_angle = to_male.angle()
                    female.target_speed = female.min_walk_speed * 1.15

                    to_female = female.pos - nearest_male.pos
                    nearest_male.target_angle = to_female.angle()
                    nearest_male.target_speed = nearest_male.min_walk_speed * 1.2

        return new_oothecas

    def _spawn_ootheca(self, mother: Cockroach) -> Ootheca:
        """根据配置产下卵鞘（母体拖卵或桌面静置）。"""
        mode = self.ootheca_mode
        if mode == "random":
            mode = random.choice(["carried", "dropped"])

        is_carried = (mode == "carried")
        ootheca = Ootheca(
            x=mother.x,
            y=mother.y,
            parent=mother if is_carried else None,
            incubation_time=self.egg_incubation_time,
            is_carried=is_carried,
        )

        if is_carried:
            mother.is_carrying_ootheca = True
            mother.carried_ootheca = ootheca
        else:
            mother.reproduce_timer = self.reproduce_cooldown

        mother.consume_energy(self.cost_energy)
        return ootheca
