"""高拟真运动学与屏幕物理边界系统。

实现：
- 趋边性力场 (Thigmotaxis): 贴边游走与避旷偏好；
- 同类防重叠柔和排斥 (Separation Steering)；
- 三足步态关联物理积分与平滑转向。
"""

from __future__ import annotations
import math
import random
from typing import List

from ..entities.base_entity import BaseEntity, EntityState
from ..entities.cockroach import Cockroach
from ..utils.math_utils import Vector2D, clamp, angle_diff, normalize_angle


class MovementSystem:
    """管理高拟真物理动力学、趋边性与边界约束。"""

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        boundary_padding: float = 45.0,
        boundary_behavior: str = "rebound",
        thigmotaxis_weight: float = 0.55,
    ) -> None:
        self.screen_width = float(screen_width)
        self.screen_height = float(screen_height)
        self.padding = boundary_padding
        self.boundary_behavior = boundary_behavior
        self.thigmotaxis_weight = thigmotaxis_weight

    def update_screen_size(self, width: int, height: int) -> None:
        self.screen_width = float(width)
        self.screen_height = float(height)

    def update(self, entities: List[BaseEntity], dt: float) -> None:
        """对所有存活生物执行运动更新。"""
        roaches = [e for e in entities if e.is_alive and isinstance(e, Cockroach)]
        # 同类排斥用空间网格索引：O(n²) 在 200 只时是 4 万次距离计算（实测 7.8ms/帧），
        # 网格化后每个个体只看 3×3 邻格，基本退化成 O(n·k)。
        grid = self._build_grid(roaches)

        for entity in roaches:
            # 0. 被鼠标捏在手上的个体：位移由拖拽弹簧接管，这里不参与任何积分，
            #    否则趋边性、边界反弹会和拖拽打架（表现为抖动 / 甩不出去）
            if entity.state == EntityState.DRAGGED:
                continue

            # 1. 计算趋边性（Thigmotaxis）引导角
            if entity.state != EntityState.PANIC and self.thigmotaxis_weight > 0.05:
                self._apply_thigmotaxis(entity, dt)

            # 2. 计算同类近距离防重叠排斥（只算邻近格子里的邻居）
            self._apply_crowd_separation(entity, self._neighbors(grid, entity), dt)

            # 3. 平滑朝向插值
            turn_speed = entity.panic_turn_rate if entity.state == EntityState.PANIC else entity.turn_rate
            diff = angle_diff(entity.angle, entity.target_angle)
            max_rotation = turn_speed * dt

            if abs(diff) <= max_rotation:
                entity.angle = entity.target_angle
            else:
                entity.angle = normalize_angle(entity.angle + math.copysign(max_rotation, diff))

            # 4. 速率平滑逼近
            eff_target_speed = entity.target_speed
            if entity.is_carrying_ootheca:
                eff_target_speed *= 0.85
            if entity.energy < 20.0:
                eff_target_speed *= 0.65

            accel = 1400.0 if entity.state == EntityState.PANIC else 450.0
            if entity.current_speed < eff_target_speed:
                entity.current_speed = min(eff_target_speed, entity.current_speed + accel * dt)
            elif entity.current_speed > eff_target_speed:
                entity.current_speed = max(eff_target_speed, entity.current_speed - accel * dt)

            # 5. 沿当前身体实际角度更新位移
            entity.velocity = Vector2D.from_polar(entity.current_speed, entity.angle)
            entity.pos = entity.pos + entity.velocity * dt

            # 6. 边界约束
            self._handle_boundaries(entity)

    def _apply_thigmotaxis(self, roach: Cockroach, dt: float) -> None:
        """趋边性：靠近屏幕边缘时，产生沿墙壁游走的转向诱导力。"""
        edge_zone = 110.0  # 边缘感知带 (像素)
        w, h = self.screen_width, self.screen_height

        d_left = roach.x - self.padding
        d_right = (w - self.padding) - roach.x
        d_top = roach.y - self.padding
        d_bottom = (h - self.padding) - roach.y

        desired_ang: float | None = None

        if d_left < edge_zone:
            # 贴着左边缘上下爬行
            desired_ang = math.pi / 2.0 if math.sin(roach.angle) > 0 else -math.pi / 2.0
        elif d_right < edge_zone:
            # 贴着右边缘
            desired_ang = math.pi / 2.0 if math.sin(roach.angle) > 0 else -math.pi / 2.0
        elif d_top < edge_zone:
            # 贴着上边缘左右爬行
            desired_ang = 0.0 if math.cos(roach.angle) > 0 else math.pi
        elif d_bottom < edge_zone:
            # 贴着底边
            desired_ang = 0.0 if math.cos(roach.angle) > 0 else math.pi

        if desired_ang is not None:
            # 平滑将目标角度向贴壁方向倾斜
            diff = angle_diff(roach.target_angle, desired_ang)
            steer = diff * self.thigmotaxis_weight * dt * 2.0
            roach.target_angle = normalize_angle(roach.target_angle + steer)

    #: 同类排斥的作用半径，同时也是网格单元边长
    SEPARATION_DIST = 48.0

    def _build_grid(self, roaches: List[Cockroach]) -> dict[tuple[int, int], list[Cockroach]]:
        cell = self.SEPARATION_DIST
        grid: dict[tuple[int, int], list[Cockroach]] = {}
        for r in roaches:
            key = (int(r.pos.x // cell), int(r.pos.y // cell))
            bucket = grid.get(key)
            if bucket is None:
                grid[key] = [r]
            else:
                bucket.append(r)
        return grid

    def _neighbors(
        self, grid: dict[tuple[int, int], list[Cockroach]], roach: Cockroach
    ) -> List[Cockroach]:
        cell = self.SEPARATION_DIST
        cx = int(roach.pos.x // cell)
        cy = int(roach.pos.y // cell)
        out: List[Cockroach] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = grid.get((cx + dx, cy + dy))
                if bucket:
                    out.extend(bucket)
        return out

    def _apply_crowd_separation(
        self,
        roach: Cockroach,
        neighbors: List[Cockroach],
        dt: float,
    ) -> None:
        """同类微弱防重叠斥力。"""
        min_sep_dist = self.SEPARATION_DIST
        px, py = roach.pos.x, roach.pos.y
        for other in neighbors:
            if other is roach:
                continue
            ox, oy = other.pos.x, other.pos.y
            dx = px - ox
            dy = py - oy
            d2 = dx * dx + dy * dy
            if 0.01 < d2 < min_sep_dist * min_sep_dist:
                d = math.sqrt(d2)
                repel_ang = math.atan2(dy, dx)
                diff = angle_diff(roach.target_angle, repel_ang)
                # 施加微弱排斥避让
                roach.target_angle = normalize_angle(
                    roach.target_angle + diff * 1.5 * dt * (1.0 - d / min_sep_dist) * 2.0
                )

    def _handle_boundaries(self, entity: BaseEntity) -> None:
        """处理屏幕边缘。"""
        min_x = self.padding
        min_y = self.padding
        max_x = max(min_x + 100.0, self.screen_width - self.padding)
        max_y = max(min_y + 100.0, self.screen_height - self.padding)

        rebounded = False
        new_x = clamp(entity.x, min_x, max_x)
        new_y = clamp(entity.y, min_y, max_y)

        if entity.x <= min_x or entity.x >= max_x:
            entity.target_angle = normalize_angle(math.pi - entity.angle + random.uniform(-0.3, 0.3))
            entity.angle = entity.target_angle
            rebounded = True

        if entity.y <= min_y or entity.y >= max_y:
            entity.target_angle = normalize_angle(-entity.angle + random.uniform(-0.3, 0.3))
            entity.angle = entity.target_angle
            rebounded = True

        entity.x = new_x
        entity.y = new_y

        if rebounded and entity.state == EntityState.PANIC:
            entity.current_speed *= 0.82
