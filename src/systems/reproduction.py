"""高拟真生物繁殖系统（性别、求偶、交配受精与卵鞘生产）。

两条硬规则撑着全部行为：

1. **只有雌虫能产卵，且必须先受精。** 处女雌虫（``broods_left == 0``）能量再满
   也生不出来 —— 所以 ``female_ratio``、``mating_search_radius`` 在两种模式下都真实
   生效，而不是只在有性模式里挂个摆设。
2. **一次交配支撑若干次产卵**（``fertilized_broods``）。真实蟑螂雌虫会储精，交配
   一次能连产几枚卵鞘；额度用完才需要重新求偶。雄虫因此是"节奏约束"，不是"每胎
   都必须到场的瓶颈"。

两种模式只差交配仪式的严格程度：

- ``auto``   ：感知半径内发现雄虫即趋近，靠近到 1.15 倍体长处（约 80px，尾部刚好相碰）
  即算受孕，
  过程短（``auto_mating_time``）；
- ``sexual`` ：必须尾部相接（0.92 倍体长，实际约 64px，对 70px 的虫体才是贴合而
  不是重叠），尾对尾交尾更久（``mating_duration``），任一方受惊即告中断。

求偶驱力通过 ``Cockroach.mate_seek`` 交给行为层执行（见 behavior.py），所以屏幕上
看到的是"雄虫循着气味一路找过去"，而不是雌虫站着自己冒出卵鞘。
"""

from __future__ import annotations

import math
import random
import logging
from typing import List, Optional, Dict

from ..entities.base_entity import BaseEntity, LifeStage, EntityState
from ..entities.cockroach import Cockroach
from ..entities.ootheca import Ootheca

logger = logging.getLogger("roachHub.reproduction")

# 求偶扫描的节流间隔（秒）。全场两两距离是 O(雌×雄)，200 只时每帧都算要 1ms 上下；
# 0.25s 扫一次足够让它们"看起来一直在找"，中间各帧沿用上次的 mate_seek。
COURTSHIP_INTERVAL = 0.25

# 长时间找不到雄虫时的日志节流（秒）
NO_MALE_WARN_INTERVAL = 120.0


