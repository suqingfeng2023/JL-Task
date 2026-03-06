import json
import math
from shapely.geometry import Polygon, LineString, Point, box
from shapely.affinity import rotate, translate
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from typing import List, Tuple, Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import os


# ============================================================================
# 枚举和数据类型定义
# ============================================================================

class ItemType(Enum):
    """物品类型枚举"""
    FRIDGE = "fridge"
    SHELF = "shelf"
    OVER_SHELF = "overShelf"
    ICE_MAKER = "iceMaker"


@dataclass
class ItemDefinition:
    """物品定义 - 需要放置的物品的基本信息"""
    name: str
    length: float  # 长度（毫米）
    width: float  # 宽度（毫米）
    clearance: Optional[float] = None  # 净空区深度（毫米），仅冰箱需要

    @property
    def type(self) -> ItemType:
        """根据名称判断物品类型"""
        name_lower = self.name.lower()
        if 'fridge' in name_lower:
            return ItemType.FRIDGE
        elif 'overshelf' in name_lower:
            return ItemType.OVER_SHELF
        elif 'shelf' in name_lower:
            return ItemType.SHELF
        elif 'icemaker' in name_lower:
            return ItemType.ICE_MAKER
        return ItemType.SHELF

    @property
    def area(self) -> float:
        """物品面积"""
        return self.length * self.width


@dataclass
class PlacedItem:
    """已放置的物品 - 包含位置和几何信息"""
    name: str
    polygon: Polygon  # 物品的多边形表示
    center: Tuple[float, float]  # 中心点坐标
    rotation: float  # 旋转角度（度）
    length: float  # 原始长度
    width: float  # 原始宽度
    item_type: ItemType
    clearance_zone: Optional[Polygon] = None  # 净空区多边形（仅冰箱）


@dataclass
class WallEdge:
    """墙边信息"""
    start: Tuple[float, float]  # 起点坐标
    end: Tuple[float, float]  # 终点坐标
    length: float  # 边长（毫米）
    direction_vector: Tuple[float, float]  # 方向向量（单位向量）
    angle: float  # 与X轴的夹角（度）
    inward_normal: Tuple[float, float]  # 指向房间内部的法向量
    is_door_edge: bool  # 是否包含门的边
    line: LineString  # 边的线段表示


@dataclass
class PlacementCandidate:
    """放置候选位置"""
    center: Tuple[float, float]
    rotation: float
    polygon: Polygon
    clearance_zone: Optional[Polygon] = None
    score: float = 0.0
    edge_info: str = ""


# ============================================================================
# 门禁区计算器
# ============================================================================

class DoorForbiddenZoneCalculator:
    """计算门打开时的禁止区域"""

    def __init__(self, door_start: Tuple[float, float],
                 door_end: Tuple[float, float],
                 is_open_inward: bool,
                 room_boundary: Polygon):
        """
        初始化门禁区计算器

        参数:
            door_start: 门起点
            door_end: 门终点
            is_open_inward: 门是否向内开
            room_boundary: 房间边界多边形
        """
        self.door_start = door_start
        self.door_end = door_end
        self.is_open_inward = is_open_inward
        self.room_boundary = room_boundary

        # 计算门的线段和宽度
        self.door_line = LineString([door_start, door_end])
        self.door_width = self.door_line.length

    def calculate_forbidden_zone(self) -> Polygon:
        """计算门的禁止区域（门打开时占用的空间）"""
        # 计算门的方向向量和长度
        dx = self.door_end[0] - self.door_start[0]
        dy = self.door_end[1] - self.door_start[1]
        door_length = math.sqrt(dx * dx + dy * dy)

        # 计算单位方向向量
        door_direction = (dx / door_length, dy / door_length)

        # 计算两个可能的法向量（垂直于门的方向）
        # 法向量1: 逆时针旋转90度
        normal_1 = (-door_direction[1], door_direction[0])
        # 法向量2: 顺时针旋转90度
        normal_2 = (door_direction[1], -door_direction[0])

        # 确定哪个法向量指向房间内部
        inward_normal = self._determine_inward_normal(normal_1, normal_2)

        # 根据开门方向计算禁区深度
        if self.is_open_inward:
            # 内开门：禁区深度 = 门宽度的一半
            forbidden_depth = self.door_width / 2
        else:
            # 外开门：禁区深度 = 500mm  自定义的
            forbidden_depth = 500.0

        # 构建禁区多边形
        forbidden_zone = self._build_forbidden_zone_polygon(
            self.door_start,
            self.door_end,
            inward_normal,
            forbidden_depth
        )

        return forbidden_zone

    def _determine_inward_normal(self, normal_1: Tuple[float, float],
                                 normal_2: Tuple[float, float]) -> Tuple[float, float]:
        """
        确定指向房间内部的法向量
        方法: 在门的中点沿两个法向量方向偏移一定距离，检查哪个点在房间内
        """
        # 计算门的中点
        mid_x = (self.door_start[0] + self.door_end[0]) / 2
        mid_y = (self.door_start[1] + self.door_end[1]) / 2
        mid_point = Point(mid_x, mid_y)

        # 测试点偏移距离
        test_offset = 500.0

        # 创建两个测试点
        test_point_1 = Point(
            mid_x + normal_1[0] * test_offset,
            mid_y + normal_1[1] * test_offset
        )

        test_point_2 = Point(
            mid_x + normal_2[0] * test_offset,
            mid_y + normal_2[1] * test_offset
        )

        # 检查哪个测试点在房间内
        if self.room_boundary.contains(test_point_1):
            return normal_1
        else:
            return normal_2

    def _build_forbidden_zone_polygon(self,
                                      door_start: Tuple[float, float],
                                      door_end: Tuple[float, float],
                                      inward_normal: Tuple[float, float],
                                      depth: float) -> Polygon:
        """构建禁区多边形 禁区是一个四边形"""
        # 计算禁区的四个顶点
        point_1 = door_start
        point_2 = door_end
        point_3 = (
            door_end[0] + inward_normal[0] * depth,
            door_end[1] + inward_normal[1] * depth
        )
        point_4 = (
            door_start[0] + inward_normal[0] * depth,
            door_start[1] + inward_normal[1] * depth
        )

        forbidden_zone = Polygon([point_1, point_2, point_3, point_4])
        if not forbidden_zone.is_valid:
            forbidden_zone = forbidden_zone.buffer(0)

        return forbidden_zone


