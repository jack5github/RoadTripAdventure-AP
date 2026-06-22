from .pine import Pine
from ..ram_data import Addresses
from .MIPS import *

NOP_BYTES = bytes([0,0,0,0])
JAL_AP_LOCATION_FUNC_READ = bytes([0x80, 0x68, 0x0B, 0x0C]) # jal 0x2DA200 (0C0B6883)
JAL_AP_LOCATION_FUNC_WRITE = bytes([0x83, 0x68, 0x0B, 0x0C]) # jal 0x2DA20C (0C0B6880)

J_AP_LOCATION_FUNC_READ = bytes([0x80, 0x68, 0x0B, 0x08]) # j 0x2DA200 (080B6883)
J_AP_LOCATION_FUNC_WRITE = bytes([0x83, 0x68, 0x0B, 0x08]) # j 0x2DA20C (080B6880)

# ---------------------------------------------------
_patch_index = 0
def patch_rta_no_slot_data(pine : Pine, verification_run : bool = False):
    patches = [
        # Disable the email system
        # This is where we'll store our patches - but in particular, we need to clear
        #   something of this size to store all of the shop strings.
        disable_email_system,

        # AP save setup
        hook_currency_input_to_init_ap_item_index,

        # Handling AP location checks
        write_ap_location_func,
        hook_shop_purchases,
        hook_npc_rewards,
        patch_npc_dialogue_triggers,
        hook_overworld_item_funcs,
        hook_license_upgrades,

        # For shop strings (this function does not require slot data)
        change_shop_item_quantity_display_to_ap,

        # Other patches
        patch_npc_equips,
        disable_vanilla_my_city_part_shop_handling,
        hook_game_continue_to_reset_my_city_part_shop,
        patch_tin_raceway_requirements,
        disable_func_that_overwrites_ap_save_data,
        # patch_bars_for_ap_hints(pine) # TODO
        # fix_curling_bug(pine) # TODO
    ]

    if verification_run:
        # Run only one patch. Used for patch verification, so we're not verifying every function on every client loop.
        global _patch_index
        patches[_patch_index](pine)
        _patch_index += 1
        if _patch_index >= len(patches):
            _patch_index = 0
    else:
        # Run all patches. Standard use case (i.e. patching the game).
        for patch in patches:
            patch(pine)


def patch_rta_post_connect(pine : Pine, shop_strings : list, area_unlock_mode : int):
    # Handle shop strings
    hook_shops_to_display_ap_item_strings(pine, shop_strings)

    # Handle enforcing area access
    enforce_area_access(pine, area_unlock_mode)

# ---------------------------------------------------

def disable_email_system(pine : Pine):
    """
    Removes all interactions with the email system from the game. 
    
    This allows us to use the space in memory where email text was stored for our new AP item descriptions in part shops.
    Additionally, this space is so large that we can actually store all of our assembly patches here (which will help
    prevent any other RTA mods from accidentally overwriting our patches for AP).
    """
    # Original ASM line at this address compares whether distance traveled is greater than amount needed to trigger email. 
    # This sets the comparison to always be False instead.
    pine.write_bytes(0x210EE8, mips([
        addiu(a1, zero, 0x0)
    ]))

    # Prevent blinking indicator on the minimap
    # pine.write_bytes(0x249264, NOP_BYTES) # Broken, hides the entire border around the minimap.
    pine.write_bytes(0x2492BC, NOP_BYTES) # NOPing this line instead seems to work?

    # Remove option for email from garage menu.
    pine.write_bytes(0x22CE88, NOP_BYTES)

    # Automatically increment garage menu index by one so that all menu options run the correct tasks.
    # Otherwise, the first option would still open the email inbox, even with its button removed.
    # Also, skip code that handles the email option.
    pine.write_bytes(0x22D118, mips([
        addiu(v1, v1, 0x1),
        beq(zero, zero, 9), # beq zero,zero,0x0022D140
        nop()
    ]))


def hook_currency_input_to_init_ap_item_index(pine : Pine):
    """ 
    Hook RTA to initialize the AP index variable and get the AP save ID on new game. 
    This hook runs after currency input is complete, but before the President Forest cutscene begins.
    """
    # Overwrite jal to president Forest cutscene so we can add a hook that runs first
    addr = 0x2da180
    pine.write_bytes(0x26d650, mips([
        jal(addr)
    ]))
    
    # Hook
    pine.write_bytes(addr, mips([
        # Set AP index to 0x1
        addiu(t0, zero, 0x1),
        lui(t1, 0x177),
        ori(t1, t1, 0xFDAC),
        sb(t0, 0, t1),

        # Set boolean (at 0x2DA0F0) to indicate to the server that we are ready to receive the AP save ID to 1
        lui(t1, 0x2D),
        ori(t1, t1, 0xA0F0),
        sb(t0, 0, t1),

        # Set boolean (at 0x2DA0F1) to indicate to the server that we're ready to receive the current My City part shop inventory
        sb(t0, 1, t1),

        # Now jump to the president Forest cutscene function (don't jal, our jal into this hook already set the ra register)
        j(0x211708),
        nop()
    ]))


def hook_game_continue_to_reset_my_city_part_shop(pine : Pine):
    """
    Hook the function that continues from a saved game to set the 'ready_for_my_city_part_shop_inventory' byte.
    When the client sees this, it will set the parts sold at the My City part shop to the parts the player has
    received so far from the multiworld.
    """
    # Change the jr ra to a j to our hook
    addr = 0x2DAE00
    pine.write_bytes(0x26d42c, mips([
        j(addr)
    ]))

    pine.write_bytes(addr, mips([
        lui(t0, 0x2D),
        ori(t0, t0, 0xA0F1),
        addiu(t1, t1, 0x1),
        sb(t1, 0, t0),
        jr(ra),
        nop()
    ]))
    

