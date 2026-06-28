from enum import Enum
from __future__ import annotations

'''TODO: This is the nodes and classes here we will put the methods and usages of each any one of them
first the type enum to catalog them easiely'''


class ItemType(Enum):
    MIXER = "mixer"
    PA = "pa"
    MIC = "microphone"
    LIGHTING = "lighting"
    DI = "di"
    BACKLINE = "backline"
    STAND = "stand"
    CABLE = "cable"
    CASE = "case"



class ItemNode():
    def __init__(self, id : str, count : int, quality_score : int, type : ItemType, in_use_count : int, requierments : list[Requirement]):
        self.id = id
        self.count = count
        self.quality_score = quality_score
        self.type = type
        self.in_use_count = in_use_count
        self.req = requierments



class Requirement:
    def __init__(self, item: ItemNode, amount: int):
        self.item = item
        self.amount = amount