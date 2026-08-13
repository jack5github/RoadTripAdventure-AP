from typing import Callable
from functools import partial

# References: 
# https://student.cs.uwaterloo.ca/~isg/res/mips/opcodes
# https://www.kth.se/social/files/563c63c9f276547044e8695f/mips-ref-sheet.pdf

# REGISTER NUMBERS
zero = 0
at = 1
v0 = 2
v1 = 3
a0 = 4
a1 = 5
a2 = 6
a3 = 7
t0 = 8
t1 = 9
t2 = 10
t3 = 11
t4 = 12
t5 = 13
t6 = 14
t7 = 15
s0 = 16
s1 = 17
s2 = 18
s3 = 19
s4 = 20
s5 = 21
s6 = 22
s7 = 23
t8 = 24
t9 = 25
k0 = 26
k1 = 27
gp = 28
sp = 29
fp = 30
ra = 31

# ENCODING FUNCTIONS
# All MIPS instructions are 32 bits, and follow one of three encoding patterns (Register, Immediate, Jump)
# All encoding functions return binary machine code, stored as a string
def mips_instruction_register_encoding(opcode, src_register_1 : int, src_register_2 : int, dest_register : int, shift_amount : int) -> str:
    # ------ Bit layout ------
    # empty             6 bits
    # src_register_1    5 bits
    # src_register_2    5 bits
    # dest_register     5 bits
    # shift_amount      5 bits
    # opcode            6 bits

    def validate_registers(dest, src1, src2, shift_amount):
        if dest > 31 or dest < 0:
            return False
        if src1 > 31 or src1 < 0:
            return False
        if src2 > 31 or src2 < 0:
            return False
        if shift_amount > 31 or shift_amount < 0:
            return False
        return True
    
    if validate_registers(dest_register, src_register_1, src_register_2, shift_amount):
        # https://stackoverflow.com/a/10411184
        return (
            '000000' 
            + bin(src_register_1).replace('0b','').zfill(5)
            + bin(src_register_2).replace('0b','').zfill(5)
            + bin(dest_register).replace('0b','').zfill(5)
            + bin(shift_amount).replace('0b','').zfill(5)
            + opcode
        )
    else:
        raise Exception("Bad MIPS instruction (register encoded)")

def mips_instruction_immediate_encoding(opcode, register_1 : int, register_2 : int, immediate : int) -> str:
    # ------ Bit layout ------
    # opcode            6 bits
    # register_1        5 bits
    # register_2        5 bits
    # immediate        16 bits

    def validate_registers(dest, src, immediate):
        if dest > 31 or dest < 0:
            return False
        if src > 31 or src < 0:
            return False
        if immediate > 65535 or immediate < -32768: # Technically, integers greater than 32767 will become negative, but we can't reject them as we do often pass numbers between 32767 and 65535, because memory addresses (ori)
            return False
        return True
    
    if validate_registers(register_1, register_2, immediate):
        if immediate < 0 and immediate > -32768: # Handle negative immediates
            immediate = 65536 + immediate
        
        # https://stackoverflow.com/a/10411184
        return (
            opcode 
            + bin(register_1).replace('0b','').zfill(5)
            + bin(register_2).replace('0b','').zfill(5)
            + bin(immediate).replace('0b','').zfill(16)
        )
    else:
        raise Exception("Bad MIPS instruction (immediate encoded)")