# ============================================================================
# 墙边检测器
# ============================================================================

class WallEdgeDetector:
    """检测房间的墙边并计算相关属性"""

    def __init__(self, boundary_polygon: Polygon, door_start: Tuple[float, float], door_end: Tuple[float, float]):
        """
        初始化墙边检测器

        参数:
            boundary_polygon: 房间边界多边形
        """
        self.boundary = boundary_polygon
        self.door_start = door_start
        self.door_end = door_end

    def detect_edges(self, door_line: LineString) -> List[WallEdge]:
        """
        检测所有墙边

        步骤:
        1. 获取边界多边形的所有顶点
        2. 遍历相邻顶点构建边
        3. 计算每条边的属性
        """
        # 获取边界顶点坐标
        if self.boundary.exterior is None:
            return []

        coords = list(self.boundary.exterior.coords)
        edges = []

        # 遍历每对相邻顶点
        for i in range(len(coords) - 1):
            start = coords[i]
            end = coords[i + 1]

            # 计算边的属性
            edge = self._compute_edge_properties(start, end)
            edges.append(edge)

        # 按长度降序排序
        edges.sort(key=lambda e: -e.length)

        return edges

    def _compute_edge_properties(self,
                                 start: Tuple[float, float],
                                 end: Tuple[float, float]) -> WallEdge:
        """
        计算一条边的所有属性

        计算的内容:
        1. 边的长度
        2. 方向向量和角度
        3. 指向房间内部的法向量
        4. 是否包含门
        """
        # 计算边的向量
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.sqrt(dx * dx + dy * dy)

        # 计算单位方向向量
        if length > 0:
            direction_vector = (dx / length, dy / length)
        else:
            direction_vector = (0, 0)

        # 计算与X轴的夹角（度）
        angle = math.degrees(math.atan2(dy, dx))

        # 计算指向房间内部的法向量
        inward_normal = self._calculate_inward_normal(start, end, direction_vector)

        # 检查这条边是否包含门
        is_door_edge = self._check_if_door_edge(start, end, self.door_start, self.door_end)

        # 创建边的线段表示
        line = LineString([start, end])

        return WallEdge(
            start=start,
            end=end,
            length=length,
            direction_vector=direction_vector,
            angle=angle,
            inward_normal=inward_normal,
            is_door_edge=is_door_edge,
            line=line
        )

    def _calculate_inward_normal(self,
                                 start: Tuple[float, float],
                                 end: Tuple[float, float],
                                 direction: Tuple[float, float]) -> Tuple[float, float]:
        """
        计算指向房间内部的法向量

        方法:
        1. 计算两个可能的法向量（垂直方向）
        2. 取边的中点
        3. 沿两个法向量方向偏移，判断哪个点在房间内
        """
        # 计算两个可能的法向量
        normal_1 = (-direction[1], direction[0])  # 逆时针90度
        normal_2 = (direction[1], -direction[0])  # 顺时针90度

        # 计算边的中点
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2

        # 测试点偏移距离
        test_offset = 200.0

        # 创建两个测试点
        test_point_1 = Point(
            mid_x + normal_1[0] * test_offset,
            mid_y + normal_1[1] * test_offset
        )

        test_point_2 = Point(
            mid_x + normal_2[0] * test_offset,
            mid_y + normal_2[1] * test_offset
        )

        # 判断哪个点在房间内
        if self.boundary.contains(test_point_1):
            return normal_1
        else:
            return normal_2

    def _check_if_door_edge(self,
                               start: Tuple[float, float],
                               end: Tuple[float, float],
                               door_start: Tuple[float, float],
                               door_end: Tuple[float, float]) -> bool:
        """检查这条边是否包含门（使用投影距离）"""
        # 创建墙边线段
        wall_edge = LineString([start, end])

        # 创建门端点
        door_point1 = Point(door_start)
        door_point2 = Point(door_end)

        # 设置容差
        tolerance = 2.0

        # 使用投影
        # 找到门端点在墙边上的垂足
        proj_dist1 = wall_edge.project(door_point1)
        proj_point1 = wall_edge.interpolate(proj_dist1)

        proj_dist2 = wall_edge.project(door_point2)
        proj_point2 = wall_edge.interpolate(proj_dist2)

        # 计算端点到垂足的距离
        dist1 = door_point1.distance(proj_point1)
        dist2 = door_point2.distance(proj_point2)

        # 如果两个端点到垂足的距离都小于容差，说明门在这条边上
        return dist1 < tolerance and dist2 < tolerance


