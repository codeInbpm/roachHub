# 繁衍模型：性别、求偶、受精与卵鞘

> 对应代码：`src/systems/reproduction.py`（核心）、`src/systems/behavior.py`（求偶驱力落地）、
> `src/systems/movement.py`（交配时不被打散）、`src/entities/cockroach.py`（状态机）。
> 验收：`tools/reproduction_test.py`（30 项）；测速：`tools/repro_probe.py`。

## 1. 为什么要改

改造前的 auto 模式是这样：`adults` 只按 `stage == ADULT` 筛选，**不看性别**。也就是说雄虫
也在产卵，且 `female_ratio`、`mating_search_radius` 两个参数在默认模式下完全是摆设 ——
界面上可调，代码里从不读取。

有性模式则基本产不出卵鞘，三个原因叠在一起：

1. 行为层没有 `MATING` 分支，`_update_cockroach_behavior` 每帧往下走，几秒内就把状态改回
   `WALK`，`mating_progress` 永远到不了 2.2s；
2. 光标感知在中距离（120px）无条件 `state = WALK`，等于"鼠标停在附近 = 全场停产"；
3. `interrupt_mating()` 只清 `partner_id` 不清 `state`：一旦被受惊打断、或伴侣被拍死，
   雌虫会永久停在 `MATING` —— 一副抽搐的定妆照，繁殖循环再也进不去。

另外对接距离写死 26px，而虫体长 70px：所谓"尾对尾"其实是两只虫叠在一起。

## 2. 模型：两条硬规则

```
只有雌虫产卵  +  必须已受精（broods_left > 0）
一次交配支撑 N 次产卵（fertilized_broods，默认 2）
```

`broods_left` 放在 `BaseEntity` 上：0 = 处女，不能产卵；产一枚扣 1；归零后必须重新交配。
真实蟑螂雌虫会储精，所以"交配一次连产几枚"比"每胎都找对象"更接近事实，也让雄虫变成
**节奏约束**而不是**每胎都必须到场的瓶颈**。

所有产出走同一条路：

```
交配完成 → broods_left = N（+ 双方能量/冷却结算）
        → 下一帧 _can_produce() 通过 → _spawn_ootheca()（扣能量、扣额度、定冷却/拖卵）
        → 15s 孵化 → 1~3 只若虫 → 4 龄 × 18s → 成虫（处女，重新开始）
```

### 两种模式的差别只在"交配仪式"

| | 简化交配 `auto` | 写实有性 `sexual` |
| --- | --- | --- |
| 对接距离 | `1.15 × 半体长和` ≈ 80px，尾部刚好相碰 | `0.92 × 半体长和` ≈ 64px，尾部实际贴合 |
| 时长 | `auto_mating_time` 默认 1.5s | `mating_duration` 默认 2.2s |
| 雌虫感知 | `mating_search_radius` 内 | 同左 |
| 雄虫感知 | 性信息素远程吸引（不设半径） | 同左 |
| 受惊打断 | 会（`r_panic` 内） | 会（`r_panic` 内） |
| `female_ratio` | **生效** | 生效 |

两种模式都要求"有一只可用雄虫"。雄虫侧不设半径上限是有意的：真实雄虫会顺着信息素
长距离搜索；同时避免"雌雄随机散落在 1920×1080 两端"的开局卡住几分钟。

### 求偶必须看得见

`ReproductionSystem` 每 0.25s 扫一次配对（200 只时约 0.14ms/帧），把目标点写进
`Cockroach.mate_seek`，由行为层消费：

```python
# behavior.py：优先级 惊慌 > 僵直 > 梳理 > 蜕皮 > 进食 > 交配 > 求偶 > 漫步/觅食
if roach.state == EntityState.MATING:
    roach.target_speed = 0.0
    return
if roach.mate_seek is not None:
    roach.state = EntityState.WALK
    roach.target_angle = (roach.mate_seek - roach.pos).angle()
    roach.target_speed = roach.min_walk_speed * 1.35 * roach.circadian_factor
    return
```