def write_ap_location_func(pine : Pine):
    """
    Creates a function that, given an item's part type (in register a0) and item ID (in register a1),
    can read/write whether the AP location associated with obtaining that item is complete.
    """
    # Additional info:
    # There are two AP location tables: One for items received from NPCs, and one for items purchased from a part
    # shop. Both are needed since a few items can be obtained either way, and those are separate AP locations.
    # Example: RS Magnum (Which-Way Maze location != Shop Purchase RS Magnum location)
    #
    # This function will check to see whether we are currently in a part shop. If we are, it reads/writes
    # the shop location table. If not, it reads/writes the NPC location table.
    #
    # These 'AP location completion' tables are saved in (what appears to be) an unused part of the save file.
    # (More specifically, all data from the save file is stored by RTA in one contiguous block of memory.
    # When the game saves, it just writes that big block of memory to the memory card. So we don't write to the
    # memory card here, we're writing to the part of memory that *will get written* to the memory card when
    # the player saves.)
    
    # -----------------------------------------
    # What is 'table_length_table'?
    # The AP location completion tables are bitfields. Each bit corresponds to whether the location for that
    # item has been completed.
    #
    # To determine which bit we need to read/write, we first need to convert the item type + ID to the correct
    # bit. This involves adding the total number of parts of all prior part types, plus the item ID.
    #
    # Example: Engines are part type 2. The RS Magnum is engine index 8. Given this, its bit in the bitfield is #172 because:
    # 0x97 (all 151 bodies) + 0x0D (all 13 tires) + 0x8 (RS Magnum index in engine table) = 0xAC hex, or 172 decimal
    #
    table_length_table = bytes([0x97, 0x0D, 0x0C, 0x05, 0x06, 0x04, 0x04, 0x0F, 0x03, 0x02, 0x03, 0x09, 0x02, 0x0F, 0x0B, 0x30])
    pine.write_bytes(0x2da100, table_length_table) # Just prior to all ASM patches

    addr = 0x2da200
    pine.write_bytes(addr, mips([
        # To read a bit, start here. Set t7 to 1 (checked later).
        addiu(t7, zero, 1),
        beq(zero, zero, 3),
        nop(),

        # To write a bit, start here. Set t7 to 2 (checked later).
        addiu(t7, zero, 2),

        # ---------------------------------------

        # Main function body
        # First, if a2 is not 0, exit the function.
        # a2 appears to always be 0 when the player is receiving a part.
        bne(a2,zero,0x2D),
        nop(),

        # Copy the arguments to temporary registers, instead of mutating directly (they could 
        #   be needed by the vanilla function later)
        addiu(t0, a0, 0),
        addiu(t2, a1, 0),

        # Load the address to the table length table
        lui(t1, 0x002D),
        ori(t1, t1, 0xA100),

        # Loop
        # Add the part totals for each part type until we've reached this part's type.
        beq(t0, zero, 6),
        lbu(t3, 0, t1),
        addu(t2, t2, t3),
        addiu(t1, t1, 1),
        addiu(t0, t0, -1),
        beq(zero, zero, -5),

        # Determine whether we are currently in a shop by testing for either shop task
        #   address in ra.
        # One will be in ra during a buy, the other will be in ra while browsing.
        # If we are in the shop, set the address to check to the the AP shop purchases 
        #   bitfield. Otherwise, set it to the AP NPC items received bitfield.
        lui(t3, 0x0026),
        ori(t3, t3, 0x97E0),
        beq(ra, t3, 10),
        nop(),
        lui(t3, 0x0024),
        ori(t3, t3, 0x7244),
        beq(ra, t3, 6),
        nop(),

        # Set table to NPC reward table
        lui(t1, 0x0178),
        addiu(t1, t1, 0x2A00),
        beq(zero, zero, 4),
        nop(),

        # Set table to shop purchases table
        lui(t1, 0x0178),
        addiu(t1, t1, 0x29D0),

        # Loop
        # Continue subtracting 8 from the total item count until it would go negative.
        # This is to determine which *byte* contains our bit (since 8 bits are in a byte).
        addiu(t3, t2, -0x8),
        bltz(t3, 6),
        nop(),
        addiu(t2, t2, -0x8),
        addiu(t1, t1, 1),
        beq(zero,zero,-5),
        nop(),

        # Load the byte, and prepare t2 to contain a 1 in the bit we want to read/write, and
        #   0 in all other bits (i.e. create a bit mask)
        lbu(t0, 0, t1),
        addiu(t3, zero, 1),
        sllv(t2, t3, t2),

        # If t7 (read/write enum) is 1, branch to TABLE READ
        slti(t7, t7, 2),
        bne(t7, zero, 5),
        nop(),

        # TABLE WRITE
        or_(t0, t0, t2),
        sb(t0, 0, t1),
        jr(ra),

        # TABLE READ
        and_(t0, t0, t2),
        slti(v0, t0, 1),
        xori(v0, v0, 1),
        jr(ra),
        nop()
    ]))


def hook_shop_purchases(pine : Pine):
    """
    Convert shop purchases to AP locations, instead of giving the player the item purchased - *except* in My City.
    My City's part shop does not contain any locations, and is used exclusively for repurchasing parts you've
    already obtained.
    """
    # At 0x2697d8, change the ASM instruction (which is currently a jump-and-link to the function that handles
    #   updating your inventory) to a jal to our new hook.
    pine.write_bytes(0x2697D8, mips([
        jal(0x2DA380)
    ]))
    
    # In our hook, test if the current region index is 9 (My City).
    #   If it's not, run our AP location check function (defined above).
    #   If it is, jump (not jal) to the normal shop function that updates your inventory.
    addr = 0x2DA380
    pine.write_bytes(addr, mips([
        lui(t0, 0x0033),
        addiu(t0, t0, 0x5923),
        lbu(t0, 0, t0), # was lb
        addiu(t1, zero, 0x9),
        bne(t0, t1, 4),
        nop(),
        j(0x23D2C0),
        nop(),
        j(0x2DA20C),
        nop()
    ]))


def hook_npc_rewards(pine : Pine):
    """
    Convert NPC rewards to AP locations, instead of the vanilla behavior of giving the player an item. 
    Also sets the name of the received item to 'AP Item', instead of the vanilla name.
    """
    # Overwrite the call to the normal 'set inventory' function with our AP location function.
    pine.write_bytes(0x23A0B4, JAL_AP_LOCATION_FUNC_WRITE)

    # Set all NPC reward item names to 'AP Item'
    # 1. Hook the JAL that would normally get the pointer to the reward's name.
    pine.write_bytes(0x239FE4, mips([
        jal(0x2DA400)
    ]))

    # 2. In our hook, instead return the address to our new "AP Item" string.
    addr = 0x2DA400
    pine.write_bytes(addr, mips([
        lui(v0, 0x002D),
        ori(v0, v0, 0xA610),
        jr(ra),
        nop()
    ]))

    # 3. Write the 'AP Item' string.
    pine.write_bytes(0x2DA610, bytes([0x41, 0x50, 0x20, 0x49]))
    pine.write_bytes(0x2DA614, bytes([0x74, 0x65, 0x6d, 0x00]))

    # Replace 'Body' string used when receiving a body from an NPC with just a double-quote
    #     (since it needs to be there to be used as the opening quote in "AP Item")
    pine.write_bytes(0x3338E8, bytes([0x22, 0x00]))