# ============================================================================
# 几何工具类
# ============================================================================

class GeometryUtils:
    """几何计算工具类"""
    @staticmethod
    def create_rectangle_polygon(center: Tuple[float, float],
                                 length: float,
                                 width: float,
                                 rotation: float) -> Polygon:
        """
        创建矩形多边形
        步骤:
        1. 在原点创建矩形（中心在原点）
        2. 旋转矩形
        3. 平移到指定中心点
        """
        # 创建以原点为中心的矩形
        half_length = length / 2
        half_width = width / 2
        rectangle_at_origin = box(-half_length, -half_width, half_length, half_width)

        # 旋转矩形
        rotated_rectangle = rotate(rectangle_at_origin, rotation, origin=(0, 0))

        # 平移到指定位置
        final_rectangle = translate(rotated_rectangle, center[0], center[1])

        return final_rectangle

    @staticmethod
    def create_clearance_zone(center: Tuple[float, float],
                              length: float,
                              width: float,
                              inward_normal: Tuple[float, float],
                              clearance_depth: float) -> Polygon:
        """
        创建冰箱的净空区（朝向房间内部）
        净空区是从冰箱前表面向房间内部延伸的区域
        """
        # 计算冰箱前表面的中心点
        # 从冰箱中心沿着内法向移动width/2距离
        front_center_x = center[0] + inward_normal[0] * (width / 2)
        front_center_y = center[1] + inward_normal[1] * (width / 2)

        # 计算净空区的中心点
        # 从前表面中心继续沿着内法向移动clearance_depth/2距离
        clearance_center_x = front_center_x + inward_normal[0] * (clearance_depth / 2)
        clearance_center_y = front_center_y + inward_normal[1] * (clearance_depth / 2)

        # 计算旋转角度
        # 净空区应该与冰箱前表面平行，所以使用内法向的角度
        rotation = math.degrees(math.atan2(inward_normal[1], inward_normal[0]))

        # 创建净空区矩形
        # 长度等于冰箱长度，宽度等于净空深度
        half_length = length / 2
        half_depth = clearance_depth / 2

        # 创建以原点为中心的矩形
        clearance_rect = box(-half_length, -half_depth, half_length, half_depth)

        # 旋转矩形使其法向对齐（需要加90度，因为box默认是沿坐标轴方向）
        clearance_rect = rotate(clearance_rect, rotation + 90, origin=(0, 0))

        # 平移到计算出的中心点
        clearance_rect = translate(clearance_rect, clearance_center_x, clearance_center_y)

        return clearance_rect

    @staticmethod
    def is_polygon_fully_inside(inner_polygon: Polygon,
                                outer_polygon: Polygon,
                                threshold: float = 1.0) -> bool:
        """
        检查多边形是否完全在另一个多边形内部
        步骤:
        1. 确保多边形有效
        2. 计算交集面积
        3. 计算面积比例
        4. 判断是否超过阈值
        """
        # 确保多边形有效
        if not inner_polygon.is_valid:
            inner_polygon = inner_polygon.buffer(0)

        if not outer_polygon.is_valid:
            outer_polygon = outer_polygon.buffer(0)

        # 计算交集
        try:
            intersection = outer_polygon.intersection(inner_polygon)
        except Exception:
            return False

        # 计算面积比例
        inner_area = inner_polygon.area
        if inner_area <= 0:
            return False

        intersection_area = intersection.area
        ratio = intersection_area / inner_area

        # 判断是否超过阈值
        return ratio >= threshold


