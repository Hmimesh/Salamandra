from __future__ import annotations

from enum import Enum

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
    def __init__(self):
        self.id = ""
        self.count = 0
        self.quality_score = 0
        self.type = None
        self.in_use_count = 0
        self.req = []
    
    def add_req(self, req : Requirement):
        self.req.append(req)
    



class Requirement:
    def __init__(self, item=None, amount=None):
        self.item = item
        self.amount = amount
    
    def __repr__(self):
        return f"{self.amount}x {self.item.id}"