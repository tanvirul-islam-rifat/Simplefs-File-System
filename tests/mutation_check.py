#!/usr/bin/env python3
"""Plant one deliberate bug at a time in a scratch copy of the sources and check
that the test suite notices.

    python3 tests/mutation_check.py

Exit status is 0 only if every planted bug was caught. The "control" mutation
changes nothing observable, so it is expected to survive; that proves the
harness itself reports survivors correctly.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, original text, replacement, description, expected to survive?)
MUTATIONS = [
    ("src/common.c", "return DATA_REGION_BLOCK + index;", "return index;",
     "data block returned as a bitmap index", False),
    ("src/common.c", "return index + 1;", "return index;",
     "inode returned as a bitmap index", False),
    ("src/common.c", "for (int index = 1; index < TOTAL_INODES", "for (int index = 0; index < TOTAL_INODES",
     "inode search includes the root's bit", False),
    ("src/simplefs_adder.c", "inode.size = (uint32_t)file_size;",
     "inode.size = (uint32_t)required_blocks * BLOCK_SIZE;",
     "allocated size stored instead of real size", False),
    ("src/simplefs_adder.c", "root_inode.size += sizeof(dirent_t);", "root_inode.size += 0;",
     "root directory size not updated", False),
    ("src/simplefs_adder.c", "if (strlen(name) > MAX_NAME_LENGTH)", "if (strlen(name) > MAX_NAME_LENGTH + 1)",
     "file name limit off by one", False),
    ("src/simplefs_adder.c", "if (find_dirent(root_dir, name) >= 0)", "if (0)",
     "duplicate-name check removed", False),
    ("src/simplefs_adder.c", "file_size > MAX_FILE_SIZE", "file_size > MAX_FILE_SIZE + 1",
     "file size limit off by one", False),
    ("src/simplefs_adder.c", "for (int i = 2; i < DIRENTS_PER_BLOCK; i++) {\n        if (dir[i].inode_no == 0)",
     "for (int i = 1; i < DIRENTS_PER_BLOCK; i++) {\n        if (dir[i].inode_no == 0)",
     "directory search may reuse the reserved slots", False),
    ("src/simplefs_adder.c", "unsigned char buffer[BLOCK_SIZE] = {0};",
     "unsigned char buffer[BLOCK_SIZE]; memset(buffer, 0xAA, BLOCK_SIZE);",
     "partial last block not zero-filled", False),
    ("src/simplefs_adder.c", "if (!superblock_valid(&sb)) {", "if (0) {",
     "superblock not validated", False),
    ("src/simplefs_adder.c", "ftell(image) != IMAGE_SIZE", "0",
     "image length not validated", False),
    ("src/simplefs_builder.c", "set_bit(inode_bitmap, INODE_BIT(ROOT_INODE));", "",
     "root inode bit not set", False),
    ("src/simplefs_builder.c", "set_bit(data_bitmap, DATA_BIT(ROOT_DATA_BLOCK));", "",
     "root data block bit not set", False),
    ("src/simplefs_builder.c", "root.links = 2;", "root.links = 1;",
     "root link count wrong", False),
    ("src/simplefs_builder.c", "dotdot.inode_no = ROOT_INODE;", "dotdot.inode_no = 0;",
     '".." entry points nowhere', False),
    ("src/simplefs_adder.c", "int required_blocks, free_inode, slot;", "int required_blocks, free_inode, slot; (void)0;",
     "control: no behavioural change", True),
]


def run_suite(work):
    build = subprocess.run(["make", "-s"], cwd=work, capture_output=True, text=True)
    if build.returncode != 0:
        return None, build.stderr.strip()
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                           cwd=work, capture_output=True, text=True)
    return tests.returncode == 0, ""


def main():
    unexpected = 0
    for path, old, new, label, expect_survive in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("build", "__pycache__", ".git"))
            text = (work / path).read_text()
            if old not in text:
                print(f"STALE     {label}: pattern not found in {path}")
                unexpected += 1
                continue
            (work / path).write_text(text.replace(old, new, 1))
            survived, error = run_suite(work)
            if survived is None:
                print(f"NO BUILD  {label}: {error[:100]}")
                unexpected += 1
                continue
            ok = survived == expect_survive
            unexpected += not ok
            print(f"{'ok      ' if ok else 'PROBLEM '}  {'survived' if survived else 'caught  '}  {label}")
    real = sum(1 for m in MUTATIONS if not m[4])
    print(f"\n{real} planted bugs, {unexpected} unexpected result(s)")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