class CollisionDetector:
    """碰撞检测器"""
    def __init__(self, room_boundary: Polygon, door_forbidden_zone: Polygon):
        self.room_boundary = room_boundary
        self.door_forbidden_zone = door_forbidden_zone
        self.placed_items: List[PlacedItem] = []

    def add_placed_item(self, item: PlacedItem):
        """添加已放置的物品"""
        self.placed_items.append(item)

    def check_placement_valid(self,
                              item_polygon: Polygon,
                              clearance_zone: Optional[Polygon] = None,
                              area_threshold: float = 100.0) -> bool:
        """
        综合检查放置是否有效
        检查项:
        1. 物品是否完全在房间内
        2. 净空区是否完全在房间内（如果有）
        3. 是否与门禁区碰撞
        4. 是否与已放置物品碰撞
        """
        # 检查物品是否完全在房间内
        if not GeometryUtils.is_polygon_fully_inside(item_polygon, self.room_boundary):
            return False

        # 检查净空区是否完全在房间内
        if clearance_zone is not None:
            if not GeometryUtils.is_polygon_fully_inside(clearance_zone, self.room_boundary):
                return False

        # 检查是否与门禁区碰撞
        if self._check_door_collision(item_polygon, clearance_zone, area_threshold):
            return False

        # 检查是否与已放置物品碰撞
        if self._check_item_collisions(item_polygon, clearance_zone, area_threshold):
            return False

        return True

    def _check_door_collision(self,
                              item_polygon: Polygon,
                              clearance_zone: Optional[Polygon],
                              area_threshold: float) -> bool:
        """
        检查是否与门禁区碰撞
        """
        # 如果门禁区无效或为空，则无碰撞
        if (self.door_forbidden_zone is None or
                not self.door_forbidden_zone.is_valid or
                self.door_forbidden_zone.is_empty):
            return False

        # 检查物品是否与门禁区相交
        if item_polygon.intersects(self.door_forbidden_zone):
            intersection = item_polygon.intersection(self.door_forbidden_zone)
            if intersection.area > area_threshold:
                return True

        # 检查净空区是否与门禁区相交
        if clearance_zone is not None:
            if clearance_zone.intersects(self.door_forbidden_zone):
                intersection = clearance_zone.intersection(self.door_forbidden_zone)
                if intersection.area > area_threshold:
                    return True

        return False

    def _check_item_collisions(self,
                               item_polygon: Polygon,
                               clearance_zone: Optional[Polygon],
                               area_threshold: float) -> bool:
        """
        检查是否与已放置物品碰撞
        检查的碰撞对:
        1. 新物品 vs 已放置物品
        2. 新物品 vs 已放置物品的净空区
        3. 新物品的净空区 vs 已放置物品
        """
        for placed_item in self.placed_items:
            # 检查新物品 vs 已放置物品
            if item_polygon.intersects(placed_item.polygon):
                intersection = item_polygon.intersection(placed_item.polygon)
                if intersection.area > area_threshold:
                    return True

            # 检查新物品 vs 已放置物品的净空区
            if placed_item.clearance_zone is not None:
                if item_polygon.intersects(placed_item.clearance_zone):
                    intersection = item_polygon.intersection(placed_item.clearance_zone)
                    if intersection.area > area_threshold:
                        return True

            # 检查新物品的净空区 vs 已放置物品
            if clearance_zone is not None:
                if clearance_zone.intersects(placed_item.polygon):
                    intersection = clearance_zone.intersection(placed_item.polygon)
                    if intersection.area > area_threshold:
                        return True

        return False

class ItemPlacer:
    def __init__(self, name: str, collision_detector: CollisionDetector):
        """
        初始化物品放置器
        参数:
            name: 物品名称
            collision_detector: 碰撞检测器
        """
        self.name = name
        self.collision_detector = collision_detector
    def place(self, item_def: ItemDefinition, wall_edges: List[WallEdge]) -> Optional[PlacementCandidate]:
        """
        放置物品（子类应重写此方法）
        参数:
            item_def: 物品定义
            wall_edges: 可用的墙边列表
        返回:
            最佳放置候选，若无有效位置则返回None
        """
        raise NotImplementedError("子类必须实现place方法")


