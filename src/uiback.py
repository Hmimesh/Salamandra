from Inventory import Inventory
from Item_node import ItemNode, Requirement
from pathlib import Path

def user_interface_inv():
    inventory = Inventory()
    done = False
    
    while not done:
        print("\n ===INVENTORY MANAGEMENT SYSTEM=== \n")
        print("1. add an item to the inventory\n")
        print("2. remove an item from the inventory\n")
        print("3. show inventory\n")
        print("4. save inventory\n")
        print("5. exit\n")

        user_input = input("Please enter 1 - 5: ")

        match user_input:
            case "1":
                item = ItemNode()
                

                item_id = input("The name of the item? ")
                item.id = item_id

                answare = input("Does the item require anything? enter 'y' or 'n': ")
                
                while answare.lower() == 'y' or answare.lower == 'yes':
                    req = Requirement()
                    req_item = ItemNode()

                    if answare.lower() == "y" or answare.lower() == "yes":
                        req_id = input("what is the required item name? ")
                        req_amount_str = input("what is the amount it needs from this item? ")

                        try:
                            req_amount = int(req_amount_str)
                        except ValueError:
                            print(f"'{req_amount_str}' is not a valid number")
                            print("removing the requirement")
                            req_amount = 0

                        req_item.id = req_id
                        req.item = req_item
                        req.amount = req_amount

                        if req.amount > 0:
                            item.req.append(req)

                        answare = input("Does it require any additional items? ")
                    else:
                        break

                inventory.add_item(item)

            case "2":
                while True:
                    answare = input("What item do you wish to remove? ")

                    inventory.remove_item(answare)
                    
                    more = input("Do you wish to remove more items? enter 'y' or 'n': ")

                    if more.lower() == "n" or more.lower() == "no":
                        break
            
            case "3":
                inventory.show_inventory()

            case "4":
                path = Path(__file__).resolve().parents[1] / "docs" / "inventory.json"
                inventory.save_inventory(path)
            
            case "5":
                print("exiting...")
                done = True

            case _:
                print("Invalid option, please enter 1 - 5.")