def hook_overworld_item_funcs(pine : Pine):
    """
    Convert overworld items to AP locations. Also modify the code that decides whether to display them
    to check whether they've been picked up, *not* whether we actually have the item.
    """
    # Overwrite existing set inventory calls to use our AP location handling function instead
    overworld_item_JALs = [
        # Function calls for inventory update
        0x2409E0, # Peach
        0x25C03C, # Wallet 
        0x25C2B4, # Fluffy Mushroom
        0x25C3F0, # Amethyst
        0x25C4D4, # Moonstone
        0x25C608, # Small Bottle
        0x25C6E8, # Black Opal
        0x25C7C8, # Papu Flower
        0x25C904, # Ruby
        0x25CAE8, # Fountain Pen
        0x25CBC8, # Blue Sapphire
        0x25D4A8, # Topaz
        0x25D5B8  # Emerald
    ]
    for address in overworld_item_JALs:
        pine.write_bytes(address, JAL_AP_LOCATION_FUNC_WRITE)

    # Also modify these functions to prevent overworld items from disappearing when we add that item to our
    #    inventory. (Road Trip uses the status of the item in your inventory to determine whether it should
    #    appear in the overworld.)
    overworld_item_inventory_checks = [
        0x25BF9C, # Wallet 
        0x25C218, # Fluffy Mushroom
        0x25C350, # Amethyst
        0x25C434, # Moonstone
        0x25C568, # Small Bottle
        0x25C648, # Black Opal
        0x25C728, # Papu Flower
        0x25C868, # Ruby
        0x25CA48, # Fountain Pen
        0x25CB28, # Blue Sapphire
        0x25D410, # Topaz
        0x25D520  # Emerald
    ]

    for address in overworld_item_inventory_checks:
        pine.write_bytes(address, JAL_AP_LOCATION_FUNC_READ)
        # All overworld items have a stamp check that will cause the item to not display if the stamp has
        #     been received. For example, the Fountain Pen and Stamp 39, "Found Benji's Fountain Pen".
        # This is done since items like the Fountain Pen are removed from your inventory when completing
        #      the stamp, and the overworld item needs to stay invisible.
        # Since these items and their locations are now separate for AP, we need to remove these stamp checks.
        # All of them are four instructions (16 bytes) after the inventory checks.
        pine.write_bytes(address+16, NOP_BYTES)


def hook_license_upgrades(pine : Pine):
    """
    Convert receiving a new license to an AP location (and remove vanilla behavior).
    """
    # Road Trip assumes that the 3 license upgrades will be obtained in order. As a result, it stores
    #   an int representing the current license the player has obtained (unlike with obtained parts and
    #   items, which use bitfields). So C-rank = 0x0, B-rank = 0x1, etc.
    #
    # This creates several problems for AP. For example: What if we obtain all of the license items prior
    #    to completing all B-rank races, and THEN finish all the B-rank races? In this case, the game
    #    won't even check to see whether it needs to award you a new license. This would make that 
    #    license location permanently missable.
    #
    # So we have to do several things here:
    #    1. Modify the game so that it uses the AP license location data to determine whether to run the
    #       check, NOT the actual obtained licenses. (We can skip the check if we've already cleared the
    #       location for that license - otherwise, run it.)
    #    2. When saving a license update, update the AP license location bitfield instead of incrementing
    #       the actual license count owned by the player.

    # --------------------------------------

    # PART 1 - Modifying the license upgrade check
    
    # The function starting at 0x237508 (NTSC) handles determining whether all of the races that match
    #   the rank of the race just completed are now complete.
    #
    # Here we call our hook to get license upgrades to check against the AP license location bitfield,
    #   instead of the licenses in your inventory.
    pine.write_bytes(0x23757C, mips([
        jal(0x2DA480)
    ]))

    # Hook 1
    addr = 0x2DA480
    pine.write_bytes(addr, mips([
        # Road Trip has a table containing (among other things) the corresponding rank for each race. 
        # a2 contains a pointer to the entry in that table for this race. Byte 3 contains the race rank.
        # This instruction loads that byte.
        lbu(a3, 3, a2), # was lb

        # Convert rank int to a bitfield index.
        addiu(t7, zero, 1),
        sllv(t7, t7, a3),

        # Load AP license location bitfield
        lui(t6, 0x178),
        addiu(t6, t6, 0x2A30),
        lbu(t6, 0, t6), # was lb

        # Bitwise and. Result is 0 if we haven't completed this license location yet, not 0 if we have.
        and_(t6, t6, t7),

        # If 0, return. Setting a3 to the current race rank on line 1 has already tricked the game 
        #   into thinking our current license matches this race, so the remaining check logic should work
        #   (i.e. for the 'you earned a license' text).
        beq(t6, zero, 3),
        nop(),
        # Otherwise, if 1, set a3 to 3 instead. This will cause the function to assume we have all licenses 
        #    already, and it will not check for any license rank completions.
        addiu(a3, zero, 3),

        jr(ra),
        nop()
    ]))

    # ---------------------------------

    # PART 2 - Modifying the function that updates your license count to update the AP license bitfield instead.
    
    # Overwrite existing lines that handle updating license byte in the 'handleRaceResults' function.
    pine.write_bytes(0x2366FC, mips([
        jal(0x2DA500),
        nop(),
        nop()
    ]))

    # Hook 2 - Updates our AP location byte for license checks
    addr = 0x2DA500
    pine.write_bytes(addr, mips([
        beq(v1, zero, 9),           # Skip if no license bit to update
        addiu(v0, zero, 1),         # Init license bit slot to 1
        addiu(v1, v1, -1),          # Number of left shifts to apply to 1 
        sllv(v1, v0, v1),           # Apply shift. Result is the bit that corresponds to this license.
        lui(v0, 0x0178),
        addiu(v0, v0, 0x2A30),
        lbu(t7, 0, v0), # was lb    # Load current AP license bitfield
        or_(v1, v1, t7),
        sb(v1, 0, v0),              # Store updated AP license bitfield
        jr(ra),
        nop()
    ]))

    # ---------------------------------

    # Additionally, make a small change to the congratulations message in Q's Factory.
    # If you just completed all the A-rank licenses, you'll receive a special message for getting the Super-A license.
    #   Otherwise, you'll get a generic message.
    # Neither of these are necessarily accurate in AP (completing a license check might get you something completely
    #   different), but the Super-A one could be especially confusing since it calls out that license by name.
    # The below changes the function to always use the generic congratulations message (i.e. changes the string pointer).
    pine.write_bytes(0x2a4508, bytes([0x07, 0x27, 0x2f, 0x00]))

    #002f26f0 - Location of generic license message (NTSC)
    # For some reason has several additional characters at the beginning, possibly some kind of opcodes?
    # Actual text starts at 0x2f2707

    #002f1e40 - Location of Super-A license message (NTSC)