class FridgePlacer(ItemPlacer):
    """冰箱放置器 - 专门处理冰箱的放置逻辑"""
    def __init__(self, name: str, collision_detector: CollisionDetector):
        super().__init__(name, collision_detector)

    def place(self, item_def: ItemDefinition, wall_edges: List[WallEdge]) -> Optional[PlacementCandidate]:
        """
        放置冰箱
        冰箱的特殊要求:
        1. 背面必须贴墙
        2. 必须有净空区（开门空间）
        3. 净空区必须完全在房间内
        放置策略:
        1. 遍历所有墙边（门边除外）
        2. 在每条边上均匀采样多个位置
        3. 计算每个位置的评分
        4. 选择最佳位置
        """
        print(f"\n【冰箱放置】{self.name}: {item_def.length}x{item_def.width}, 净空={item_def.clearance}")
        print(f"  需要总深度: {item_def.width + (item_def.clearance or 0)} mm")

        best_candidate = None
        tested_positions = 0
        valid_positions = 0

        # 净空深度（默认为610mm）
        clearance_depth = item_def.clearance or 610
        # 遍历所有墙边
        for edge in wall_edges:
            # 跳过门边（不能将冰箱放在门边）
            if edge.is_door_edge:
                continue
            # 检查墙边长度是否足够容纳冰箱长度
            min_required_length = item_def.length
            if edge.length < min_required_length:
                continue
            # 在墙上采样多个位置
            candidates = self._sample_positions_on_edge(
                edge, item_def, clearance_depth
            )
            # 评估每个候选位置
            for candidate in candidates:
                tested_positions += 1

                # 检查放置是否有效
                if self.collision_detector.check_placement_valid(
                        candidate.polygon, candidate.clearance_zone
                ):
                    valid_positions += 1

                    # 计算评分
                    candidate.score = self._calculate_score(
                        candidate.center, edge
                    )

                    # 更新最佳候选
                    if best_candidate is None or candidate.score > best_candidate.score:
                        candidate.edge_info = f"边长{edge.length:.0f}mm"
                        best_candidate = candidate

        print(f"  测试位置: {tested_positions}, 有效位置: {valid_positions}")

        return best_candidate

    def _sample_positions_on_edge(self,
                                  edge: WallEdge,
                                  item_def: ItemDefinition,
                                  clearance_depth: float) -> List[PlacementCandidate]:
        """
        在一条墙边上采样多个放置位置

        采样方法:
        1. 确定沿墙方向的长度（使用物品长度）
        2. 计算可用的放置范围（减去两端余量）
        3. 在可用范围内均匀采样
        """
        candidates = []
        # 确定采样点数（每50mm采样一个点）
        num_samples = max(15, int(edge.length / 50))
        # 两端预留的余量（物品长度的一半 + 50mm）
        margin = item_def.length / 2 + 50
        # 可用的放置范围长度
        available_length = edge.length - 2 * margin
        if available_length <= 0:
            return candidates

        for i in range(num_samples):
            # 计算在边上的参数位置t (0到1之间)
            t = (i + 0.5) / num_samples

            # 根据余量调整t，确保物品不会超出墙的两端
            actual_t = margin / edge.length + t * (available_length / edge.length)

            # 计算墙上的点坐标
            edge_x = edge.start[0] + actual_t * (edge.end[0] - edge.start[0])
            edge_y = edge.start[1] + actual_t * (edge.end[1] - edge.start[1])

            # 计算冰箱中心点（从墙向内偏移width/2）
            center_x = edge_x + edge.inward_normal[0] * (item_def.width / 2)
            center_y = edge_y + edge.inward_normal[1] * (item_def.width / 2)
            center = (center_x, center_y)
            rotation = edge.angle
            # 创建冰箱多边形
            polygon = GeometryUtils.create_rectangle_polygon(
                center, item_def.length, item_def.width, rotation
            )
            # 创建净空区
            clearance_zone = GeometryUtils.create_clearance_zone(
                center, item_def.length, item_def.width,
                edge.inward_normal, clearance_depth
            )
            candidates.append(PlacementCandidate(
                center=center,
                rotation=rotation,
                polygon=polygon,
                clearance_zone=clearance_zone
            ))

        return candidates

    def _calculate_score(self,
                         center: Tuple[float, float],
                         edge: WallEdge) -> float:
        """
        计算放置位置的评分
        评分因素:
        1. 远离门（加分）
        2. 靠近角落（加分）
        """
        # 这里简化为返回固定分数，实际应用中可能需要更复杂的评分逻辑
        return 100.0