于是玩家看到的是"雄虫一路小跑找过去、对上、短暂僵住、分开"，而不是雌虫站着凭空
冒卵鞘。

## 3. 交配状态机（每一环都有人负责收尾）

```
            ┌─ 找不到雄性 ────────────────→ 继续漫游（mate_seek 清空）
待受精 ──→ 雌虫：半径内找 ─┐
            │              ├─ 距离 ≤ 对接距离 → _begin_mating()
            │              └─ 距离 >  对接距离 → mate_seek = 雄虫位置
            └─ 雄虫：全场找最近待受精雌虫 → 同上（远程信息素）

MATING ──→ 每帧 mating_progress += dt
        ├─ progress ≥ duration ──→ _finish_fertilize()：额度 N、双方回 WALK、雄虫冷却 0.6×
        └─ 任一方 PANIC/FREEZE/死亡/被拎走 ──→ _repair_pairs() 让另一方 interrupt_mating()
```

`_repair_pairs()` 每帧跑一次，是"绝不卡死"的兜底：不管哪一方以什么方式脱队，另一方
都会在同一帧被放回 `WALK`。

## 4. 参数

| 界面 | config.toml | 默认 | 作用 |
| --- | --- | --- | --- |
| 繁殖模式选择 | `reproduce_mode` | `auto` | 简化 / 写实 |
| 寻偶感应距离 | `mating_search_radius` | 180 | 雌虫发现雄虫的距离 |
| 雌性比例 | `female_ratio` | 0.5 | 直接决定增速上限（只有雌虫能产） |
| 繁殖所需最低能量 | `reproduce_min_energy` | 55 | 低于此值不产卵 |
| 每次产卵消耗能量 | `reproduce_cost_energy` | 25 | 满能量 100 只能连产 2 枚 |
| 成虫繁殖冷却 | `reproduce_cooldown` | 14 | 两枚卵鞘之间的间隔（拖卵式由孵化时间替代） |
| 单次交配可产卵次数 | `fertilized_broods` | 2 | 储精额度；调 1 = 每枚都要重新交配 |
| 简化交配耗时 | `auto_mating_time` | 1.5 | 简化模式的交配时长 |
| 尾对尾交尾耗时 | `mating_duration` | 2.2 | 写实模式的交配时长 |
| 卵鞘孵化时间 | `egg_incubation_time` | 15 | 也是拖卵母体的封锁时长 |
| 每鞘若虫数 | `nymphs_per_ootheca_min/max` | 1~3 | 唯一的乘法杠杆 |
| 每龄蜕皮耗时 | `instar_duration` | 18 | 4 龄 → 成虫 = 72s |
| 成虫寿命 | `adult_lifespan` | 300 | — |

> 直接投放的成虫按"野生成虫"处理，雌性带 1 次产卵额度（`app.spawn_cockroach`）；
> 由卵鞘孵化、自己长成的个体是处女，必须先交配。

## 5. 实测速率

`tools/repro_probe.py`，2 只成虫起手，60fps 定步长，跑真实系统（行为 + 运动 + 繁殖 + 生命周期）：

| 场景 | 改造前 | 改造后 |
| --- | --- | --- |
| 能量管够 | 195s 撞 200 上限，101 鞘/分钟 | 240s 到 85 只，**13 鞘/分钟** |
| 当前食物配置 | 峰值 42 只，稳态 20~40，5.1 鞘/分钟 | 峰值 18 只，稳态 **10~25**，**2.8 鞘/分钟** |
| 完全不给食物 | 一次性 4 鞘 / 9 只后全员饿死 | 一次性 2 鞘 / 4 只后饿死 |

速率下降的构成基本可以逐项对上：只有雌性（÷2）+ 求偶行走时间 + 雄虫冷却 + 雌虫在能量
耗尽前只能排空一次额度。也就是说**出生率降到了原来的 1/4 左右**，换来的是"雄虫真的有用"
和"能看到交配过程"。