def change_shop_item_quantity_display_to_ap(pine : Pine):
    """
    Change the "You (don't) have it" text in part shops to check whether we've purchased the part for AP, *not*
    whether we actually have the item that would be normally bought in that slot.
    """
    # Change the inventory check call to use our own hook instead
    pine.write_bytes(0x24723c, mips([
        jal(0x2DAC00)
    ]))

    # Change the check at this location to jump to our hook, which will prevent the game from displaying 
    #     "You have #" in part shops (except My City), and always display "You [don't] have it" instead.
    #     ("You have #" would not make sense for AP location checks.)
    pine.write_bytes(0x247284, mips([
        nop(),
        jal(0x2DAC80)
    ]))

    # Is this the My City part shop? If so, jump to the normal function. 
    # Otherwise, call the AP location check function
    addr = 0x2DAC00
    pine.write_bytes(addr, mips([
        lui(t0, 0x0033),
        ori(t0, t0, 0x5921),
        lbu(t1, 0, t0),
        addiu(t2, zero, 1),
        bne(t1, t2, 7),
        lbu(t1, 2, t0),
        addiu(t2, zero, 9),
        bne(t1, t2, 4),
        nop(),
        j(0x23D488),
        nop(),
        j(0x2DA200), # J_AP_LOCATION_FUNC_READ
        nop()
    ]))

    addr = 0x2DAC80
    # If this is not the My City part shop, jump to the part of the calling function that makes the
    #     displayed text "You have it" or "You don't have it".
    #     Otherwise, jump to the part that could make it "You have #".
    pine.write_bytes(addr, mips([
        lui(t0, 0x0033),
        ori(t0, t0, 0x5921),
        lbu(t1, 0, t0),
        addiu(t2, zero, 1),
        bne(t1, t2, 9),
        lbu(t1, 2, t0),
        addiu(t2, zero, 9),
        bne(t1, t2, 6),
        lui(t0, 0x0175),
        ori(t0, t0, 0x7B88),
        lw(v1, 0, t0),
        j(0x2472b0),    # If My City part shop
        nop(),
        j(0x24728c),    # If not My City part shop
        nop()
    ]))


def patch_npc_equips(pine : Pine):
    """
    Prevent NPCs from equipping parts to the player (which could allow them to, at least temporarily, have parts
    when they shouldn't in AP).
    """
    # Write hook in dialogue handler function that only allows your equipped items to be modified
    #   if you are in the Ski Jump lobby (since it should still remove the Flight Wing).
    # All other NPC equips should be disabled. (e.g. Billboards, Wing Set + Propeller)
    pine.write_bytes(0x23B984, mips([
        jal(0x2DAB80)
    ]))

    addr = 0x2DAB80
    pine.write_bytes(addr, mips([
        lui(t0, 0x0033),
        ori(t0, t0, 0x5921),
        lbu(t1, 0, t0),
        addiu(t2, zero, 7),
        bne(t1, t2, 7),
        lbu(t1, 2, t0),
        addiu(t2, zero, 6),
        bne(t1, t2, 4),
        nop(),
        j(0x23C418),
        nop(),
        jr(ra),
        nop()
    ]))


def patch_npc_dialogue_triggers(pine : Pine):
    """
    Patch some dialogue opcodes that would cause dialogue to be skipped if the player already has an item.
    Not patching these would make some AP locations missable if the player receives the vanilla item for
    that location prior to completing it.
    """
    # Patch Luke's dialogue for the UnbaboDoll. Normally, the dialogue where he would give you the doll
    #   is skipped if you already have the stamp, *or* if you already have the item.
    # This changes the item check to use the collectible index after the final one (the Peach), which is never set.
    pine.write_bytes(0x315FC4, bytes([0x30]))


def patch_tin_raceway_requirements(pine : Pine):
    """
    Make Tin Raceway accessible prior to becoming the president so it can be a location check.
    """
    # Change the license requirement for entering Tin Raceway to the A License.
    #   This does not make Tin Raceway a required race for obtaining the Super A License.
    pine.write_bytes(0x2BDF63, bytes([2]))

    # Also for Tin Raceway, modify the assembly instruction at the below location to be an unconditional branch.
    #   This branch typically checks whether the race you're trying to enter is Tin Raceway. 
    #   If it is, it then checks if you've completed stamp 100 (Became the President), and prevents you from
    #   entering if you haven't (displays "Under construction").
    pine.write_bytes(0x239E12, bytes([0,0x10]))


def disable_func_that_overwrites_ap_save_data(pine : Pine):
    """
    Disable a function call that will, for some reason, reset our AP save data upon recruiting a 2nd teammate.

    I've yet to find any indication in Ghidra that the data this writes is ever read by anything in the game.
    No idea why these 0x0 writes happen - for now, I'm just going to remove this call, run some test multiworlds,
    and see if anything breaks.
    """
    pine.write_bytes(0x23daf4, mips([
        jr(ra) # (instead of a j to the offending function)
    ]))

def disable_vanilla_my_city_part_shop_handling(pine : Pine):
    """
    Overwrite function that handles vanilla part availability behavior for My City part shop to a jr ra (i.e. do nothing).
    In vanilla, all parts from part shops you have previously visited are available in My City.
    """
    addr = 0x267C30
    pine.write_bytes(addr, mips([
        jr(ra),
        nop()
    ]))


def encode_as_ascii_code_list(string : str) -> list[int]:
    codes = []
    for char in string:
        try:
            codes.append(list(char.encode('ascii'))[0])
        # If a character is not a valid ASCII character, fall back to a space.
        except Exception:
            if char == "é":
                codes.append(0x65) # Special case for 'é' since it's used throughout Pokémon, replace with 'e'
            else:
                codes.append(0x20) # 0x20 is a space in ASCII
    
    return codes

