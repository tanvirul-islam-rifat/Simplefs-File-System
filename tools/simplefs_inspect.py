#!/usr/bin/env python3
"""Read-only inspector and consistency checker for SimpleFS images.

    python3 tools/simplefs_inspect.py disk.img             dump the image and check it
    python3 tools/simplefs_inspect.py disk.img --check     check only (exit status 1 on problems)
    python3 tools/simplefs_inspect.py disk.img --cat NAME  write one stored file to stdout

The on-disk format is re-implemented here from the specification rather than
shared with the C code, so the checker doubles as an independent oracle for the
test suite.
"""
import argparse
import struct
import sys
from collections import namedtuple

BLOCK_SIZE = 4096
TOTAL_BLOCKS = 64
TOTAL_INODES = 32
IMAGE_SIZE = TOTAL_BLOCKS * BLOCK_SIZE
INODE_BITMAP_BLOCK = 1
DATA_BITMAP_BLOCK = 2
INODE_TABLE_BLOCK = 3
DATA_REGION_BLOCK = 4
DATA_BLOCKS = 60
ROOT_INODE = 1
ROOT_DATA_BLOCK = 4
MAGIC = 0x53465331
MAX_DIRECT = 3
MAX_FILE_SIZE = MAX_DIRECT * BLOCK_SIZE
TYPE_FILE, TYPE_DIR = 1, 2

SUPERBLOCK = struct.Struct("<9I")
INODE = struct.Struct("<HHI3I108s")
DIRENT = struct.Struct("<IB59s")
DIRENTS = BLOCK_SIZE // DIRENT.size

EXPECTED_SUPERBLOCK = (MAGIC, BLOCK_SIZE, TOTAL_BLOCKS, TOTAL_INODES,
                       INODE_BITMAP_BLOCK, DATA_BITMAP_BLOCK, INODE_TABLE_BLOCK,
                       DATA_REGION_BLOCK, ROOT_INODE)

Inode = namedtuple("Inode", "type links size direct reserved")
Dirent = namedtuple("Dirent", "inode_no type raw")


def entry_name(entry):
    return entry.raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def ranges(numbers):
    """[4, 5, 6, 9] -> '4-6, 9'"""
    out, run = [], []
    for n in sorted(numbers):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(run)
        run = [n]
    if run:
        out.append(run)
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in out) or "none"