class WallItemPlacer(ItemPlacer):
    """普通墙边物品放置器（货架、悬挂架、制冰机）"""
    def __init__(self, name: str, collision_detector: CollisionDetector):
        super().__init__(name, collision_detector)
        self.placed_items_ref: List[PlacedItem] = []  # 引用已放置物品用于紧凑布局

    def set_placed_items_ref(self, placed_items: List[PlacedItem]):
        """设置已放置物品的引用（用于紧凑布局评分）"""
        self.placed_items_ref = placed_items

    def place(self, item_def: ItemDefinition, wall_edges: List[WallEdge]) -> Optional[PlacementCandidate]:
        """
        放置普通墙边物品
        这些物品没有净空区要求，可以有两种朝向：
        1. 长度方向沿墙
        2. 宽度方向沿墙
        """
        print(f"\n【普通物品放置】{self.name}: {item_def.length}x{item_def.width}")

        best_candidate = None

        # 遍历所有墙边
        for edge in wall_edges:
            # 尝试两种可能的朝向
            for orientation in ['length_along_wall', 'width_along_wall']:
                candidates = self._sample_positions_on_edge(
                    edge, item_def, orientation
                )

                # 评估每个候选位置
                for candidate in candidates:
                    if self.collision_detector.check_placement_valid(
                            candidate.polygon, None
                    ):
                        candidate.score = self._calculate_score(
                            candidate.center, candidate.polygon, edge
                        )

                        if best_candidate is None or candidate.score > best_candidate.score:
                            best_candidate = candidate

        return best_candidate

    def _sample_positions_on_edge(self,
                                  edge: WallEdge,
                                  item_def: ItemDefinition,
                                  orientation: str) -> List[PlacementCandidate]:
        """
        在一条墙边上采样多个放置位置
        参数:
            edge: 墙边
            item_def: 物品定义
            orientation: 朝向 ('length_along_wall' 或 'width_along_wall')
        """
        candidates = []

        # 根据朝向确定沿墙方向和垂直墙方向的尺寸
        if orientation == 'length_along_wall':
            along_wall_size = item_def.length
            perpendicular_size = item_def.width
            rotation = edge.angle
        else:  # width_along_wall
            along_wall_size = item_def.width
            perpendicular_size = item_def.length
            rotation = edge.angle + 90

        # 检查墙边长度是否足够
        if edge.length < along_wall_size + 50:
            return candidates

        # 确定采样点数
        num_samples = max(12, int(edge.length / 50))

        # 两端预留的余量
        margin = along_wall_size / 2 + 50
        available_length = edge.length - 2 * margin

        if available_length <= 0:
            return candidates

        for i in range(num_samples):
            # 计算在边上的参数位置
            t = (i + 0.5) / num_samples
            actual_t = margin / edge.length + t * (available_length / edge.length)

            # 计算墙上的点
            edge_x = edge.start[0] + actual_t * (edge.end[0] - edge.start[0])
            edge_y = edge.start[1] + actual_t * (edge.end[1] - edge.start[1])

            # 计算物品中心点
            center_x = edge_x + edge.inward_normal[0] * (perpendicular_size / 2 + 5)
            center_y = edge_y + edge.inward_normal[1] * (perpendicular_size / 2 + 5)
            center = (center_x, center_y)

            # 创建物品多边形
            polygon = GeometryUtils.create_rectangle_polygon(
                center, item_def.length, item_def.width, rotation
            )

            candidates.append(PlacementCandidate(
                center=center,
                rotation=rotation,
                polygon=polygon,
                clearance_zone=None
            ))

        return candidates

    def _calculate_score(self,
                         center: Tuple[float, float],
                         polygon: Polygon,
                         edge: WallEdge) -> float:
        """
        计算放置位置的评分

        评分因素:
        1. 靠近已放置物品（紧凑布局加分）
        2. 远离门（加分）
        3. 非门边（加分）
        """
        score = 0.0

        # 因素1: 靠近已放置物品
        for placed_item in self.placed_items_ref:
            distance = polygon.distance(placed_item.polygon)
            # 如果距离在0mm到500mm之间，加分（距离越小加分越多）
            if 0 < distance < 500:
                score += 150 - distance / 5

        # 因素2: 远离门
        # 这里简化为固定分数，实际应用可能需要计算到门的距离
        score += 50

        # 因素3: 非门边加分
        if not edge.is_door_edge:
            score += 100

        return score