如果觉得太慢，按性价比排序调这几个：`fertilized_broods`（2→4 直接翻倍，且不动能量账）
→ `reproduce_cost_energy`（25→15，让满能量能连产 4 枚）→ `reproduce_cooldown`，
最后才是 `female_ratio`（0.5→0.7 大约再快 40%）。

## 6. 改了什么

| 文件 | 改动 |
| --- | --- |
| `src/systems/reproduction.py` | 重写：性别筛选、求偶配对（节流）、受精额度、按体长的对接距离、配对修补 |
| `src/entities/base_entity.py` | 新增 `broods_left`（受精额度） |
| `src/entities/cockroach.py` | 新增 `mate_seek`；`interrupt_mating()` 现在会把状态放回 `WALK` |
| `src/systems/behavior.py` | 新增 `MATING` 早退分支；新增 `mate_seek` 求偶分支；光标感知不再把交配个体改回 `WALK`（但 `r_panic` 内仍可惊吓打断） |
| `src/systems/movement.py` | 交配中的个体跳过趋边性/同类排斥/惯性位移，只保留朝向对齐 |
| `src/app.py` | 投放的雌性成虫带 1 次额度；`console_stats` 增加 `unmated` |
| `src/settings_window.py` | 模式文案、感应距离文案、3 个新参数、状态栏「待受精 ♀N」 |
| `src/config.py` / `config.toml` | `fertilized_broods` / `auto_mating_time` / `mating_duration` |

## 7. 已知取舍

- **全是雌性时种群会停**：这是新模型的本意，但可能被误认为 bug。缓解：控制台显示
  「待受精 ♀N」、日志在 120s 后提示一次"没有可交配雄虫"。
- **光标停在交尾现场附近会拆散它们**：55px 内是设计上的"受惊打断"；120px 内不再拆散，
  但会让它们保持警惕不敢靠拢。想要"围观也照配"，把 `perception_panic_radius` 调小。
- **帽顶（max_roaches）时仍会继续产卵**：`allow_reproduce_near_cap` 目前只放开"产"，
  孵化侧仍按剩余容量截断，因此到顶后会堆积永不孵化的卵鞘（实测 240s 时 122 枚）。
  这属于密度反馈（D 方向）的活，本次没动。
- **饥饿死亡仍是暴毙**：`consume_energy` 在能量归零后每帧扣 2.5 血，约 0.7s 死亡。
  同样留待 D 方向处理。