def hook_shops_to_display_ap_item_strings(pine : Pine, shop_strings : list):
    """
    Write the AP part shop descriptions (item name, player, classification) into the now-unused email text data.
    """
    from BaseClasses import ItemClassification

    for description in shop_strings:
        addr = Addresses.ADDR_SHOP_STRINGS + (Addresses.SHOP_STRING_LENGTH * description["vanilla_item_id"])

        # Encode the item name and the name of the player it's for into a list of ASCII codes.
        item_name_bytes = bytes(encode_as_ascii_code_list(description["item_name"]) + [0]) # Add an extra space before the player name for spacing in-game, and the null terminator
        player_bytes = bytes([0x20] + encode_as_ascii_code_list(description["player"]) + [0]) # Add null terminator
        item_classification_addr = []

        match(description["item_classification"]):
            case ItemClassification.progression:
                temp = Addresses.ADDR_PART_SHOP_ITEM_CLASSIFICATIONS
            case ItemClassification.useful:
                temp = Addresses.ADDR_PART_SHOP_ITEM_CLASSIFICATIONS + 0x10
            case ItemClassification.filler:
                temp = Addresses.ADDR_PART_SHOP_ITEM_CLASSIFICATIONS + 0x18
            case ItemClassification.trap:
                temp = Addresses.ADDR_PART_SHOP_ITEM_CLASSIFICATIONS + 0x20
            case _:
                temp = Addresses.ADDR_PART_SHOP_ITEM_CLASSIFICATIONS + 0x28
        
        item_classification_addr = list(temp.to_bytes(4))
        item_classification_addr.reverse()
        item_classification_addr = bytes(item_classification_addr)

        pine.write_bytes(addr, item_name_bytes)
        pine.write_bytes(addr+Addresses.OFFSET_SHOP_STRING_PLAYER_NAME, player_bytes)
        pine.write_bytes(addr+Addresses.OFFSET_SHOP_STRING_ITEM_CLASSIFICATION_PTR, item_classification_addr)
        
    # 'Progression' string
    pine.write_bytes(0x2DA650, bytes([0x20, 0x50, 0x72, 0x6f]))
    pine.write_bytes(0x2DA654, bytes([0x67, 0x72, 0x65, 0x73]))
    pine.write_bytes(0x2DA658, bytes([0x73, 0x69, 0x6f, 0x6e]))
    pine.write_bytes(0x2DA65C, bytes([0x00, 0x00, 0x00, 0x00]))

    # 'Useful' string
    pine.write_bytes(0x2DA660, bytes([0x20, 0x55, 0x73, 0x65]))
    pine.write_bytes(0x2DA664, bytes([0x66, 0x75, 0x6c, 0x00]))

    # 'Filler' string
    pine.write_bytes(0x2DA668, bytes([0x20, 0x46, 0x69, 0x6c]))
    pine.write_bytes(0x2DA66C, bytes([0x6c, 0x65, 0x72, 0x00]))

    # 'Trap' string
    pine.write_bytes(0x2DA670, bytes([0x20, 0x54, 0x72, 0x61]))
    pine.write_bytes(0x2DA674, bytes([0x70, 0x00, 0x00, 0x00]))

    # 'Other' string
    pine.write_bytes(0x2DA678, bytes([0x20, 0x4f, 0x74, 0x68]))
    pine.write_bytes(0x2DA67C, bytes([0x65, 0x72, 0x00, 0x00]))

    # ------------------------------------------

    # Change JAL in func that handles the final buy confirmation to our hook.
    # This will change it to display the full AP item name.
    addr = 0x2DA980
    pine.write_bytes(0x268910, mips([
        jal(addr)
    ]))

    # Change JAL that would get the address of the vanilla parts description to our hook
    pine.write_bytes(0x22e9c4, mips([
        jal(addr + 0xC) # jal 0x002DA98C
    ]))

    # Write the hook to replace the part descriptions in stores with AP item data
    addr = 0x2DA980
    pine.write_bytes(addr, mips([
        # Start here if we want the full part name (set t0 to 1, checked later)
        # This would likely give us too many characters to fit in the part description box,
        #   but is important for the final buy confirmation so the player can see the full
        #   item name somewhere.
        addiu(t0, zero, 0x1),
        beq(zero, zero, 3),
        nop(),

        # Otherwise, start here for a truncated version of the item name (set t0 to 0, checked later)
        addiu(t0, zero, 0),

        # Check if we are in a part shop. If not, return.
        # (We don't want to modify part descriptions when in other places - for example,
        #   while changing our parts in a Q's Factory.)
        #
        # Load shop type value
        lui(t7, 0x0175),
        ori(t7, t7, 0x7B88),
        lbu(t7, 0, t7),
        addiu(t6, zero, 0x1),

        # Check if shop type value is one, else return
        beq(t7, t6, 4),
        nop(),
        j(0x2DAA9C), # JUMP TO 'PASSTHROUGH' BELOW
        nop(),

        # Check if this is the My City part shop. If so, return
        addiu(t7, zero, 0x9),
        lui(t6, 0x0033),
        ori(t6, t6, 0x5923),
        lbu(t6, 0, t6),
        bne(t7, t6, 4),
        nop(),
        j(0x2DAA9C), # JUMP TO 'PASSTHROUGH' BELOW
        nop(),

        # Init t5 and t6
        addiu(t5, zero, 0),
        addiu(t6, zero, 0),

        # Load address to table_length_table
        lui(t7, 0x002D),
        ori(t7, t7, 0xA100),

        # Iterate through table_length_table and add part counts of all prior part types
        beq(a0, zero, 6),
        addiu(a0, a0, -0x1),
        lbu(t5, 0, t7),
        addu(t6, t5, t6),
        addiu(t7, t7, 0x1),
        beq(zero, zero, -5),
        nop(),

        # Add ID of this part to the total
        addu(t6, t6, a1),

        # Multiply the total by 64 (each part description is 64 bytes)
        sll(t6, t6, 0x6),

        # Get address of part descriptions
        lui(t7, 0x0032),
        ori(t7, t7, 0x9460),

        # Add ID * 64 to that address to get the address of this part description
        addu(t7, t7, t6),

        # Test if t0 is not 0. 
        #   t0 == 0 -> Get truncated name
        #   t0 == 1 -> Get full name
        # If 1, jump to 'RETURN FULL NAME' at the end (we don't need the next part).
        bne(t0, zero, 32),
        nop(),

        # If t0 is 0, we're trying to fill the part description box.
        # a2 stores the current line we're trying to print in the part description
        # If it's line 0, get the name of the item.
        # If it's line 1, get the name of the player that item is for.
        # If it's line 2, get the AP item classification for that item (e.g. Progression, Useful, etc.)

        # Test if line 0. Decrement a2 and branch if not.
        bne(a2, zero, 16),

        # Line 0 - Get truncated version of item name
        # Copy characters to the temp address location, return the address to it
        addiu(a2, a2, -0x1),
        lui(t6, 0x002D),
        ori(t6, t6, 0xA630),
        addu(v0, zero, t6),
        addiu(t4, zero, 0x12), # Max character count is 18 (19 total when null terminator added)

        # Loop. Break if char in t5 is null terminator, or if we hit the max character count
        # (i.e. t4 == 0). Otherwise, copy char and increment.
        lbu(t5, 0, t7),
        sb(t5, 0, t6),
        beq(t5, zero, 23),
        sb(zero, 1, t6), # Write null terminator in following char - will get overwritten if we aren't done looping, otherwise sets null terminator
        addiu(t7, t7, 0x1),
        addiu(t6, t6, 0x1),
        beq(t4, zero, 19),
        nop(),
        beq(zero, zero, -8),
        addiu(t4, t4, -0x1),

        # Test if line 1. Decrement a2 and branch if not.
        bne(a2, zero, 5),
        addiu(a2, a2, -0x1),

        # Line 1 - Get name of player this item is for
        addiu(v0, t7, 0x2A), # (add offset to part description address)
        beq(zero, zero, 12),
        nop(),

        # Test if line 2. Decrement a2 and branch if not.
        bne(a2, zero, 6),
        addiu(a2, a2, -0x1),

        # Line 2 - Get the AP item classification
        addiu(t7, t7, 0x3C), # (add offset to part description address)
        lw(v0, 0, t7),
        beq(zero, zero, 6),
        nop(),

        # Line 3 - Return null terminator
        addiu(v0, t7, 0x3F), # (offset of null terminator)

        # Return
        jr(ra),
        nop(),

        # RETURN FULL NAME
        addiu(v0, t7, 0),
        jr(ra),
        nop(),

        # PASSTHROUGH
        # If we've jumped here, it's because we're either not in a part shop, or in the My City part shop.
        # Depending on whether this function was called for a part shop description or for the full part title displayed
        #   just before purchase, we need to now jump to different functions.
        beq(t0, zero, 4),
        nop(),
        j(0x243468),
        nop(),
        j(0x243848),
        nop()
    ]))