class PlacementManager:
    # 物品放置优先级
    PRIORITY_ORDER = {
        ItemType.FRIDGE: 0,
        ItemType.ICE_MAKER: 1,
        ItemType.SHELF: 2,
        ItemType.OVER_SHELF: 3
    }
    def __init__(self, room_data: dict):
        self.room_data = room_data
        self.item_definitions: List[ItemDefinition] = []
        self.placed_items: List[PlacedItem] = []
        self.results: Dict[str, Optional[dict]] = {}

        self._init_room()
        self._init_item_definitions()
        self.collision_detector = CollisionDetector(
            self.room_boundary, self.door_forbidden_zone
        )

    def _init_room(self):
        """初始化房间几何信息"""
        self.boundary_points = self.room_data['boundary']
        self.room_boundary = Polygon(self.boundary_points)

        if not self.room_boundary.is_valid:
            self.room_boundary = self.room_boundary.buffer(0)

        # 获取门信息
        self.door_start = tuple(self.room_data['door'][0])
        self.door_end = tuple(self.room_data['door'][1])

        # 处理开门方向
        is_inward = self.room_data['isOpenInward']
        if isinstance(is_inward, str):
            self.is_open_inward = is_inward.lower() == 'true'
        else:
            self.is_open_inward = bool(is_inward)

        # 计算门禁区
        door_calculator = DoorForbiddenZoneCalculator(
            self.door_start, self.door_end, self.is_open_inward, self.room_boundary
        )
        self.door_forbidden_zone = door_calculator.calculate_forbidden_zone()

        # 检测墙边
        door_line = LineString([self.door_start, self.door_end])
        edge_detector = WallEdgeDetector(self.room_boundary, self.door_start, self.door_end)
        self.wall_edges = edge_detector.detect_edges(door_line)

        self._print_room_info()

    def _init_item_definitions(self):
        """初始化物品定义"""
        algo_place = self.room_data['algoToPlace']

        for name, dimensions in algo_place.items():
            length, width = dimensions[0], dimensions[1]

            item_def = ItemDefinition(
                name=name,
                length=length,
                width=width
            )

            # 为冰箱设置净空区
            if 'fridge' in name.lower():
                item_def.clearance = (item_def.length / 2)

            self.item_definitions.append(item_def)

    def _print_room_info(self):
        """打印房间信息"""
        print("=" * 60)
        print("房间信息:")
        print(f"  边界有效: {self.room_boundary.is_valid}")
        print(f"  房间面积: {self.room_boundary.area / 1e6:.2f} m²")
        print(f"  门宽度: {LineString([self.door_start, self.door_end]).length:.0f} mm")
        print(f"  门类型: {'内开' if self.is_open_inward else '外开'}")
        print(f"  墙边数量: {len(self.wall_edges)}")
        print("=" * 60)

    def _sort_items_by_priority(self) -> List[ItemDefinition]:
        """
        按优先级排序物品
        优先级规则:
        1. 按类型优先级（冰箱最高，悬挂架最低）
        2. 同类型按面积降序（大的先放）
        """
        def get_priority(item_def: ItemDefinition) -> tuple:
            type_priority = self.PRIORITY_ORDER.get(item_def.type, 99)
            # 面积大的优先级高（所以用负值）
            area_priority = -item_def.area
            return (type_priority, area_priority)

        return sorted(self.item_definitions, key=get_priority)

    def _create_placer_for_item(self, item_def: ItemDefinition) -> ItemPlacer:
        """
        根据物品类型创建对应的放置器
        """
        if item_def.type == ItemType.FRIDGE:
            placer = FridgePlacer(item_def.name, self.collision_detector)
        else:
            placer = WallItemPlacer(item_def.name, self.collision_detector)
            # 为普通物品放置器设置已放置物品引用（用于紧凑布局）
            if isinstance(placer, WallItemPlacer):
                placer.set_placed_items_ref(self.placed_items)

        return placer

    def place_all_items(self) -> dict:
        """
        放置所有物品

        步骤:
        1. 按优先级排序物品
        2. 按顺序放置每个物品
        3. 记录放置结果
        """
        # 步骤1: 按优先级排序
        sorted_items = self._sort_items_by_priority()

        print("\n" + "=" * 60)
        print("放置顺序:")
        for i, item_def in enumerate(sorted_items):
            print(f"  {i + 1}. {item_def.name} ({item_def.type.value}) - "
                  f"{item_def.length}x{item_def.width}")
        print("=" * 60)

        # 步骤2: 按顺序放置每个物品
        for item_def in sorted_items:
            # 创建对应的放置器
            placer = self._create_placer_for_item(item_def)

            # 尝试放置物品
            candidate = placer.place(item_def, self.wall_edges)

            if candidate is not None:
                # 放置成功，创建已放置物品记录
                placed_item = PlacedItem(
                    name=item_def.name,
                    polygon=candidate.polygon,
                    center=candidate.center,
                    rotation=candidate.rotation,
                    length=item_def.length,
                    width=item_def.width,
                    item_type=item_def.type,
                    clearance_zone=candidate.clearance_zone
                )

                # 添加到列表和碰撞检测器
                self.placed_items.append(placed_item)
                self.collision_detector.add_placed_item(placed_item)

                # 记录结果
                self.results[item_def.name] = {
                    'center': list(candidate.center),
                    'rotation': candidate.rotation % 360
                }

                print(f"  ✓ {item_def.name} 放置成功: "
                      f"center=({candidate.center[0]:.1f}, {candidate.center[1]:.1f}), "
                      f"rotation={candidate.rotation % 360:.1f}°")
            else:
                # 放置失败
                self.results[item_def.name] = None
                print(f"  ✗ {item_def.name} 放置失败")

        return self.results

    def get_summary(self) -> dict:
        """获取放置结果摘要"""
        placed_count = sum(1 for v in self.results.values() if v is not None)
        failed_items = [k for k, v in self.results.items() if v is None]

        return {
            "total": len(self.item_definitions),
            "placed": placed_count,
            "failed": len(failed_items),
            "failed_items": failed_items,
            "feasible": len(failed_items) == 0
        }

