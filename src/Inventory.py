import json
from Item_node import ItemNode, ItemType
from pathlib import Path
from enum import Enum

class Inventory:
    def __init__(self):
        self.items = {}

    def add_item(self, item: ItemNode):
            item.id = item.id.lower()
            item.id.strip()

            if self.get_item(item.id):
                print(f"Item with ID {item.id} already exists in inventory.\n")
                print(f"Updating item {item.id}")
                self.items[item.id].count += item.count
            
            if item.type is None:
                while True:
                    user_input = input(f"Please enter the type of the item: {[item_type.value for item_type in ItemType]} ").strip().lower()

                    try:
                        item.type = ItemType(user_input)
                        break
                    except ValueError:
                        print(f"Not a valid type. Please enter one of: {[t.value for t in ItemType]}")
                        


            print(f"Adding item with ID {item.id} to inventory.")
            item.count += 1
            self.items[item.id] = item
        
    def remove_item(self, item_id: str):
            item_id.lower()
            if item_id in self.items:
                print(f"Removing a {item_id} from inventory.")
                self.items[item_id].count -= 1
            
                if self.items[item_id].count <= 0:
                    print(f"Item with ID {item_id} is out of stock and will be removed from inventory.")
                    del self.items[item_id]
    
    def get_item(self, item_id: str) -> ItemNode:
          item_id.lower()
          if self.items.get(item_id) is None:
              print(f"Item with ID {item_id} not found in inventory.")
              return False
        
          return self.items.get(item_id, None)
    
    def use_item(self, item_id: str):
        item = self.get_item(item_id)
        
        if item is not None:
            
            if item.count > 0:
                item.count -= 1
                item.in_use_count += 1
                print(f"Item with ID {item_id} is now in use. Remaining count: {item.count}")
            
            else:
                print(f"Item with ID {item_id} is out of stock.")
    
    def show_inventory(self):
        print("Current Inventory: \n")
        
        for item in self.items.values():
            print(f"ID: {item.id}, Count: {item.count}, In use: {item.in_use_count}, Requirements: {item.req}")
    
    def save_inventory(self, filename: str):
        path = Path(filename)

        print("Do you want to save the inventory?")
        self.show_inventory()

        answer = input("Enter 'y' to save or 'n' to cancel: ")

        if answer.lower() == "y" or answer.lower() == "yes":
            path.parent.mkdir(parents=True, exist_ok=True)

            data = make_jsonable(self.items)

            with open(path, "w") as f:
                json.dump(data, f, indent=4)

            print(f"Inventory saved to {path}.")
        else:
            print("Save operation cancelled.")
    
    

def make_jsonable(value):
        if isinstance(value, Enum):
            return value.value
        
        if isinstance(value, Path):
            return str(value)
        
        if isinstance(value, list):
            return [make_jsonable(item) for item in value]

        if isinstance(value, dict):
            return {
                key: make_jsonable(val)
                for key, val in value.items()
            }
        
        if hasattr(value, "__dict__"):
            return {
                key: make_jsonable(val)
                for key, val in value.__dict__.items()
            }
        return value