def enforce_area_access(pine : Pine, area_unlock_mode : int):
    """
    Create a function in RTA that, while in the overworld, checks every frame to see if the player has access
    to the chunk they are in, given their AP decorations/stamps (depending on the mode the player set in their YAML).
    
    If they don't have access, prevent them from interacting with anything, and display a warning on screen.
    """
    # 1. Check the progression mode
    # 2. If Decorations:
    #    - What chunk are we in?
    #    - Check the reference table for chunk -> region unlock ID
    #    - Do we have that item in our collectibles table?
    #    - If yes, return True, if no, return False
    # 3. If Stamps:
    #    - What chunk are we in?
    #    - Pull the AP stamp count
    #    - Check the reference table for chunk -> required stamp count
    #    - Is AP stamp count >= required stamp count?
    #    - If yes, return True, if no, return False
    # 4. Result:
    #    - If True: great, we can talk to people, enter houses, and pick up overworld items
    #    - If False: we can't do those things, print "No access!" in the top left of the screen

    # Create the chunk -> region unlock ID reference table
    NO_REGION = "None"
    SANDPOLIS = "Sandpolis"
    MY_CITY = "My City"
    CHESTNUT_CANYON = "Chestnut Canyon"
    FUJI_CITY = "Fuji City"
    MUSHROOM_ROAD = "Mushroom Road"
    PEACH_TOWN = "Peach Town"
    WHITE_MOUNTAIN = "White Mountain"
    WHITE_MOUNTAIN_MAIN = "White Mountain Main" # Need to handle White Mountain proper separately due to Moonstone being in the chunk (which should be in-logic for Mushroom Road)
    WINDMILLS = "Windmills"
    PAPAYA_ISLAND = "Papaya Island"
    CLOUD_HILL = "Cloud Hill"
    chunks = [
        # 0x0 through 0xF
        NO_REGION,NO_REGION,NO_REGION,NO_REGION,
        NO_REGION,SANDPOLIS,SANDPOLIS,SANDPOLIS,
        SANDPOLIS,SANDPOLIS,NO_REGION,MY_CITY,
        MY_CITY,MY_CITY,NO_REGION,NO_REGION,

        # 0x10 through 0x1F
        NO_REGION,NO_REGION,NO_REGION,CHESTNUT_CANYON,
        CHESTNUT_CANYON,NO_REGION,SANDPOLIS,FUJI_CITY,
        FUJI_CITY,NO_REGION,MY_CITY,NO_REGION,
        NO_REGION,NO_REGION,NO_REGION,NO_REGION,

        # 0x20 through 0x2F
        NO_REGION,WHITE_MOUNTAIN,NO_REGION,WHITE_MOUNTAIN_MAIN,
        MUSHROOM_ROAD,WHITE_MOUNTAIN,FUJI_CITY,WINDMILLS,
        FUJI_CITY,PEACH_TOWN,PEACH_TOWN,PEACH_TOWN,
        NO_REGION,PEACH_TOWN,NO_REGION,PAPAYA_ISLAND,

        # 0x30 through 0x3F
        NO_REGION,NO_REGION,WHITE_MOUNTAIN,NO_REGION,
        WHITE_MOUNTAIN,NO_REGION,NO_REGION,NO_REGION,
        NO_REGION,NO_REGION,PEACH_TOWN,NO_REGION,
        PEACH_TOWN,NO_REGION,NO_REGION,NO_REGION,

        # 0x40
        CLOUD_HILL
    ]

    data = []
    # For decorations mode, the table values should contain the IDs of both
    #   items that can unlock access to the chunk.
    # Items have a table ID and an index in that table, but all decorations
    #   are in the collectibles table (0xF), so only the index is needed.
    if area_unlock_mode == 0: # Decorations
        for chunk in chunks:
            if chunk == NO_REGION:
                data += [0,0]
            elif chunk == PEACH_TOWN:
                data += [0xA, 0xB]
            elif chunk == WINDMILLS:
                data += [0xFF, 0xFF]
            elif chunk == FUJI_CITY:
                data += [0xC, 0xD]
            elif chunk == MY_CITY:
                data += [0,0]
            elif chunk == SANDPOLIS:
                data += [0xe, 0xf]
            elif chunk == CHESTNUT_CANYON:
                data += [0x10, 0x11]
            elif chunk == MUSHROOM_ROAD:
                data += [0x1, 0x2]
            elif chunk == WHITE_MOUNTAIN:
                data += [0x9, 0x12]
            elif chunk == WHITE_MOUNTAIN_MAIN:
                data += [0xFE, 0xFE]
            elif chunk == PAPAYA_ISLAND:
                data += [0x13, 0x14]
            elif chunk == CLOUD_HILL:
                data += [0x15, 0x16]
            else:
                raise Exception("enforce_area_access, decorations table: Invalid region?")
            
        # DECORATIONS MODE PATCH
        addr = 0x2DA680
        pine.write_bytes(addr, mips([
            # TODO: Add comments
            addiu(sp, sp, -0x4),
            sw(ra, 0, sp),
            lui(t0, 0x0033),
            addiu(t0, t0, 0x5954),
            lbu(t0, 0, t0),
            lui(t1, 0x002D),
            ori(t1, t1, 0xA580),
            addu(t1, t1, t0),
            addu(t1, t1, t0),
            lbu(t2, 1, t1),
            lbu(t1, 0, t1),
            beq(t1, zero, 53),
            ori(t3, zero, 0xFF),
            bne(t1, t3, 14),
            nop(),
            lui(t3, 0x0177),
            ori(t3, t3, 0xACE0),
            lhu(t3, 2, t3),
            slti(t3, t3, 0x43B0),
            bne(t3, zero, 5),
            nop(),
            addiu(t0, zero, 0x2B),
            beq(zero, zero, -17),
            nop(),
            addiu(t0, zero, 0x23),
            beq(zero, zero, -20),
            nop(),
            ori(t3, zero, 0xFE),
            bne(t1, t3, 14),
            nop(),
            lui(t3, 0x0177),
            ori(t3, t3, 0xACE0),
            lhu(t3, 10, t3),
            slti(t3, t3, 0x4310),
            bne(t3, zero, 5),
            nop(),
            addiu(t0, zero, 0x25),
            beq(zero, zero, -32),
            nop(),
            addiu(t0, zero, 0x24),
            beq(zero, zero, -35),
            nop(),
            lui(t3, 0x002D),
            ori(t3, t3, 0xA57F),
            addiu(a0, zero, 0xF),
            addiu(a1, t1, 0),
            jal(0x23D488),
            addiu(a2, zero, 0),
            bne(v0, zero, 16),
            addiu(a0, zero, 0xF),
            addiu(a1, t2, 0),
            jal(0x23D488),
            addiu(a2, zero, 0),
            bne(v0, zero, 11),
            nop(),
            addiu(a0, zero, 0x11),
            addiu(a1, zero, 0x7),
            lui(a2, 0x002D),
            ori(a2, a2, 0xA620),
            addiu(a3, zero, 0x6),
            lw(ra, 0, sp),
            addiu(sp, sp, 0x4),
            j(0x203FC8),
            sb(v0, 0, t3),
            lw(ra, 0, sp),
            addiu(v0, zero, 0x1),
            lui(t3, 0x002D),
            ori(t3, t3, 0xA57F),
            addiu(sp, sp, 0x4),
            jr(ra),
            sb(v0, 0, t3),
            nop()
        ]))

    elif area_unlock_mode == 1: # Stamps       
        # For stamp mode, the table values should contain the number of AP stamp items
        #   needed to unlock access to the chunk.
        for chunk in chunks:
            if chunk == NO_REGION:
                data += [0,0]
            elif chunk == PEACH_TOWN:
                data += [0, 0]
            elif chunk == WINDMILLS:
                data += [0xFF, 0]
            elif chunk == FUJI_CITY:
                data += [5, 0]
            elif chunk == MY_CITY:
                data += [0,0]
            elif chunk == SANDPOLIS:
                data += [10, 0]
            elif chunk == CHESTNUT_CANYON:
                data += [15, 0]
            elif chunk == MUSHROOM_ROAD:
                data += [20, 0]
            elif chunk == WHITE_MOUNTAIN:
                data += [25, 0]
            elif chunk == WHITE_MOUNTAIN_MAIN:
                data += [0xFE, 0x0]
            elif chunk == PAPAYA_ISLAND:
                data += [30, 0]
            elif chunk == CLOUD_HILL:
                data += [35, 0]
            else:
                raise Exception("enforce_area_access, stamp table: Invalid region?")
        
        # STAMP MODE PATCH
        addr = 0x2DA680
        pine.write_bytes(addr, mips([
            # TODO: Add comments
            addiu(sp, sp, -0x4),
            sw(ra, 0, sp),
            lui(t0, 0x0033),
            addiu(t0, t0, 0x5954),
            lbu(t0, 0, t0),
            lui(t1, 0x002D),
            ori(t1, t1, 0xA580),
            addu(t1, t1, t0),
            addu(t1, t1, t0),
            nop(),
            lbu(t1, 0, t1),
            beq(t1, zero, 50),
            nop(),
            ori(t3, zero, 0xFF),
            bne(t1, t3, 14),
            nop(),
            lui(t3, 0x0177),
            ori(t3, t3, 0xACE0),
            lhu(t3, 2, t3),
            slti(t3, t3, 0x43B0),
            bne(t3, zero, 5),
            nop(),
            addiu(t0, zero, 0x2B),
            beq(zero, zero, -18),
            nop(),
            addiu(t0, zero, 0x23),
            beq(zero, zero, -21),
            nop(),
            ori(t3, zero, 0xFE),
            bne(t1, t3, 14),
            nop(),
            lui(t3, 0x0177),
            ori(t3, t3, 0xACE0),
            lhu(t3, 10, t3),
            slti(t3, t3, 0x4310),
            bne(t3, zero, 5),
            nop(),
            addiu(t0, zero, 0x25),
            beq(zero, zero, -33),
            nop(),
            addiu(t0, zero, 0x24),
            beq(zero, zero, -36),
            nop(),
            # Save file location for AP stamp count: 0x1782A31 (next to license checks)
            lui(t3, 0x0178),
            ori(t3, t3, 0x2A31),
            lbu(t3, 0, t3),
            addiu(t3, t3, 0x1),
            slt(v0, t1, t3),
            lui(t3, 0x002D),
            ori(t3, t3, 0xA57F),
            bne(v0, zero, 11),
            nop(),
            addiu(a0, zero, 0x11),
            addiu(a1, zero, 0x7),
            lui(a2, 0x002D),
            ori(a2, a2, 0xA620),
            addiu(a3, zero, 0x6),
            lw(ra, 0, sp),
            addiu(sp, sp, 0x4),
            j(0x203FC8),
            sb(v0, 0, t3),
            lw(ra, 0, sp),
            addiu(v0, zero, 0x1),
            lui(t3, 0x002D),
            ori(t3, t3, 0xA57F),
            addiu(sp, sp, 0x4),
            jr(ra),
            sb(v0, 0, t3),
            nop()
        ]))

        # In stamp mode, display the number of AP stamp items received in the Stamps page in the Notebook
        # Part 1 - Write "AP stamps: " string
        addr = 0x2DAE80 # Ran out of room in the other location, moving lower
        pine.write_bytes(addr+0, bytes([0x41, 0x50, 0x20, 0x53]))
        pine.write_bytes(addr+4, bytes([0x74, 0x61, 0x6d, 0x70]))
        pine.write_bytes(addr+8, bytes([0x73, 0x3a, 0x20]))

        # Part 2 - Jump to our hook instead of returning from notebook stamps page task
        addr = 0x2DAD00 # Hook address
        pine.write_bytes(0x265FDC, mips([
            j(addr)
        ]))

        # Part 3 - Hook
        # Takes the AP stamp count, converts the number to a string, concatenates it to the end of
        #    our "AP stamps: " string, and then passes that string's address (+ positioning values
        #    and text color value) to RTA's print text function.
        pine.write_bytes(addr, mips([
            addiu(sp, sp, -0x4),
            sw(ra, 0, sp),
            lui(a0, 0x0178),
            ori(a0, a0, 0x2A31),
            lbu(a0, 0, a0),
            lui(a2, 0x002D),
            ori(a2, a2, 0xAE80),
            sb(zero, 0xB, a2),
            jal(0x2DAD80),
            addiu(a1, zero, 0x64),
            addu(a0, zero, v0),
            jal(0x2DAD80),
            addiu(a1, zero, 0xA),
            addu(a0, zero, v0),
            jal(0x2DAD80),
            addiu(a1, zero, 0x1),
            addiu(a0, zero, 0x14),
            addiu(a1, zero, 0x4),
            addiu(a3, zero, 0x0),
            lw(ra, 0, sp),
            j(0x203FC8),
            addiu(sp, sp, 0x4),
            nop(),
        ]))

        # Function extracting a character from a number (sets only one char, for one digit)
        # Called by above
        # a0 - Number to print
        # a1 - Lowest possible value that contains the digit to test against (i.e to get hundreds digit, pass 100, or 0x64)
        # a2 - Address to string, we'll save the char to the end of the string and then add a null terminator after it
        addr = 0x2DAD80
        pine.write_bytes(addr, mips([
            addiu(t0, zero, 0),
            addu(t1, zero, a2),
            lbu(t2, 0, t1),
            bne(t2, zero, -0x1),
            addiu(t1, t1, 0x1),
            addu(t2, zero, a0),
            sltu(t3, t2, a1),
            bne(t3, zero, 5),
            nop(),
            addiu(t0, t0, 0x1),
            beq(zero, zero, -4),
            subu(t2, t2, a1),
            addiu(t1, t1, -0x1),
            bne(t0, zero, 11),
            nop(),
            addiu(t4, zero, 0x1),
            beq(t4, a1, 8),
            nop(),
            lbu(t5, -1, t1),
            addiu(t6, zero, 0x20),
            bne(t5, t6, 4),
            nop(),
            beq(zero, zero, 4),
            nop(),
            addiu(t0, t0, 0x30),
            sb(t0, 0, t1),
            sb(zero, 1, t1),
            jr(ra),
            addu(v0, zero, t2),
            nop()
        ]))

    # Write the reference table into the game's memory
    pine.write_bytes(0x2DA580, bytes(data))

    # Write 'No access!' string
    pine.write_bytes(0x2DA620, bytes([0x4e, 0x6f, 0x20, 0x61]))
    pine.write_bytes(0x2DA624, bytes([0x63, 0x63, 0x65, 0x73]))
    pine.write_bytes(0x2DA628, bytes([0x73, 0x21, 0x00, 0x00]))

    # While in the overworld, if the player doesn't have access to the chunk they're currently in,
    #   display "No access!" in the top-left of the screen.
    # This calls the big patch above every frame, which sets a boolean value that the hooks below
    #   reference to see if they should allow an action or not.
    pine.write_bytes(0x24332C, mips([
        j(0x2DA680)
    ]))

    # NPCs and entrances
    addr = 0x2DA800
    pine.write_bytes(0x210EEC, mips([
        jal(addr) # Jump-and-link to hook
    ]))
    # Hook
    pine.write_bytes(addr, mips([
        beq(v0, zero, 8),
        lui(t0, 0x002D),
        ori(t0, t0, 0xA57F),
        lbu(t0, 0, t0), # was lb
        beq(t0, zero, 4),
        nop(),
        jr(ra),
        nop(),
        j(0x211100),
        nop()
    ]))

    # Q Coins
    addr = 0x2DA880
    pine.write_bytes(0x241C98, mips([
        jal(addr), # Jump-and-link to hook
        nop()
    ]))
    # Hook
    pine.write_bytes(addr, mips([
        bc1fl(9),
        nop(),
        lui(t0, 0x002D),
        ori(t0, t0, 0xA57F),
        lbu(t0, 0, t0), # was lb
        beq(t0, zero, 4),
        nop(),
        jr(ra),
        nop(),
        j(0x241D00),
        nop()
    ]))

    # Overworld items
    addr = 0x2DA900 
    # Hook
    pine.write_bytes(addr, mips([
        addiu(t1, ra, 0),
        lui(t0, 0x002D),
        ori(t0, t0, 0xA57F),
        lbu(t0, 0, t0), # was lb
        bne(t0, zero, 5),
        nop(),
        addiu(ra, ra, 0x10),
        jr(ra),
        nop(),
        jal(0x258E50),
        addiu(ra, t1, 0),
        nop()
    ]))

    # Once the game confirms that the player is colliding with an overworld item, jump to the above area access hook
    overworld_item_collision_checks = [
        0x2409C8, # Peach
        0x25C024, # Wallet 
        0x25C29C, # Fluffy Mushroom
        0x25C3D8, # Amethyst
        0x25C4BC, # Moonstone
        0x25C5F0, # Small Bottle
        0x25C6D0, # Black Opal
        0x25C7B0, # Papu Flower
        0x25C8EC, # Ruby
        0x25CAD0, # Fountain Pen
        0x25CBB0, # Blue Sapphire
        0x25D490, # Topaz
        0x25D5A0  # Emerald
    ]

    for address in overworld_item_collision_checks:
        pine.write_bytes(address+8, mips([
            jal(addr) # Jump-and-link to hook
        ]))