class RoomVisualizer:
    """房间布局可视化器"""
    # 物品类型颜色映射
    COLORS = {
        ItemType.FRIDGE: '#2ecc71',  # 绿色
        ItemType.ICE_MAKER: '#3498db',  # 蓝色
        ItemType.SHELF: '#e67e22',  # 橙色
        ItemType.OVER_SHELF: '#9b59b6',  # 紫色
    }

    def __init__(self, placement_manager: PlacementManager):
        self.manager = placement_manager

    def visualize(self, save_path: str = "room_layout.png"):
        """可视化房间布局"""
        # 创建图形
        fig, ax = plt.subplots(1, 1, figsize=(14, 12))
        # 绘制房间边界
        self._draw_room_boundary(ax)
        # 绘制门
        self._draw_door(ax)
        # 绘制门禁区
        self._draw_door_forbidden_zone(ax)
        # 绘制已放置物品
        self._draw_placed_items(ax)
        # 绘制图例
        self._draw_legend(ax)
        # 绘制状态信息
        self._draw_status(ax)

        # 设置图形属性
        ax.set_aspect('equal')
        ax.set_xlabel('X (mm)', fontsize=12)
        ax.set_ylabel('Y (mm)', fontsize=12)
        ax.set_title('Room Layout - Clearance Zone Must Be Inside Room',
                     fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3)

        # 保存和显示
        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        plt.show()

        print(f"\n可视化已保存至: {save_path}")

    def _draw_room_boundary(self, ax):
        """绘制房间边界"""
        boundary_coords = list(self.manager.room_boundary.exterior.coords)
        boundary_x = [p[0] for p in boundary_coords]
        boundary_y = [p[1] for p in boundary_coords]

        ax.fill(boundary_x, boundary_y, alpha=0.1, color='gray',
                edgecolor='black', linewidth=2)

    def _draw_door(self, ax):
        """绘制门"""
        ax.plot([self.manager.door_start[0], self.manager.door_end[0]],
                [self.manager.door_start[1], self.manager.door_end[1]],
                'brown', linewidth=6, solid_capstyle='butt', label='Door')

    def _draw_door_forbidden_zone(self, ax):
        """绘制门禁区"""
        forbidden_zone = self.manager.door_forbidden_zone
        if forbidden_zone is not None and forbidden_zone.is_valid and not forbidden_zone.is_empty:
            coords = list(forbidden_zone.exterior.coords)
            x = [p[0] for p in coords]
            y = [p[1] for p in coords]

            ax.fill(x, y, alpha=0.2, color='red',
                    edgecolor='red', linestyle='--', linewidth=1.5)

    def _draw_placed_items(self, ax):
        """绘制已放置物品"""
        for item in self.manager.placed_items:
            color = self.COLORS.get(item.item_type, '#95a5a6')

            # 绘制物品
            coords = list(item.polygon.exterior.coords)
            x = [p[0] for p in coords]
            y = [p[1] for p in coords]

            ax.fill(x, y, alpha=0.7, color=color, edgecolor='black', linewidth=1.5)

            # 绘制净空区
            if item.clearance_zone is not None:
                self._draw_clearance_zone(ax, item)

            # 添加物品名称标签
            centroid = item.polygon.centroid
            ax.annotate(item.name, (centroid.x, centroid.y),
                        ha='center', va='center', fontsize=8, fontweight='bold',
                        color='white',
                        bbox=dict(boxstyle='round,pad=0.2', facecolor=color, alpha=0.8))

    def _draw_clearance_zone(self, ax, item: PlacedItem):
        """绘制净空区"""
        coords = list(item.clearance_zone.exterior.coords)
        x = [p[0] for p in coords]
        y = [p[1] for p in coords]

        # 检查净空区是否在房间内
        is_inside = GeometryUtils.is_polygon_fully_inside(
            item.clearance_zone, self.manager.room_boundary, 0.99
        )

        if is_inside:
            # 净空区有效
            ax.fill(x, y, alpha=0.25, color='lightgreen',
                    edgecolor='green', linestyle='--', linewidth=2)
        else:
            # 净空区超出房间
            ax.fill(x, y, alpha=0.5, color='red',
                    edgecolor='darkred', linestyle='-', linewidth=2)

    def _draw_legend(self, ax):
        """绘制图例"""
        legend_elements = [
            patches.Patch(facecolor=self.COLORS[ItemType.FRIDGE], alpha=0.7, label='Fridge'),
            patches.Patch(facecolor=self.COLORS[ItemType.ICE_MAKER], alpha=0.7, label='IceMaker'),
            patches.Patch(facecolor=self.COLORS[ItemType.SHELF], alpha=0.7, label='Shelf'),
            patches.Patch(facecolor=self.COLORS[ItemType.OVER_SHELF], alpha=0.7, label='OverShelf'),
            patches.Patch(facecolor='red', alpha=0.2, label='Door Zone'),
            patches.Patch(facecolor='lightgreen', alpha=0.25, edgecolor='green',
                          label='Clearance (valid)'),
        ]

        ax.legend(handles=legend_elements, loc='upper left', fontsize=10)

    def _draw_status(self, ax):
        """绘制状态信息"""
        summary = self.manager.get_summary()

        if summary['feasible']:
            status = "✓ FEASIBLE"
            status_color = 'green'
        else:
            status = f"✗ FAILED ({summary['failed']} items)"
            status_color = 'red'

        ax.text(0.98, 0.98, status, transform=ax.transAxes,
                fontsize=12, fontweight='bold',
                verticalalignment='top', horizontalalignment='right',
                bbox=dict(boxstyle='round', facecolor=status_color, alpha=0.3))


# ============================================================================
# 主函数
# ============================================================================

def main():
    """主函数"""
    result_dir = "result"
    if not os.path.exists(result_dir):
        os.makedirs(result_dir)
    # 输入数据
    for i in range(1, 5):
        filename = f"./居灵-TakeHome工程题/example{i}.json"
        with open(filename, "r", encoding="utf-8") as f:
            input_data = json.load(f)
        # 创建放置管理器并放置物品
        manager = PlacementManager(input_data)
        results = manager.place_all_items()

        # 打印结果
        print("\n" + "=" * 60)
        print("放置结果:")
        print("=" * 60)
        # 添加feasible参数
        summary = manager.get_summary()
        results["feasible"] = summary["feasible"]
        print(json.dumps(results, indent=2, ensure_ascii=False))
        # 保存
        with open(f"../result/example{i}_result.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        # 打印摘要
        print("\n" + "=" * 60)
        print("摘要:")
        print("=" * 60)
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        # 可视化
        visualizer = RoomVisualizer(manager)
        visualizer.visualize(f"./result/room_layout{i}.png")


if __name__ == "__main__":
    main()