class Image:
    def __init__(self, data):
        self.data = bytes(data)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    def block(self, number):
        return self.data[number * BLOCK_SIZE:(number + 1) * BLOCK_SIZE]

    def superblock(self):
        return SUPERBLOCK.unpack_from(self.data, 0)

    def _inode_offset(self, number):
        return INODE_TABLE_BLOCK * BLOCK_SIZE + (number - 1) * INODE.size

    def inode(self, number):
        kind, links, size, d0, d1, d2, reserved = INODE.unpack_from(self.data, self._inode_offset(number))
        return Inode(kind, links, size, (d0, d1, d2), reserved)

    def inode_bytes(self, number):
        off = self._inode_offset(number)
        return self.data[off:off + INODE.size]

    def marked_bits(self, block, count):
        raw = self.block(block)
        return [i for i in range(count) if raw[i // 8] >> (i % 8) & 1]

    def _dirent_offset(self, slot):
        return ROOT_DATA_BLOCK * BLOCK_SIZE + slot * DIRENT.size

    def dirent(self, slot):
        return Dirent(*DIRENT.unpack_from(self.data, self._dirent_offset(slot)))

    def entries(self):
        """(slot, Dirent) for every slot in the root directory that is in use."""
        found = []
        for slot in range(DIRENTS):
            entry = self.dirent(slot)
            if entry.inode_no:
                found.append((slot, entry))
        return found

    def lookup(self, name):
        for slot, entry in self.entries():
            if slot >= 2 and entry_name(entry) == name:
                return entry.inode_no
        raise KeyError(name)

    def read_file(self, name):
        inode = self.inode(self.lookup(name))
        content = b"".join(self.block(b) for b in inode.direct if b)
        return content[:inode.size]

    def check(self):
        """Return a list of human-readable problems; an empty list means consistent."""
        if len(self.data) != IMAGE_SIZE:
            return [f"image is {len(self.data)} bytes, expected {IMAGE_SIZE}"]

        problems = []
        bad = problems.append

        if self.superblock() != EXPECTED_SUPERBLOCK:
            bad("superblock fields differ from the specification")
        if any(self.block(0)[SUPERBLOCK.size:]):
            bad("superblock block has non-zero bytes after the 36-byte structure")

        entries = self.entries()
        by_slot = dict(entries)
        for slot, want in ((0, "."), (1, "..")):
            entry = by_slot.get(slot)
            if (entry is None or entry_name(entry) != want
                    or entry.inode_no != ROOT_INODE or entry.type != TYPE_DIR):
                bad(f'directory slot {slot} is not a proper "{want}" entry')

        root = self.inode(ROOT_INODE)
        if root.type != TYPE_DIR or root.links != 2:
            bad("root inode must be a directory with 2 links")
        if root.direct != (ROOT_DATA_BLOCK, 0, 0):
            bad(f"root inode direct pointers are {root.direct}, expected (4, 0, 0)")
        if root.size != DIRENT.size * len(entries):
            bad(f"root size is {root.size}, expected {DIRENT.size * len(entries)} for {len(entries)} entries")
        if any(root.reserved):
            bad("root inode reserved area is not zero")

        for slot in range(DIRENTS):
            if slot not in by_slot:
                off = self._dirent_offset(slot)
                if any(self.data[off:off + DIRENT.size]):
                    bad(f"unused directory slot {slot} contains stale bytes")

        used_inodes = {ROOT_INODE}
        used_blocks = {ROOT_DATA_BLOCK}
        names = set()
        for slot, entry in entries:
            if slot < 2:
                continue
            name = entry_name(entry)
            if b"\0" not in entry.raw:
                bad(f"slot {slot}: name is not NUL-terminated")
            if not name:
                bad(f"slot {slot}: empty name")
            if name in names:
                bad(f'duplicate name "{name}"')
            names.add(name)
            if entry.type != TYPE_FILE:
                bad(f'"{name}": directory entry type is {entry.type}, expected {TYPE_FILE}')
            if not 2 <= entry.inode_no <= TOTAL_INODES:
                bad(f'"{name}": inode number {entry.inode_no} is out of range')
                continue
            if entry.inode_no in used_inodes:
                bad(f'"{name}": inode {entry.inode_no} is referenced more than once')
                continue
            used_inodes.add(entry.inode_no)
            self._check_file_inode(entry.inode_no, name, used_blocks, bad)

        for number in range(1, TOTAL_INODES + 1):
            if number not in used_inodes and any(self.inode_bytes(number)):
                bad(f"unused inode {number} contains stale bytes")

        for label, block, count, expected in (
                ("inode", INODE_BITMAP_BLOCK, BLOCK_SIZE * 8, {n - 1 for n in used_inodes}),
                ("data", DATA_BITMAP_BLOCK, BLOCK_SIZE * 8, {b - DATA_REGION_BLOCK for b in used_blocks})):
            marked = set(self.marked_bits(block, count))
            if expected - marked:
                bad(f"{label} bitmap: bits {sorted(expected - marked)} are in use but not marked")
            if marked - expected:
                bad(f"{label} bitmap: bits {sorted(marked - expected)} are marked but nothing uses them")

        for number in range(DATA_REGION_BLOCK, TOTAL_BLOCKS):
            if number not in used_blocks and any(self.block(number)):
                bad(f"free data block {number} contains data")

        return problems

    def _check_file_inode(self, number, name, used_blocks, bad):
        inode = self.inode(number)
        tag = f'"{name}" (inode {number})'
        if inode.type != TYPE_FILE or inode.links != 1:
            bad(f"{tag}: must be a regular file with 1 link")
        if inode.size > MAX_FILE_SIZE:
            bad(f"{tag}: size {inode.size} exceeds {MAX_FILE_SIZE}")
        if any(inode.reserved):
            bad(f"{tag}: reserved area is not zero")

        needed = -(-inode.size // BLOCK_SIZE)
        pointers = [b for b in inode.direct if b]
        if inode.direct != tuple(pointers + [0] * (MAX_DIRECT - len(pointers))):
            bad(f"{tag}: direct pointers are not packed at the front: {inode.direct}")
        if len(pointers) != needed:
            bad(f"{tag}: size {inode.size} needs {needed} blocks but {len(pointers)} are assigned")

        for block in pointers:
            if not DATA_REGION_BLOCK < block < TOTAL_BLOCKS:
                bad(f"{tag}: block {block} is outside the data region")
            elif block in used_blocks:
                bad(f"{tag}: block {block} is already owned by another file")
            used_blocks.add(block)

        if needed and len(pointers) == needed:
            tail_start = inode.size - (needed - 1) * BLOCK_SIZE
            if any(self.block(pointers[-1])[tail_start:]):
                bad(f"{tag}: unused tail of the last block is not zero")


def dump(image):
    sb = image.superblock()
    print(f"SimpleFS image: {len(image.data)} bytes")
    print(f"  magic 0x{sb[0]:08X}, block size {sb[1]}, {sb[2]} blocks, {sb[3]} inodes")
    print(f"  inode bitmap: block {sb[4]}, data bitmap: block {sb[5]}, "
          f"inode table: block {sb[6]}, data region: blocks {sb[7]}-{sb[2] - 1}")

    inodes = image.marked_bits(INODE_BITMAP_BLOCK, TOTAL_INODES)
    blocks = image.marked_bits(DATA_BITMAP_BLOCK, DATA_BLOCKS)
    print(f"Inode bitmap : {image.block(INODE_BITMAP_BLOCK)[:4].hex(' ')}   inodes in use: "
          f"{ranges(i + 1 for i in inodes)}")
    print(f"Data bitmap  : {image.block(DATA_BITMAP_BLOCK)[:8].hex(' ')}   blocks in use: "
          f"{ranges(b + DATA_REGION_BLOCK for b in blocks)}")

    root = image.inode(ROOT_INODE)
    print(f"Root directory (inode {ROOT_INODE}, {root.size} bytes)")
    print(f"  {'slot':>4}  {'inode':>5}  {'type':<4}  {'size':>6}  {'blocks':<8}  name")
    for slot, entry in image.entries():
        if 1 <= entry.inode_no <= TOTAL_INODES:
            inode = image.inode(entry.inode_no)
            size, where = str(inode.size), ", ".join(str(b) for b in inode.direct if b) or "-"
        else:
            size, where = "?", "?"
        kind = "dir" if entry.type == TYPE_DIR else "file" if entry.type == TYPE_FILE else "?"
        print(f"  {slot:>4}  {entry.inode_no:>5}  {kind:<4}  {size:>6}  {where:<8}  {entry_name(entry)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and verify a SimpleFS image.")
    parser.add_argument("image")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="only run the consistency check")
    mode.add_argument("--cat", metavar="NAME", help="write the named file to stdout")
    args = parser.parse_args(argv)

    try:
        image = Image.load(args.image)
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.cat:
        try:
            sys.stdout.buffer.write(image.read_file(args.cat))
        except KeyError:
            print(f"error: {args.cat}: no such file in the image", file=sys.stderr)
            return 1
        return 0

    problems = image.check()
    if not args.check and len(image.data) == IMAGE_SIZE:
        dump(image)
    if problems:
        print("Consistency check: FAILED")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Consistency check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
