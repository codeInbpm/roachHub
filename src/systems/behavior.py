"""高拟真生物行为决策系统。

实现：
- 分级逃逸感知 (Graded Escape Response): 远距触角警戒、中距戒备避让、近距瞬间僵直+极速狂奔；
- 自然梳理触角 (Grooming) 行为机；
- 昼夜节律活跃度换算 (Circadian Rhythm)；
- 觅食与生态决策。
"""

from __future__ import annotations
import math
import random
import datetime
from typing import List, Dict, Optional

from ..entities.base_entity import BaseEntity, EntityState, LifeStage
from ..entities.cockroach import Cockroach
from ..entities.food import Food
from ..utils.math_utils import normalize_angle, Vector2D


class BehaviorSystem:
    """高拟真行为状态机决策系统。"""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.thigmotaxis_weight = float(cfg.get("thigmotaxis_weight", 0.55))
        self.grooming_frequency = float(cfg.get("grooming_frequency", 0.35))
        self.circadian_rhythm = bool(cfg.get("circadian_rhythm", True))

        self.r_far = float(cfg.get("perception_far_radius", 260.0))
        self.r_mid = float(cfg.get("perception_mid_radius", 120.0))
        self.r_panic = float(cfg.get("perception_panic_radius", 55.0))

        self._decision_timers: Dict[str, float] = {}
        self._last_cursor_pos: Optional[Vector2D] = None

    def update_config(self, cfg: dict) -> None:
        self.thigmotaxis_weight = float(cfg.get("thigmotaxis_weight", self.thigmotaxis_weight))
        self.grooming_frequency = float(cfg.get("grooming_frequency", self.grooming_frequency))
        self.circadian_rhythm = bool(cfg.get("circadian_rhythm", self.circadian_rhythm))
        self.r_far = float(cfg.get("perception_far_radius", self.r_far))
        self.r_mid = float(cfg.get("perception_mid_radius", self.r_mid))
        self.r_panic = float(cfg.get("perception_panic_radius", self.r_panic))

    def update(
        self,
        entities: List[BaseEntity],
        dt: float,
        cursor_pos: Optional[Vector2D] = None,
    ) -> None:
        foods = [e for e in entities if isinstance(e, Food) and e.is_alive]

        # 计算昼夜节律活跃度乘数 (夜间 20:00~06:00 活跃，白天偏好停顿)
        circadian_multiplier = self._compute_circadian_factor()

        for entity in entities:
            if not entity.is_alive or not isinstance(entity, Cockroach):
                continue

            entity.circadian_factor = circadian_multiplier
            timer = self._decision_timers.get(entity.id, 0.0) - dt

            # 被捏在手上时不做任何自主决策，方向信息也失去意义
            if entity.state == EntityState.DRAGGED:
                self._decision_timers[entity.id] = timer
                continue

            # 1. 鼠标光标分级感知反应 (Graded Escape Response)
            self._handle_cursor_perception(entity, cursor_pos)

            # 2. 行为状态推进
            self._update_cockroach_behavior(entity, foods, timer, dt)

        self._last_cursor_pos = cursor_pos

    def _compute_circadian_factor(self) -> float:
        """根据当前系统时间计算昼夜活跃度因子。"""
        if not self.circadian_rhythm:
            return 1.0
        hour = datetime.datetime.now().hour
        # 夜间 (20:00 - 05:00) 极为活跃
        if hour >= 20 or hour < 6:
            return 1.30
        # 白天 (09:00 - 17:00) 偏好停顿休息
        elif 9 <= hour <= 17:
            return 0.75
        # 傍晚/清晨过渡
        return 1.0

    def _handle_cursor_perception(self, roach: Cockroach, cursor_pos: Optional[Vector2D]) -> None:
        """鼠标靠近时的分级警觉反应。

        方向信息 (threat_vector) 常驻更新，不设半径门槛 —— 触须因此始终能"感觉到"
        光标在哪；距离只影响威胁接近度与警觉度，也就是反应强度。
        """
        if cursor_pos is None:
            roach.threat_vector = None
            roach.threat_proximity = 0.0
            return

        to_cursor = cursor_pos - roach.pos
        dist = to_cursor.length()
        roach.threat_vector = to_cursor

        # 若虫更胆小，感知半径上浮 20%
        scale_mod = 1.2 if roach.stage == LifeStage.NYMPH else 1.0
        r_far = self.r_far * scale_mod
        r_mid = self.r_mid * scale_mod
        r_panic = self.r_panic * scale_mod

        # 触须跟随权重：进入感知半径就几乎完全指向（好奇/警戒），
        # 圈外按距离平滑衰减，保留一点"感觉到远处有动静"的底噪。
        if dist <= r_far:
            roach.threat_proximity = 1.0 - 0.15 * (dist / max(1.0, r_far))
        else:
            roach.threat_proximity = max(0.0, 0.85 * (1.0 - (dist - r_far) / 700.0))

        if roach.state in (EntityState.PANIC, EntityState.FREEZE, EntityState.DRAGGED):
            return

        if dist < r_panic:
            # 极近距离：先收触须（0.12s 预警），再僵直，最后爆发冲刺。
            # 这个顺序是"活物"和"开关"的区别：触须是它先做出反应的部分。
            roach.begin_prestartle(cursor_pos, 0.12)

        elif dist < r_mid:
            # 中距离：警惕，触角紧盯并开始小步后退或转向避开
            roach.alertness = min(1.0, roach.alertness + 0.35)
            # 缓慢背离光标移动
            flee_ang = (roach.pos - cursor_pos).angle()
            roach.target_angle = flee_ang
            roach.state = EntityState.WALK
            roach.target_speed = roach.min_walk_speed * 1.1

        elif dist < r_far:
            # 远距离：触角朝向光标轻颤警戒，偶尔停下探嗅
            if random.random() < 0.08 and roach.state == EntityState.WALK:
                roach.state = EntityState.IDLE
                roach.target_speed = 0.0
                roach.start_probe(0.4)

    def _update_cockroach_behavior(
        self,
        roach: Cockroach,
        foods: List[Food],
        timer: float,
        dt: float,
    ) -> None:
        """更新蟑螂自主决策状态机。"""
        # 恐慌爆发中
        if roach.state == EntityState.PANIC:
            roach.target_speed = roach.panic_speed * roach.circadian_factor
            if random.random() < 0.06:
                roach.target_angle = normalize_angle(roach.target_angle + random.uniform(-0.6, 0.6))
            return

        # 僵直防御中
        if roach.state == EntityState.FREEZE:
            roach.target_speed = 0.0
            return

        # 梳理触角中
        if roach.state == EntityState.GROOMING:
            roach.target_speed = 0.0
            return

        # 蜕皮脱壳中
        if roach.state == EntityState.MOLTING:
            roach.target_speed = 0.0
            return

        # 正在进食：按咬食节律推进
        if roach.state == EntityState.EAT:
            self._tick_eating(roach, dt)
            return

        # 觅食与进食判定 (饥饿驱动)
        if roach.energy < 75.0 and foods:
            target = self._pick_food(roach, foods)
            if target is None:
                # 食物都被占满：在外圈等，触须高频探嗅
                waiting = self._nearest_edible(roach, foods)
                if waiting is not None and roach.pos.distance_to(waiting.pos) < 95.0:
                    roach.state = EntityState.IDLE
                    roach.target_speed = 0.0
                    roach.target_angle = (waiting.pos - roach.pos).angle()
                    roach.start_probe(0.35)
                    return
            else:
                food, dist = target
                # 停下来的距离要够它把头探到食物上：体长约一半，否则食物会被压在身体下面
                stop_dist = max(32.0, roach.body_len * 0.62)
                if dist < stop_dist and food.claim(roach.id):
                    roach.state = EntityState.EAT
                    roach.eating_food = food
                    roach.bite_timer = 0.30
                    roach.target_speed = 0.0
                    roach.target_angle = (food.pos - roach.pos).angle()
                    return
                roach.state = EntityState.WALK
                roach.target_angle = (food.pos - roach.pos).angle()
                roach.target_speed = roach.max_walk_speed
                return

        # 正常状态下的自主漫步与梳理切换
        if timer <= 0.0:
            if roach.state in (EntityState.WALK, EntityState.EAT):
                # 白天或饱腹时更易停顿休息
                idle_prob = 0.42 / max(0.5, roach.circadian_factor)
                if random.random() < idle_prob:
                    # 停顿小憩
                    roach.state = EntityState.IDLE
                    roach.target_speed = 0.0
                    # 停顿期间有概率进入梳理触角动作 (Grooming)
                    if random.random() < self.grooming_frequency and roach.alertness < 0.2:
                        roach.start_grooming(duration=random.uniform(1.8, 2.6))
                        next_duration = roach.grooming_timer
                    else:
                        # 站着"探一探"：身体前倾、触须加密扫探
                        if random.random() < 0.55:
                            roach.start_probe(random.uniform(0.35, 0.6))
                        next_duration = random.uniform(1.0, 2.6)
                else:
                    # 继续行走漫步
                    roach.state = EntityState.WALK
                    roach.target_angle = normalize_angle(roach.angle + random.uniform(-0.8, 0.8))
                    base_sp = random.uniform(roach.min_walk_speed, roach.max_walk_speed)
                    roach.target_speed = base_sp * roach.circadian_factor
                    next_duration = random.uniform(1.6, 3.4)
            else:
                # 从 IDLE / GROOMING 重新启程
                roach.state = EntityState.WALK
                roach.target_angle = normalize_angle(roach.angle + random.uniform(-1.2, 1.2))
                base_sp = random.uniform(roach.min_walk_speed, roach.max_walk_speed)
                roach.target_speed = base_sp * roach.circadian_factor
                next_duration = random.uniform(2.0, 4.0)

            self._decision_timers[roach.id] = next_duration
        else:
            self._decision_timers[roach.id] = timer

    @staticmethod
    def _nearest_edible(roach: Cockroach, foods: List[Food]) -> Optional[Food]:
        best: Optional[Food] = None
        best_d = 1e9
        for f in foods:
            if f.nutrition <= 1.0:
                continue
            d = roach.pos.distance_to(f.pos)
            if d < best_d:
                best_d = d
                best = f
        return best

    def _pick_food(
        self, roach: Cockroach, foods: List[Food]
    ) -> Optional[tuple[Food, float]]:
        """挑一块还有空位的食物；满员的跳过（避免一窝蜂叠在同一个点上）。"""
        best: Optional[Food] = None
        best_d = 280.0
        for f in foods:
            if f.nutrition <= 1.0:
                continue
            if not f.has_room(roach.id):
                continue
            d = roach.pos.distance_to(f.pos)
            if d < best_d:
                best_d = d
                best = f
        return (best, best_d) if best is not None else None

    def _tick_eating(self, roach: Cockroach, dt: float) -> None:
        """进食推进：每 0.4s 咬一口，食物体积随之缩小。"""
        food = roach.eating_food
        roach.target_speed = 0.0
        if food is None or not food.is_alive or food.nutrition <= 0.5:
            roach.eating_food = None
            roach.state = EntityState.WALK
            roach.target_speed = roach.min_walk_speed
            return

        roach.target_angle = (food.pos - roach.pos).angle()
        if roach.energy >= 99.0:
            food.release(roach.id)
            roach.eating_food = None
            roach.state = EntityState.WALK
            return

        roach.bite_timer -= dt
        if roach.bite_timer <= 0.0:
            roach.bite_timer = 0.4
            eaten = food.consume(6.0)
            if eaten > 0.0:
                roach.feed(eaten)
                roach.bite()
            else:
                roach.eating_food = None
                roach.state = EntityState.WALK

    def cleanup_entity(self, entity_id: str) -> None:
        self._decision_timers.pop(entity_id, None)