def mips_instruction_jump_encoding(opcode, immediate : int) -> str:
    # ------ Bit layout ------
    # opcode            6 bits
    # immediate        26 bits

    def validate_registers(immediate):
        if immediate > 134217727 or immediate % 4 != 0:
            return False
        return True
    
    if validate_registers(immediate):
        # https://stackoverflow.com/a/10411184
        return (
            opcode 
            + bin(immediate // 4).replace('0b','').zfill(26)
        )
    else:
        raise Exception("Bad MIPS instruction (jump encoded)")

# INSTRUCTIONS
def addiu(dest, src, immediate):
    """
    Add immediate unsigned
    """
    opcode = '001001'
    return mips_instruction_immediate_encoding(opcode, src, dest, immediate)

def addu(dest, src_1, src_2):
    """
    Add unsigned
    """
    opcode = '100001'
    return mips_instruction_register_encoding(opcode, src_1, src_2, dest, 0)

def and_(dest, src1, src2):
    """
    Bitwise logical and
    """
    opcode = '100100'
    return mips_instruction_register_encoding(opcode, src1, src2, dest, 0)

def andi(dest, src, immediate):
    """
    Bitwise logical and, using provided immediate
    """
    opcode = '001100'
    return mips_instruction_immediate_encoding(opcode, src, dest, immediate)

def beq(src_1, src_2, offset):
    """
    Branch if src_1 and src_2 are equal
    """
    # Label-compatible version of function
    def _using_label(src_1, src_2, label : str, line_number : int, labels : dict):
        opcode = '000100'
        offset = labels[label] - (line_number + 1)
        return mips_instruction_immediate_encoding(opcode, src_1, src_2, offset)
    
    # If the offset is a label, return a partial function filling in all the details
    #   except the line number and labels dictionary, which will be needed to process
    #   the label.
    if isinstance(offset, str):
        return partial(_using_label, src_1, src_2, offset)

    # If here, offset should be an immediate (int)
    opcode = '000100'
    offset -= 1
    return mips_instruction_immediate_encoding(opcode, src_1, src_2, offset)

def bgtz(src, offset):
    """
    Branch if src greater than zero
    """
    # Label-compatible version of function
    def _using_label(src, label : str, line_number : int, labels : dict):
        opcode = '000111'
        offset = labels[label] - (line_number + 1)
        return mips_instruction_immediate_encoding(opcode, src, 0, offset)
    
    # If the offset is a label, return a partial function filling in all the details
    #   except the line number and labels dictionary, which will be needed to process
    #   the label.
    if isinstance(offset, str):
        return partial(_using_label, src, offset)

    # If here, offset should be an immediate (int)
    opcode = '000111'
    offset -= 1
    return mips_instruction_immediate_encoding(opcode, src, 0, offset)

def bltz(src, offset):
    """
    Branch if src less than zero
    """
    # Label-compatible version of function
    def _using_label(src, label : str, line_number : int, labels : dict):
        opcode = '000001'
        offset = labels[label] - (line_number + 1)
        return mips_instruction_immediate_encoding(opcode, src, 0, offset)
    
    # If the offset is a label, return a partial function filling in all the details
    #   except the line number and labels dictionary, which will be needed to process
    #   the label.
    if isinstance(offset, str):
        return partial(_using_label, src, offset)
    
    # If here, offset should be an immediate (int)
    opcode = '000001'
    offset -= 1
    return mips_instruction_immediate_encoding(opcode, src, 0, offset)

def bne(src_1, src_2, offset):
    """
    Branch if src_1 and src_2 are not equal
    """
    # Label-compatible version of function
    def _using_label(src_1, src_2, label : str, line_number : int, labels : dict):
        opcode = '000101'
        offset = labels[label] - (line_number + 1)
        return mips_instruction_immediate_encoding(opcode, src_1, src_2, offset)
    
    # If the offset is a label, return a partial function filling in all the details
    #   except the line number and labels dictionary, which will be needed to process
    #   the label.
    if isinstance(offset, str):
        return partial(_using_label, src_1, src_2, offset)
    
    # If here, offset should be an immediate (int)
    opcode = '000101'
    offset -= 1
    return mips_instruction_immediate_encoding(opcode, src_1, src_2, offset)

def j(address):
    """
    Jump to the provided address
    """
    opcode = '000010'
    return mips_instruction_jump_encoding(opcode, address)

def jal(address):
    """
    Jump and link
    
    Jump to the provided address, and store the next line to run in this function (i.e. the line after the branch
    delay slot) in register ra

    This allows for using jr(ra) in the called function to return - execution in this function will continue from
    the address stored in ra.
    """
    opcode = '000011'
    return mips_instruction_jump_encoding(opcode, address)

def jr(src):
    """
    Jump to the address stored in the provided register
    """
    opcode = '001000'
    return mips_instruction_register_encoding(opcode, src, 0, 0, 0)

def lbu(dest_register, offset, src_address):
    """
    Load byte unsigned
    """
    opcode = '100100'
    return mips_instruction_immediate_encoding(opcode, src_address, dest_register, offset)

def lhu(dest_register, offset, src_address):
    """
    Load halfword unsigned
    """
    opcode = '100101'
    return mips_instruction_immediate_encoding(opcode, src_address, dest_register, offset)

def lui(dest, immediate):
    """
    Load upper immediate

    Sets the upper 2 bytes of the provided register to the provided immediate (registers are 4 bytes)
    """
    opcode = '001111'
    return mips_instruction_immediate_encoding(opcode, 0, dest, immediate)

def lw(dest_register, offset, src_address):
    """
    Load word

    NOTE: lw takes an additional cycle to actually load the word into the register, do not 
    try to read that register on the line that immediately follows lw (it will still be the 
    old value)
    """
    opcode = '100011'
    return mips_instruction_immediate_encoding(opcode, src_address, dest_register, offset)

def or_(dest, src1, src2):
    """
    Bitwise logical or
    """
    opcode = '100101'
    return mips_instruction_register_encoding(opcode, src1, src2, dest, 0)

def ori(dest, src, immediate):
    """
    Bitwise logical or, using provided immediate
    """
    opcode = '001101'
    return mips_instruction_immediate_encoding(opcode, src, dest, immediate)

def sb(src_register, offset, dest_address):
    """
    Store byte
    """
    opcode = '101000'
    return mips_instruction_immediate_encoding(opcode, dest_address, src_register, offset)

def sll(dest, src, immediate):
    """
    Shift left logical
    """
    opcode = '000000'
    return mips_instruction_register_encoding(opcode, 0, src, dest, immediate)

def sllv(dest, src, shift_src):
    """
    Shift left logical variable
    """
    opcode = '000100'
    return mips_instruction_register_encoding(opcode, shift_src, src, dest, 0)

def slt(dest, src_smaller, src_larger):
    """
    Set dest to 1 if src_smaller is less than src_larger. Assume both are signed integers.
    """
    opcode = '101010'
    return mips_instruction_register_encoding(opcode, src_smaller, src_larger, dest, 0)

def slti(dest, src, immediate):
    """
    Set dest to 1 if src is less than immediate. Assume src is a signed integer.
    """
    opcode = '001010'
    return mips_instruction_immediate_encoding(opcode, src, dest, immediate)

def sltu(dest, src_smaller, src_larger):
    """
    Set dest to 1 if src_smaller is less than src_larger. Assume both are unsigned integers.
    """
    # uuuuughhh, the University of Waterloo reference apparently has a couple of opcode typos
    # See page 151 for the correct opcode for sltu: https://www.cs.cmu.edu/afs/cs/academic/class/15740-f97/public/doc/mips-isa.pdf
    opcode = '101011'
    return mips_instruction_register_encoding(opcode, src_smaller, src_larger, dest, 0)

def sltiu(dest, src, immediate):
    """
    Set dest to 1 if src is less than immediate. Assume src is an unsigned integer.
    """
    # Another UWaterloo opcode typo
    # See page 150 for correct opcode: https://www.cs.cmu.edu/afs/cs/academic/class/15740-f97/public/doc/mips-isa.pdf
    opcode = '001011'
    return mips_instruction_immediate_encoding(opcode, src, dest, immediate)

def subu(dest, src_1, src_2):
    """
    Subtract unsigned
    """
    opcode = '100011'
    return mips_instruction_register_encoding(opcode, src_1, src_2, dest, 0)

def sw(src_register, offset, dest_address):
    """
    Store word
    """
    opcode = '101011'
    return mips_instruction_immediate_encoding(opcode, dest_address, src_register, offset)

def xori(dest, src, immediate):
    """
    Bitwise logical exclusive or, using provided immediate
    """
    opcode = '001110'
    return mips_instruction_immediate_encoding(opcode, src, dest, immediate)

def nop():
    """
    No operation
    """
    return '00000000'*4

def bc1fl(offset):
    """
    Branch if the floating point comparison register (set by floating point comparison instructions) is currently 0 (i.e. false).
    """
    # Reference page 234 for why the below values are used: https://www.cs.cmu.edu/afs/cs/academic/class/15740-f97/public/doc/mips-isa.pdf
    # Note that using the immediate encoding function here is (to my understanding) a hack, see page 299 
    #   in the above for proper details on FPU instruction encoding.
    offset -= 1
    return mips_instruction_immediate_encoding('010001', int('01000',2), int('000' + '1' + '0', 2), offset)


def mips(instruction_bin_strings : list[str | Callable], endianness : str = "little") -> bytes:
    '''
    Converts a list of machine code (i.e. binary) strings to a bytes object
    '''
    instructions = bytearray()

    # Process any labels
    line_number = 1
    label_indices = []
    labels = {}
    for index, instruction in enumerate(instruction_bin_strings):
        if isinstance(instruction, str) and instruction.startswith("LABEL "):
            labels[instruction[len("LABEL "):]] = line_number
            label_indices.append(index)
            line_number -= 1
        line_number += 1
    
    removed_labels = 0
    for index in label_indices:
        instruction_bin_strings.pop(index - removed_labels)
        removed_labels += 1

    # Now that labels are removed, process all actual MIPS instructions
    for index, instruction in enumerate(instruction_bin_strings):
        # Handle any branch instructions expecting a label
        if isinstance(instruction, Callable): # Branch functions expecting a label return a partial function, so we can pass the current line number and label dictionary at this time
            instruction = instruction(index+1, labels) # Replaces the Callable with the resulting binary string

        # From this point on, the instruction is assumed to be a binary string
        if len(instruction) != 32:
            raise Exception("Provided MIPS instruction not 32 bits, invalid instruction")
        
        # [2:] used to remove '0x' from string before passing to bytes.fromhex
        val = (hex(int(instruction, 2)))[2:].zfill(8)

        if endianness == 'little':
            temp = bytearray.fromhex(val)
            temp.reverse()
            instructions += bytes(temp)
        elif endianness == 'big':
            instructions += bytes.fromhex(val)
        else:
            raise Exception("Invalid endianness value, must be either 'little' or 'big'")
    
    return instructions

def label(string : str) -> str:
    return 'LABEL ' + string # Needed in order to differentiate these strings from the binary strings


def get_lower_nibble(word: int):
    """
    Get lower two bytes from a word
    """
    if word <= 0xFFFFFFFF and word >= 0:
        return word & 0x0000FFFF
    else:
        raise Exception("get_lower_nibble was passed either an int larger than a word, or a negative int")
    
def get_upper_nibble(word: int):
    """
    Get upper two bytes from a word
    """
    if word <= 0xFFFFFFFF and word >= 0:
        return (word & 0xFFFF0000) >> 16
    else:
        raise Exception("get_upper_nibble was passed either an int larger than a word, or a negative int")
