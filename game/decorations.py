"""装饰均为永久外观；值为需要捐赠的材料数量。"""
DECORATIONS = {"水草角": 5, "贝壳小屋": 8, "月亮灯": 12}


def completed(decorations):
    return [name for name, cost in DECORATIONS.items()
            if sum(decorations.get(name, {}).values()) >= cost]