class ReproductionSystem:
    """全面管理蟑螂的求偶、交配受精与卵鞘生产。"""

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
        self.fertilized_broods = max(1, int(cfg.get("fertilized_broods", 2)))
        self.auto_mating_time = float(cfg.get("auto_mating_time", 1.5))
        self.mating_duration = float(cfg.get("mating_duration", 2.2))

        self._courtship_acc = 0.0
        self._no_male_timer = 0.0

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
        self.fertilized_broods = max(1, int(cfg.get("fertilized_broods", self.fertilized_broods)))
        self.auto_mating_time = float(cfg.get("auto_mating_time", self.auto_mating_time))
        self.mating_duration = float(cfg.get("mating_duration", self.mating_duration))

    # ------------------------------------------------------------------ 主循环

    def update(
        self,
        entities: List[BaseEntity],
        dt: float,
        max_population: int,
    ) -> List[BaseEntity]:
        """执行一轮繁殖：交尾进度 → 修补配对 → 求偶 → 产卵。"""
        new_oothecas: List[BaseEntity] = []

        roaches = [e for e in entities if e.is_alive and isinstance(e, Cockroach)]
        current_pop = len(roaches)

        # 种群容量限制：只看活体，与卵鞘无关
        if not self.allow_near_cap and current_pop >= max_population:
            return new_oothecas

        by_id: Dict[str, Cockroach] = {r.id: r for r in roaches}
        adults = [
            r for r in roaches
            if r.stage == LifeStage.ADULT and not r.is_carrying_ootheca
        ]
        females = [r for r in adults if r.gender == "female"]
        males = [r for r in adults if r.gender == "male"]

        if not females:
            return new_oothecas

        # 1. 交尾进度 / 产卵（每帧推进，不节流）
        for female in females:
            ootheca = self._tick_female(female, by_id)
            if ootheca is not None:
                new_oothecas.append(ootheca)

        # 2. 修补断裂的配对（伴侣被拍死/受惊时，另一头不能永远卡在 MATING）
        self._repair_pairs(roaches, by_id)

        # 3. 求偶配对（节流扫描）
        self._courtship_acc += dt
        if self._courtship_acc >= COURTSHIP_INTERVAL:
            self._courtship_acc = 0.0
            self._courtship(females, males)

        return new_oothecas

    # ------------------------------------------------------------------ 单只雌虫

    def _tick_female(
        self,
        female: Cockroach,
        by_id: Dict[str, Cockroach],
    ) -> Optional[BaseEntity]:
        """推进一只雌虫：交尾等进度 → 有受精额度就产卵。"""
        # 1. 交尾中：等进度，进度满了就受精
        if female.state == EntityState.MATING:
            if female.mating_progress >= female.mating_duration:
                partner = by_id.get(female.mating_partner_id or "")
                if partner is not None and partner.is_alive:
                    self._finish_fertilize(female, partner)
                else:
                    female.interrupt_mating()
            return None

        # 2. 已受精：按冷却产卵（能量门槛照旧）
        if female.broods_left > 0 and self._can_produce(female):
            return self._spawn_ootheca(female)

        return None

    # ------------------------------------------------------------------ 求偶

    def _courtship(
        self,
        females: List[Cockroach],
        males: List[Cockroach],
    ) -> None:
        """给待受精雌虫与可用雄虫配对，写入 mate_seek 或直接开始交尾。"""
        for roach in females:
            roach.mate_seek = None
        for roach in males:
            roach.mate_seek = None

        pending = [f for f in females if self._wants_mate(f)]
        if not pending:
            self._no_male_timer = 0.0
            return
        free_males = [m for m in males if self._male_free(m)]
        if not free_males:
            self._warn_no_male()
            return
        self._no_male_timer = 0.0

        # 1. 雌虫侧：只感知 mating_radius 内的雄虫（触角够得着的距离）
        for female in pending:
            near = self._nearest(female, free_males, self.mating_radius)
            if near is None:
                continue
            if female.pos.distance_to(near.pos) <= self._contact_distance(female, near):
                self._begin_mating(female, near)
                continue
            female.mate_seek = near.pos

        # 2. 雄虫侧：性信息素远程吸引，不设半径上限。
        #    真实雄虫会顺着气味长距离搜索；也让"雌雄随机散落在 1920x1080 两头"
        #    这种开局不至于半天配不上对。
        for male in free_males:
            if male.state == EntityState.MATING:
                continue
            target = self._nearest(male, pending, 1e9)
            if target is None:
                continue
            if male.pos.distance_to(target.pos) <= self._contact_distance(target, male):
                self._begin_mating(target, male)
            else:
                male.mate_seek = target.pos

    def _begin_mating(self, female: Cockroach, male: Cockroach) -> None:
        if female.state == EntityState.MATING or male.state == EntityState.MATING:
            return
        # 尾对尾：两个头朝外、尾相接
        diff_vec = male.pos - female.pos
        if diff_vec.length_squared() > 1e-6:
            female.target_angle = diff_vec.angle() + math.pi
            male.target_angle = diff_vec.angle()
        duration = (
            self.auto_mating_time
            if self.reproduce_mode == "auto"
            else self.mating_duration
        )
        female.start_mating(male.id, duration=duration)
        male.start_mating(female.id, duration=duration)
        logger.info(
            "💕 交尾开始：雌 %s <-> 雄 %s（%s 模式，%.1fs）",
            female.id, male.id, self.reproduce_mode, duration,
        )

    def _finish_fertilize(self, female: Cockroach, male: Cockroach) -> None:
        """交尾完成：写入受精额度，双方进入各自的恢复期。"""
        female.broods_left = self.fertilized_broods
        female.consume_energy(self.cost_energy * 0.10)
        male.consume_energy(self.cost_energy * 0.35)
        male.reproduce_timer = max(male.reproduce_timer, self.reproduce_cooldown * 0.6)
        female.finish_mating()
        male.finish_mating()
        logger.info(
            "🎉 受精完成：雌 %s 可产 %d 枚卵鞘（雄虫恢复 %.1fs）",
            female.id, self.fertilized_broods, self.reproduce_cooldown * 0.6,
        )

    def _repair_pairs(
        self,
        roaches: List[Cockroach],
        by_id: Dict[str, Cockroach],
    ) -> None:
        """任何一方脱队（死亡/受惊/被玩家拎走），另一方立刻退出 MATING。"""
        for roach in roaches:
            if roach.state != EntityState.MATING:
                continue
            partner = by_id.get(roach.mating_partner_id or "")
            if partner is None or not partner.is_alive or partner.state != EntityState.MATING:
                roach.interrupt_mating()

    # ------------------------------------------------------------------ 卵鞘

    def _spawn_ootheca(self, mother: Cockroach) -> Ootheca:
        """产下卵鞘（母体拖卵或桌面静置），并消耗受精额度。"""
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

        mother.broods_left = max(0, mother.broods_left - 1)
        mother.consume_energy(self.cost_energy)
        return ootheca

    # ------------------------------------------------------------------ 判定

    def _can_produce(self, female: Cockroach) -> bool:
        """能不能现在就产一枚。"""
        return (
            female.is_alive
            and female.stage == LifeStage.ADULT
            and not female.is_carrying_ootheca
            and female.reproduce_timer <= 0.0
            and female.energy >= self.min_energy
            and female.state not in _BUSY_STATES
        )

    def _wants_mate(self, female: Cockroach) -> bool:
        """想不想找雄虫：只由"额度空了 + 能量够"决定，与产卵冷却无关 ——
        冷却期正好用来谈下一轮恋爱，真实蟑螂也不会等卵鞘落地才去交配。"""
        return (
            female.is_alive
            and female.stage == LifeStage.ADULT
            and not female.is_carrying_ootheca
            and female.broods_left <= 0
            and female.energy >= self.min_energy
            and female.state not in _BUSY_STATES
        )

    def _male_free(self, male: Cockroach) -> bool:
        return (
            male.is_alive
            and male.stage == LifeStage.ADULT
            and male.reproduce_timer <= 0.0
            and male.energy >= self.min_energy * 0.6
            and male.state not in _BUSY_STATES
        )

    def _contact_distance(self, a: Cockroach, b: Cockroach) -> float:
        """对接距离：按两者体长算，而不是写死像素。

        auto 松一点（1.15 倍半体长和 ≈ 80px，尾部刚好碰上），sexual 紧一点（0.92 倍，
        尾部实际贴合）。原来写死的 26px 对 70px 的虫体来说是"叠在一起"，
        视觉上就是两只虫压成一坨；反过来超过 1.35 倍时中间会留出明显的空隙，
        看上去只是"两只虫站得比较近"。
        """
        factor = 1.15 if self.reproduce_mode == "auto" else 0.92
        return 0.5 * (a.body_len + b.body_len) * factor

    @staticmethod
    def _nearest(
        source: Cockroach,
        candidates: List[Cockroach],
        max_dist: float,
    ) -> Optional[Cockroach]:
        best: Optional[Cockroach] = None
        best_d = max_dist
        for other in candidates:
            if other is source or other.state == EntityState.MATING:
                continue
            d = source.pos.distance_to(other.pos)
            if d < best_d:
                best_d = d
                best = other
        return best

    def _warn_no_male(self) -> None:
        """全场没有可用雄虫时提示一次 —— 否则玩家只会觉得"忽然不生了"。"""
        self._no_male_timer += COURTSHIP_INTERVAL
        if self._no_male_timer < NO_MALE_WARN_INTERVAL:
            return
        self._no_male_timer = 0.0
        logger.warning(
            "繁殖停滞：有雌虫待受精，但场上没有可交配的雄虫（可能全是雌性，或雄虫太虚弱）"
        )


# 这些状态下不产卵也不谈对象：惊慌、僵直、蜕皮、交尾中、被拎着、正在吃
_BUSY_STATES = (
    EntityState.PANIC,
    EntityState.FREEZE,
    EntityState.MOLTING,
    EntityState.MATING,
    EntityState.DRAGGED,
    EntityState.EAT,
)