## 8. 验收

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/reproduction_test.py   # 45 项
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/repro_probe.py --food natural --seconds 600
```

覆盖点：处女不产卵、雄虫寻偶、额度扣减、对接距离量级、打断不死锁、伴侣死亡不死锁、
行为层不拆散交配、贴边不被推走、光标中距离不打断、全雄不产卵、应用层配置与统计接线。

---

## 9. 交配动作与视觉（2026-11-16）

> 起因：交配原本是整套系统里**最安静**的状态 —— 两只虫尾对尾站 2.2 秒，
> 姿态目标里只有一个 `tremor=0.45`，画面上是一个暗色小黏液点在高频闪。
> 玩家看不出它们在干什么，也看不出还要多久。

### 9.1 动作：挺动 / 扭摆 / 挤压

真实蟑螂交尾时雄性沿体轴反复挺动，频率由慢到快，末端一阵急促，最后抽离。
拆成四个量，全部由 `u = progress / duration` **直接算**（不累积状态，
暂停、改时长、乱序调用都不会漂）：

| 量 | 公式 | 视觉 |
| --- | --- | --- |
| `phase` | `2π·duration·∫freq(u)du`，freq 1.6→4.8Hz | 拍子 |
| `push` | `4.2px · env(u) · sin(phase)` | 前后顶动 |
| `twist` | `1.8° · env · cos(phase)`，绕尾端 | 头部划弧（与顶动错开 90°） |
| `squeeze` | `1 ± 3.2%`（雌虫 2%） | 腹部收缩 |

两个细节是踩出来的：

- **相位必须解析积分。** freq 随进度变化，若直接写 `sin(2π·freq(u)·t)`，
  变速点相位会突变 —— 表现为"抽搐一下"。积分是闭式的：
  `∫smoothstep = (b-a)(t³ - t⁴/2)`，所以相位仍是 u 的纯函数。
- **绕尾端扭，而不是整体转。** 尾部是锁合点，扭摆的支点必须在那儿；
  绕体心转会像整只虫被拨了一下。1.8° 在 70px 体长上让头划出 ±2px 的弧，
  30fps 下能看清"在用力"。

幅度包络：前 10% 从 0 涨起（对接），后 12% 落回 0，并且**额外后退 9px**（抽离）。
最初只做了"落回 0"，结束瞬间两只虫贴在一起突然定住，像卡带。

绘制位移只改 `painter.translate`，**不动 `self.pos`** —— 配对距离、点击命中、
足迹采样都还按真实坐标算，否则会引发一串"虫被自己推出的位置判定错"。

### 9.2 样式：光晕 / 波纹 / 心形

| 元素 | 做法 | 为什么这么做 |
| --- | --- | --- |
| 结合处光晕 | 预渲染径向渐变贴图，半径随拍呼吸，亮度随拍明暗 | 每帧构造 QRadialGradient 是这一段最贵的操作，而形状根本不变 |
| 扩散波纹 | 每拍一圈，年龄 = `frac(phase/2π)` | 4.8Hz 时 0.2 秒扩完，连起来就是心跳 |
| 上浮心形 | 预渲染 QPixmap，两段三次贝塞尔 | 不用字符 "♥"：离屏平台字体数为 0，字符会画成方框 |

三条硬约束：

1. **只在雄虫身上画。** 两头各画一份会叠成双倍亮度，看起来像过曝。
2. **心形在世界坐标里飘**（`setTransform(world_tf)`）。两只虫尾对尾、朝向相反，
   雄性转了 180°，局部 -y 其实是屏幕下方 —— 用局部坐标做"上浮"会让心形
   钻进自己身体里；横着配对的虫更乱（`tools/out/mating_angles.png` 用
   0/35/90/135 四个朝向验证过）。
3. **心形单独一遍画在所有虫体之后。** 两只虫是分别 render 的，
   先画的那只撒的心形会被后画的那只挡掉一半。

### 9.3 参数与开销

| 参数 | 值 | 备注 |
| --- | --- | --- |
| `FREQ_BASE / FREQ_SWING` | 1.6 / 3.2 Hz | 末端约 4.8Hz，2.2 秒共约 6 拍 |
| `PUSH_PX` | 4.2px | 体长 6%。2.2px 时截图里 8 帧像同一张 |
| `TWIST_DEG` | 1.8° | |
| `DETACH_PX` | 9px | 最后 12% 退开 |
| `HEART_MAX / LIFE` | 8 颗 / 1.2~1.8s | 稳态 4~5 颗 |

实测（1600×900，quality=2）：一对交配 **0.93ms/帧**，同两只 `WALK` 是 0.81ms，
**特效本身约 0.12ms/对**。`quality == 0`（>110 只的极简档）直接跳过全部特效。

### 9.4 悬停卡片

`交尾中` 改成 `交尾中 · 62%` —— 交配是唯一"有明确终点"的状态，
光写"交尾中"看不出还要等多久。

### 9.5 工具

```bash
python tools/mating_preview.py     # 出 mating_frames / mating_closeup / mating_angles
```

- `mating_frames.png`：2.2 秒抓 8 帧，看律动与抽离；
- `mating_closeup.png`：55% 时结合处放大 2.2 倍，看光晕/波纹/心形；
- `mating_angles.png`：0/35/90/135 四个朝向，验证心形永远朝屏幕上方飘；
- `mating_bg_light.png` / `mating_bg_dark.png`：浅色与深色桌面各一张 ——
  暖光在浅底上容易糊成白雾，只有深底才看得出"发光"，两张都要过一眼。

> 这几个图不要加 `QT_QPA_PLATFORM=offscreen` —— 该平台字体数为 0，标签会变方框。
