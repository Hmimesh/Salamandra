from pathlib import Path

from Inventory import Inventory
from Item_node import ItemNode, ItemType, Requirement


INVENTORY_PATH = Path(__file__).resolve().parents[1] / "docs" / "inventory.json"


def user_interface_inv():
    inventory = load_or_create_inventory()
    done = False

    while not done:
        print("\n=== INVENTORY MANAGEMENT SYSTEM ===\n")
        print("1. Add an item to the inventory")
        print("2. Remove an item from the inventory")
        print("3. Show inventory")
        print("4. Save inventory")
        print("5. Use an item")
        print("6. Return an item")
        print("7. Exit")

        user_input = input("Please enter 1 - 7: ").strip()

        match user_input:
            case "1":
                add_item_flow(inventory)
            case "2":
                remove_item_flow(inventory)
            case "3":
                show_inventory(inventory)
            case "4":
                inventory.save_inventory(INVENTORY_PATH)
                print(f"Inventory saved to {INVENTORY_PATH}.")
            case "5":
                use_item_flow(inventory)
            case "6":
                return_item_flow(inventory)
            case "7":
                print("Exiting...")
                done = True
            case _:
                print("Invalid option, please enter 1 - 7.")


def load_or_create_inventory() -> Inventory:
    if INVENTORY_PATH.exists():
        return Inventory.load_inventory(INVENTORY_PATH)
    return Inventory()


def add_item_flow(inventory: Inventory):
    item_id = input("The name of the item? ").strip()
    item_type = ask_item_type()
    requirements = ask_requirements()

    try:
        item = ItemNode(id=item_id, type=item_type, req=requirements)
        stored_item = inventory.add_item(item)
        print(f"Added {stored_item.id}. Available count: {stored_item.count}")
    except ValueError as error:
        print(error)


def remove_item_flow(inventory: Inventory):
    item_id = input("What item do you wish to remove? ").strip()
    amount = ask_amount("How many do you wish to remove? ")

    try:
        if inventory.remove_item(item_id, amount):
            print(f"Removed {amount} from {item_id}.")
        else:
            print(f"Item '{item_id}' was not found.")
    except ValueError as error:
        print(error)


def use_item_flow(inventory: Inventory):
    item_id = input("What item do you wish to use? ").strip()
    amount = ask_amount("How many do you wish to use? ")
    can_use, missing = inventory.can_use_requirements(item_id, amount)

    if not can_use:
        print("Cannot use item yet:")
        for missing_item in missing:
            print(f"- {missing_item}")
        return

    try:
        if inventory.use_item(item_id, amount):
            print(f"{amount} of {item_id} marked as in use.")
        else:
            print(f"Item '{item_id}' was not found.")
    except ValueError as error:
        print(error)


def return_item_flow(inventory: Inventory):
    item_id = input("What item do you wish to return? ").strip()
    amount = ask_amount("How many do you wish to return? ")

    try:
        if inventory.return_item(item_id, amount):
            print(f"{amount} of {item_id} returned to stock.")
        else:
            print(f"Item '{item_id}' was not found.")
    except ValueError as error:
        print(error)


def show_inventory(inventory: Inventory):
    print("Current Inventory:\n")

    if not inventory.items:
        print("Inventory is empty.")
        return

    for item in inventory.list_items():
        item_type = item.type.value if item.type else "unknown"
        requirements = ", ".join(str(requirement) for requirement in item.req) or "none"
        print(
            f"ID: {item.id}, Type: {item_type}, Count: {item.count}, "
            f"In use: {item.in_use_count}, Requirements: {requirements}"
        )


def ask_item_type() -> ItemType | None:
    valid_types = [item_type.value for item_type in ItemType]

    while True:
        user_input = input(f"Please enter the item type {valid_types}, or leave empty: ").strip()
        if not user_input:
            return None

        try:
            return ItemType(user_input.lower())
        except ValueError:
            print(f"Not a valid type. Please enter one of: {valid_types}")


def ask_requirements() -> list[Requirement]:
    requirements = []
    answer = input("Does the item require anything? enter 'y' or 'n': ").strip().lower()

    while answer in ("y", "yes"):
        req_id = input("What is the required item name? ").strip()
        req_amount = ask_amount("What is the amount it needs from this item? ")

        try:
            requirements.append(Requirement(req_id, req_amount))
        except ValueError as error:
            print(error)

        answer = input("Does it require any additional items? enter 'y' or 'n': ").strip().lower()

    return requirements


def ask_amount(prompt: str) -> int:
    while True:
        amount = input(prompt).strip()
        try:
            parsed_amount = int(amount)
            if parsed_amount <= 0:
                raise ValueError
            return parsed_amount
        except ValueError:
            print("Please enter a whole number greater than zero.")
