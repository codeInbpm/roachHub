"""食物类型表（恶心版）。

为什么单独一个模块：食物的"营养 / 体积 / 进食位点 / 造型 / 保质期"是纯数据，
绘制与行为都只读它。加一种新食物 = 往 ``FOOD_KINDS`` 里加一行。

两件事和上一版不同：

1. **造型换成贴图**。20 种食物各有一张真实照片质感的 PNG（见
   ``assets/sprites/food/``，由 ``tools/prepare_food_sprites.py`` 从 AI 素材
   图裁切而来）。``sprite`` 字段就是文件名，运行时不加载就不占内存。
2. **加了保质期**。``shelf_life`` 是"从新鲜到彻底腐败"的秒数，湿食短、干货长。
   腐败过程会持续流失营养、变暗、长霉、爬蛆，到头来化成一坨干掉。

营养值仍然按"成虫每秒烧 0.6 能量"折过：一块 45 营养 ≈ 一只虫活 75 秒。
整张表的加权期望压在 41 一带（旧版 42），免得多加几种食物就把种群承载
上限抬到不可控 —— 详见 ``docs/food.md``。
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class FoodKind:
    """一种食物（或者说"一块脏东西"）的静态定义。"""

    key: str
    name: str
    icon: str
    nutrition: float          # 总营养（= 一只虫能获得的能量总量）
    radius: float             # 视觉基准半径（像素）
    slots: int                # 同时进食位点数
    shelf_life: float         # 保质期（秒）：从新鲜到彻底腐败
    sprite: str               # 贴图文件名（assets/sprites/food/<sprite>.png）
    body: tuple[int, int, int]    # 主体色（贴图缺失时的兜底色 / 腐败染色基准）
    edge: tuple[int, int, int]    # 暗部色
    accent: tuple[int, int, int]  # 霉斑色
    note: str = ""            # 面板里的一行说明
    # 腐败到多少才开始爬蛆。默认 0.55；"蛆团"这种本来就该有蛆的设成 0，
    # 否则新鲜状态下它就是一块安静的白色脂肪，完全不像名字。
    grub_min_rot: float = 0.55

    @property
    def ratio_nutrition(self) -> str:
        return f"{self.nutrition:.0f}"


# 按营养从高到低排，下拉框里天然就是"大补 → 塞牙缝"的顺序。
FOOD_KINDS: tuple[FoodKind, ...] = (
    # ---------------------------------------------------------------- 高能：尸体与腐肉
    FoodKind("carrion", "腐肉块", "🥩", 78, 34.6, 3, 100, "carrion",
             (122, 74, 66), (74, 42, 38), (140, 158, 128),
             "最臭的一块，蟑螂最认这个味"),
    FoodKind("maggots", "蛆虫团", "🪱", 72, 34.0, 3, 130, "maggots",
             (232, 226, 206), (176, 168, 148), (150, 160, 128),
             "高蛋白，扭来扭去", grub_min_rot=0.0),
    FoodKind("caterpillar", "死毛毛虫", "🐛", 70, 33.3, 2, 220, "caterpillar",
             (154, 168, 104), (98, 112, 60), (132, 148, 118),
             "绿油油一条，还是软的"),
    FoodKind("moldcheese", "发霉奶酪", "🧀", 64, 32.0, 2, 190, "moldcheese",
             (226, 214, 156), (150, 140, 92), (112, 140, 122),
             "长了蓝绿霉，油脂还很高"),
    FoodKind("moldpeanut", "霉花生", "🥜", 62, 25.4, 1, 300, "moldpeanut",
             (196, 158, 118), (132, 100, 68), (168, 176, 160),
             "黄曲霉那种白毛，别吃（它吃）"),
    FoodKind("roachnymph", "蟑螂若虫尸", "🪳", 58, 29.2, 2, 240, "roachnymph",
             (176, 146, 108), (118, 92, 62), (150, 128, 96),
             "同类相食，补得很"),
    FoodKind("droppings", "老鼠屎", "💩", 56, 23.9, 1, 380, "droppings",
             (70, 52, 38), (38, 28, 22), (96, 78, 58),
             "干货，存得久"),

    FoodKind("millipede", "死马陆", "🐛", 58, 31.0, 2, 210, "millipede",
             (128, 116, 100), (74, 66, 56), (146, 152, 128),
             "百来条腿，卷成一圈"),
    FoodKind("slug", "死蛞蝓", "🐌", 50, 29.0, 2, 140, "slug",
             (150, 142, 118), (92, 86, 68), (158, 168, 132),
             "一摊鼻涕样的东西，超湿"),

    # ---------------------------------------------------------------- 中能
    FoodKind("woodlouse", "死鼠妇", "🪲", 54, 30.7, 2, 250, "woodlouse",
             (150, 146, 136), (92, 90, 84), (128, 132, 116),
             "潮虫一具，壳还没散"),
    FoodKind("moldbread", "霉面包", "🍞", 52, 32.0, 2, 240, "moldbread",
             (226, 216, 190), (156, 146, 122), (118, 146, 132),
             "绿毛都长成一层了"),
    FoodKind("rottentomato", "烂番茄", "🍅", 46, 32.0, 2, 160, "rottentomato",
             (168, 62, 54), (100, 34, 30), (150, 162, 120),
             "塌成一摊，汁水还在渗"),
    FoodKind("stalerice", "馊饭团", "🍚", 44, 29.2, 2, 280, "stalerice",
             (226, 222, 200), (160, 156, 134), (140, 148, 120),
             "结成一坨，有酸味"),
    FoodKind("moldbiscuit", "霉饼干", "🍪", 42, 27.9, 2, 260, "moldbiscuit",
             (212, 186, 148), (146, 118, 86), (120, 148, 132),
             "碎成几块，长了霉点"),
    FoodKind("deadmoth", "死蛾子", "🦋", 40, 33.3, 2, 280, "deadmoth",
             (168, 160, 142), (108, 102, 88), (140, 136, 118),
             "翅膀上一层粉，一碰就散"),
    FoodKind("bananapeel", "烂香蕉皮", "🍌", 38, 32.0, 2, 180, "bananapeel",
             (92, 78, 52), (48, 40, 28), (130, 126, 92),
             "黑透了，黏手"),
    FoodKind("applecore", "烂苹果核", "🍎", 36, 29.2, 1, 170, "applecore",
             (148, 112, 74), (86, 62, 40), (132, 140, 116),
             "啃剩下的核，已经褐变"),

    # ---------------------------------------------------------------- 低能：零碎
    FoodKind("rottentomato2", "霉烂柿子", "🍅", 34, 30.7, 2, 165, "rottentomato2",
             (150, 70, 58), (88, 40, 34), (136, 154, 124),
             "整颗塌下去，霉从脐部起"),
    FoodKind("deadspider", "死蜘蛛", "🕷️", 32, 27.9, 1, 270, "deadspider",
             (96, 82, 68), (52, 44, 36), (120, 112, 96),
             "腿卷成一团，肉不多"),
    FoodKind("flea", "死跳蚤", "🦟", 20, 17.0, 1, 260, "flea",
             (108, 66, 48), (60, 34, 24), (126, 122, 104),
             "小得几乎看不见，一口就没"),
    FoodKind("deadfly", "死苍蝇", "🪰", 26, 27.0, 1, 260, "deadfly",
             (86, 90, 88), (44, 48, 48), (120, 126, 118),
             "翻着肚皮，翅膀还透亮"),
    FoodKind("slime", "黏液团", "💧", 22, 29.2, 2, 150, "slime",
             (186, 184, 138), (120, 120, 86), (150, 158, 120),
             "不知道是什么吐的，能舔"),
    FoodKind("moldpatch", "霉斑", "🦠", 18, 31.0, 1, 420, "moldpatch",
             (128, 152, 134), (72, 96, 82), (104, 132, 116),
             "纯菌落，营养少但到处都是"),
)

BY_KEY: dict[str, FoodKind] = {k.key: k for k in FOOD_KINDS}
DEFAULT_KEY: str = "moldbread"
RANDOM_KEY: str = "random"

# 托盘菜单里的"招牌"：每种腐败速度/营养档位各挑一个代表，让用户一眼能试到差异
SIGNATURE_KEYS: tuple[str, ...] = (
    "carrion", "maggots", "caterpillar", "droppings",
    "moldbread", "rottentomato", "moldcheese",
)


def get_kind(key: str | None) -> FoodKind:
    """按 key 取定义；未知 key 退回默认（配置里写错了不至于崩）。"""
    if not key:
        return BY_KEY[DEFAULT_KEY]
    return BY_KEY.get(key, BY_KEY[DEFAULT_KEY])


def pick_kind(key: str | None = None) -> FoodKind:
    """``random`` / ``None`` 时按营养值加权随机，其余按 key 取。

    加权而不是均匀随机：低营养的霉斑、死苍蝇更常见，腐肉和蛆团稀少一些，
    否则"随手撒一把"就能把种群喂爆。
    """
    if key and key != RANDOM_KEY:
        return get_kind(key)
    if key is None:
        return get_kind(DEFAULT_KEY)
    weights = [max(6.0, 90.0 - k.nutrition) for k in FOOD_KINDS]
    return random.choices(FOOD_KINDS, weights=weights, k=1)[0]


# 下拉框的三个营养档（按 FOOD_KINDS 已降序排列的事实切分）
_MENU_BANDS = (
    ("高能 · 尸体与腐肉", 60.0, 999.0),
    ("中能 · 主食残渣", 35.0, 60.0),
    ("低能 · 零碎", 0.0, 35.0),
)


def menu_items() -> list[tuple[str, str | None]]:
    """给下拉框用的 (label, key) 列表。

    第一项是随机混合，其后按营养分三档并插入**分组标题**（key 为 None，
    由 UI 置灰不可选）。23 项平铺滚起来找不着东西，分组后一眼能定位。
    """
    items: list[tuple[str, str | None]] = [("🎲 随机混合", RANDOM_KEY)]
    for title, lo, hi in _MENU_BANDS:
        # 手工插进去的几条不保证在表里的位置，这里统一按营养降序排
        group = sorted((k for k in FOOD_KINDS if lo <= k.nutrition < hi),
                       key=lambda k: -k.nutrition)
        if not group:
            continue
        items.append((f"──── {title} ────", None))
        items += [(f"{k.icon} {k.name}（{k.nutrition:.0f} · {k.shelf_life:.0f}s）", k.key)
                  for k in group]
    return items
