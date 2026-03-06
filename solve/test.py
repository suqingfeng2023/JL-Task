import math
import json
from shapely.geometry import Point as ShapelyPoint

class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point(x={self.x}, y={self.y})"

    def to_shapely(self):
        return ShapelyPoint(self.x, self.y)

class Item:
    def __init__(self, name, length, width, item_type):
        self.name = name
        self.length = length
        self.width = width
        self.item_type = item_type
        self.rotation = 0 # 物品的旋转角度
        self.priority = self._calculate_priority() # 物品的放置优先级
        self.position = None  # 物品的中心点坐标
        self.placed = False  # 物品是否放置
        self.polygon = None  # 物品的多边形表示

    def _calculate_priority(self):
        base_priority = {
            'fridge': 100,
            'iceMaker': 80,
            'shelf': 60,
            'overShelf': 40
        }

        # 面积越大越优先
        area = self.length * self.width
        size = area / 10
        return base_priority.get(self.item_type, 50) + size

    # 物品可以旋转放置
    def get_size_cur_orientation(self):
        if self.rotation == 0:
            return self.length, self.width
        else:
            return self.width, self.length

class Boundary:
    def __init__(self, boundary, door, is_open_inward):
        self.boundary = []
        for point in boundary:
            self.boundary.append(Point(point[0], point[1]))
        self.door = []
        for point in door:
            self.door.append(Point(point[0], point[1]))
        self.is_open_inward = is_open_inward


    # 判断是否在轮廓内
    # 这里判断使用点是否在轮廓内进行判断(射线法)
    def JudgeInside(self, point):
        x = point.x
        y = point.y
        count = 0
        n = len(self.boundary)
        
        for i in range(n):
            p1 = self.boundary[i]
            p2 = self.boundary[(i + 1) % n]
            # 跳过水平边
            if p1.y == p2.y:
                continue
            if y < min(p1.y, p2.y) or y >= max(p1.y, p2.y):
                continue
            # 计算交点的x坐标
            # 两点式 (x - x1) = (x2 - x1) * (y - y1) / (y2 - y1)
            x_intersect = (p2.x - p1.x) * (y - p1.y) / (p2.y - p1.y) + p1.x
            
            # 如果交点在点的右侧，计数
            if x_intersect > x:
                count += 1
        
        # 返回：奇数在内部，偶数在外部
        return count % 2 == 1


class GenmetryUtils:

    @staticmethod
    def get_rotated_rect_corners(center, width, height, rotation):
        """获取旋转矩形的四个角坐标"""
        angle = math.radians(rotation)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        half_width = width / 2
        half_height = height / 2

        """四个角的相对与中心点坐标"""
        corners = [
            (half_width, half_height),
            (-half_width, half_height),
            (-half_width, -half_height),
            (half_width, -half_height)
        ]

        for (dx, dy) in corners:
            x = center.x + dx * cos_a - dy * sin_a
            y = center.y + dx * sin_a + dy * cos_a
            yield Point(x, y)



def main():
    with open("../居灵-TakeHome工程题/example1.json", 'r', encoding='utf-8') as file:
        data = json.load(file)

    boundary = data["boundary"]
    door = data["door"]
    is_open_inward = data["isOpenInward"]
    boundary_obj = Boundary(boundary, door, is_open_inward)

    print("=== 门的两个坐标点 ===")
    print(f"门的第一个点：{boundary_obj.door[0]}")
    print(f"门的第二个点：{boundary_obj.door[1]}")
    print(f"开门方向（是否向内）：{boundary_obj.is_open_inward}\n")

    items = {}
    for name, (length, width) in data['algoToPlace'].items():
        item_type = None
        if 'fridge' in name:
            item_type = 'fridge'
        elif 'shelf' in name:
            item_type = 'shelf'
        elif 'overShelf' in name:
            item_type = 'overShelf'
        elif 'iceMaker' in name:
            item_type = 'iceMaker'
        
        items[name] = Item(name, length, width, item_type)

    print("=== 设备信息库 ===")
    for item_name, item_obj in items.items():
        print(f"设备名：{item_name} | 长度：{item_obj.length} | 宽度：{item_obj.width} | 类型：{item_obj.item_type}")

if __name__ == '__main__':
    main()
