"""End-to-end tests for simplefs_builder and simplefs_adder.

Every test drives the compiled programs the way a user would and then inspects
the resulting image with tools/simplefs_inspect.py, which re-implements the
on-disk format independently of the C code.

Run with `make test` (builds first) or `python3 -m unittest discover -s tests -v`.
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = Path(os.environ.get("SIMPLEFS_BIN", ROOT / "build"))
sys.path.insert(0, str(ROOT / "tools"))

from simplefs_inspect import (  # noqa: E402
    BLOCK_SIZE, DATA_BITMAP_BLOCK, DATA_BLOCKS, DIRENT, DIRENTS, IMAGE_SIZE,
    INODE_BITMAP_BLOCK, INODE_TABLE_BLOCK, MAX_FILE_SIZE, ROOT_DATA_BLOCK, Image,
)

# Image produced by the original course submission for: builder, test1.txt, test2.txt.
GOLDEN_SHA256 = "13a59d74e0e284f7151485b854f9fd9d0b7ea98de0320c9455c00cdd59f13029"


def pattern(size):
    """Non-repeating-looking bytes so a misplaced block can't hide behind zeros."""
    return bytes((i * 31 + 7) % 251 for i in range(size))


class SimpleFSTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)
        self.image_path = self.cwd / "disk.img"
        for sample in (ROOT / "samples").glob("*.txt"):
            shutil.copy(sample, self.cwd)

    def run_tool(self, tool, *args):
        return subprocess.run([str(BIN / tool), *map(str, args)], cwd=self.cwd,
                              capture_output=True, text=True)

    def build(self, name="disk.img"):
        result = self.run_tool("simplefs_builder", "--image", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def add(self, filename, image="disk.img"):
        return self.run_tool("simplefs_adder", "--input", image, "--file", filename)

    def add_ok(self, filename):
        result = self.add(filename)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{filename} added successfully to disk.img")
        self.assertEqual(self.image().check(), [])
        return result

    def write_file(self, name, content):
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def image(self):
        return Image.load(self.image_path)

    def raw(self):
        return self.image_path.read_bytes()

    def assert_rejected(self, result, message, unchanged=None, image="disk.img"):
        self.assertEqual(result.returncode, 1)
        self.assertIn(message, result.stderr)
        self.assertEqual(result.stdout, "")
        if unchanged is not None:
            self.assertEqual((self.cwd / image).read_bytes(), unchanged,
                             "a rejected operation must not modify the image")


class BuilderTests(SimpleFSTestCase):
    def test_image_has_exact_size(self):
        result = self.build()
        self.assertEqual(self.image_path.stat().st_size, IMAGE_SIZE)
        self.assertEqual(result.stdout.strip(), "SimpleFS image created successfully: disk.img")

    def test_superblock_matches_spec(self):
        self.build()
        raw = self.raw()
        self.assertEqual(struct.unpack_from("<9I", raw, 0), (0x53465331, 4096, 64, 32, 1, 2, 3, 4, 1))
        self.assertEqual(raw[36:BLOCK_SIZE], bytes(BLOCK_SIZE - 36))

    def test_bitmaps_start_with_only_the_root_bit(self):
        self.build()
        image = self.image()
        for block in (INODE_BITMAP_BLOCK, DATA_BITMAP_BLOCK):
            self.assertEqual(image.block(block), b"\x01" + bytes(BLOCK_SIZE - 1))

    def test_root_inode_and_dot_entries(self):
        self.build()
        image = self.image()
        root = image.inode(1)
        self.assertEqual((root.type, root.links, root.size, root.direct), (2, 2, 128, (4, 0, 0)))
        self.assertEqual([(slot, e.inode_no, e.type, e.raw.rstrip(b"\0")) for slot, e in image.entries()],
                         [(0, 1, 2, b"."), (1, 1, 2, b"..")])

    def test_fresh_image_is_consistent(self):
        self.build()
        self.assertEqual(self.image().check(), [])

    def test_output_is_deterministic(self):
        self.build("a.img")
        self.build("b.img")
        self.assertEqual((self.cwd / "a.img").read_bytes(), (self.cwd / "b.img").read_bytes())

    def test_overwrites_an_existing_file(self):
        self.write_file("disk.img", b"junk" * 300000)
        self.build()
        self.assertEqual(self.image_path.stat().st_size, IMAGE_SIZE)
        self.assertEqual(self.image().check(), [])

    def test_rejects_bad_arguments(self):
        for args in ((), ("--image",), ("--file", "disk.img"), ("--image", "a.img", "extra")):
            with self.subTest(args=args):
                result = self.run_tool("simplefs_builder", *args)
                self.assertEqual(result.returncode, 1)
                self.assertNotEqual(result.stderr, "")
                self.assertFalse((self.cwd / "a.img").exists())

    def test_reports_unwritable_destination(self):
        result = self.run_tool("simplefs_builder", "--image", "no_such_dir/disk.img")
        self.assert_rejected(result, "Error: could not create image file.")


class AddFileTests(SimpleFSTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_small_file(self):
        self.add_ok("test1.txt")
        raw, image = self.raw(), self.image()
        self.assertEqual(raw[INODE_BITMAP_BLOCK * BLOCK_SIZE], 0x03)
        self.assertEqual(raw[DATA_BITMAP_BLOCK * BLOCK_SIZE], 0x03)
        inode = image.inode(2)
        self.assertEqual((inode.type, inode.links, inode.size, inode.direct), (1, 1, 22, (5, 0, 0)))
        self.assertTrue(raw[5 * BLOCK_SIZE:].startswith(b"Operating Systems Lab\n"))
        entry = image.dirent(2)
        self.assertEqual((entry.inode_no, entry.type, entry.raw.rstrip(b"\0")), (2, 1, b"test1.txt"))
        self.assertEqual(image.inode(1).size, 192)

    def test_two_block_file(self):
        data = pattern(5000)
        self.write_file("five.bin", data)
        self.add_ok("five.bin")
        inode = self.image().inode(2)
        self.assertEqual((inode.size, inode.direct), (5000, (5, 6, 0)))
        self.assertEqual(self.raw()[DATA_BITMAP_BLOCK * BLOCK_SIZE], 0x07)
        self.assertEqual(self.image().read_file("five.bin"), data)

    def test_three_block_file(self):
        data = pattern(MAX_FILE_SIZE)
        self.write_file("max.bin", data)
        self.add_ok("max.bin")
        inode = self.image().inode(2)
        self.assertEqual((inode.size, inode.direct), (12288, (5, 6, 7)))
        self.assertEqual(self.raw()[DATA_BITMAP_BLOCK * BLOCK_SIZE], 0x0F)
        self.assertEqual(self.image().read_file("max.bin"), data)

    def test_block_count_at_every_size_boundary(self):
        cases = [(0, 0), (1, 1), (4095, 1), (4096, 1), (4097, 2), (8191, 2),
                 (8192, 2), (8193, 3), (12287, 3), (12288, 3)]
        for size, blocks in cases:
            with self.subTest(size=size):
                self.build()
                data = pattern(size)
                self.write_file("f.bin", data)
                self.add_ok("f.bin")
                image = self.image()
                inode = image.inode(2)
                self.assertEqual(inode.size, size)
                self.assertEqual(sum(1 for b in inode.direct if b), blocks)
                self.assertEqual(len(image.marked_bits(DATA_BITMAP_BLOCK, DATA_BLOCKS)), 1 + blocks)
                self.assertEqual(image.read_file("f.bin"), data)

    def test_zero_byte_file_takes_an_inode_but_no_blocks(self):
        self.write_file("empty", b"")
        self.add_ok("empty")
        image = self.image()
        self.assertEqual(image.inode(2).direct, (0, 0, 0))
        self.assertEqual(image.marked_bits(DATA_BITMAP_BLOCK, DATA_BLOCKS), [0])
        self.assertEqual(image.read_file("empty"), b"")

    def test_multiple_files_use_first_fit(self):
        expected_root_sizes = (192, 256, 320)
        for number, name in enumerate(("test1.txt", "test2.txt", "test3.txt")):
            self.add_ok(name)
            image = self.image()
            self.assertEqual(image.inode(1).size, expected_root_sizes[number])
            self.assertEqual(image.lookup(name), number + 2)
            self.assertEqual(image.inode(number + 2).direct, (number + 5, 0, 0))
        image = self.image()
        for name in ("test1.txt", "test2.txt", "test3.txt"):
            self.assertEqual(image.read_file(name), (self.cwd / name).read_bytes())

    def test_blocks_are_packed_across_files_of_different_sizes(self):
        for name, size in (("a", 5000), ("b", 100), ("c", 9000)):
            self.write_file(name, pattern(size))
            self.add_ok(name)
        image = self.image()
        self.assertEqual(image.inode(2).direct, (5, 6, 0))
        self.assertEqual(image.inode(3).direct, (7, 0, 0))
        self.assertEqual(image.inode(4).direct, (8, 9, 10))

    def test_source_file_is_left_untouched(self):
        before = (self.cwd / "test2.txt").read_bytes()
        self.add_ok("test2.txt")
        self.assertEqual((self.cwd / "test2.txt").read_bytes(), before)

    def test_path_is_reduced_to_the_file_name(self):
        data = pattern(300)
        self.write_file("sub/dir/data.txt", data)
        result = self.add("sub/dir/data.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        image = self.image()
        self.assertEqual(image.read_file("data.txt"), data)
        with self.assertRaises(KeyError):
            image.lookup("sub/dir/data.txt")
        self.assertEqual(image.check(), [])

    def test_name_of_exactly_58_characters_is_accepted(self):
        name = "n" * 58
        self.write_file(name, b"x")
        self.add_ok(name)
        self.assertEqual(self.image().read_file(name), b"x")

    def test_inode_search_never_hands_out_the_root_inode(self):
        raw = bytearray(self.raw())
        raw[INODE_BITMAP_BLOCK * BLOCK_SIZE] = 0x00  # pretend inode 1 is free
        self.write_file("tampered.img", bytes(raw))
        result = self.add("test1.txt", image="tampered.img")
        self.assertEqual(result.returncode, 0, result.stderr)
        image = Image.load(self.cwd / "tampered.img")
        self.assertEqual(image.inode(1).type, 2)
        self.assertEqual(image.lookup("test1.txt"), 2)

    def test_directory_search_skips_the_two_reserved_slots(self):
        raw = bytearray(self.raw())
        start = ROOT_DATA_BLOCK * BLOCK_SIZE
        raw[start:start + 2 * DIRENT.size] = bytes(2 * DIRENT.size)  # blank "." and ".."
        self.write_file("tampered.img", bytes(raw))
        result = self.add("test1.txt", image="tampered.img")
        self.assertEqual(result.returncode, 0, result.stderr)
        image = Image.load(self.cwd / "tampered.img")
        self.assertEqual([slot for slot, _ in image.entries()], [2])

    def test_matches_image_from_the_original_submission(self):
        self.add_ok("test1.txt")
        self.add_ok("test2.txt")
        self.assertEqual(hashlib.sha256(self.raw()).hexdigest(), GOLDEN_SHA256)


class ErrorTests(SimpleFSTestCase):
    def setUp(self):
        super().setUp()
        self.build()
        self.add_ok("test1.txt")

    def test_duplicate_name(self):
        before = self.raw()
        self.assert_rejected(self.add("test1.txt"), "Error: file already exists in SimpleFS.", before)

    def test_missing_source_file(self):
        before = self.raw()
        self.assert_rejected(self.add("abc123.txt"), "Error: source file not found.", before)

    def test_missing_image(self):
        result = self.add("test1.txt", image="nothing.img")
        self.assert_rejected(result, "Error: file-system image not found.")
        self.assertFalse((self.cwd / "nothing.img").exists())

    def test_image_with_wrong_magic(self):
        self.write_file("bad.img", bytes(IMAGE_SIZE))
        result = self.add("test1.txt", image="bad.img")
        self.assert_rejected(result, "Error: invalid SimpleFS image.", bytes(IMAGE_SIZE), "bad.img")

    def test_image_with_wrong_geometry(self):
        raw = bytearray(self.raw())
        struct.pack_into("<I", raw, 4, 512)  # block_size field
        self.write_file("bad.img", bytes(raw))
        self.assert_rejected(self.add("test2.txt", image="bad.img"),
                             "Error: invalid SimpleFS image.", bytes(raw), "bad.img")

    def test_truncated_image_is_not_silently_grown(self):
        truncated = self.raw()[:100000]
        self.write_file("short.img", truncated)
        self.assert_rejected(self.add("test2.txt", image="short.img"),
                             "Error: invalid SimpleFS image.", truncated, "short.img")

    def test_empty_image_file(self):
        self.write_file("zero.img", b"")
        self.assert_rejected(self.add("test2.txt", image="zero.img"),
                             "Error: could not read superblock.", b"", "zero.img")

    def test_file_larger_than_12288_bytes(self):
        self.write_file("too_big.dat", pattern(MAX_FILE_SIZE + 1))
        before = self.raw()
        self.assert_rejected(self.add("too_big.dat"), "Error: file is too large for SimpleFS.", before)

    def test_name_longer_than_58_characters(self):
        name = "n" * 59
        self.write_file(name, b"x")
        before = self.raw()
        self.assert_rejected(self.add(name), "Error: file name is longer than 58 characters.", before)

    def test_bad_command_line(self):
        cases = [((), "Usage:"),
                 (("--input", "disk.img"), "Usage:"),
                 (("--image", "disk.img", "--file", "test1.txt"), "invalid command-line arguments"),
                 (("--input", "disk.img", "--name", "test1.txt"), "invalid command-line arguments")]
        for args, message in cases:
            with self.subTest(args=args):
                result = self.run_tool("simplefs_adder", *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)


class ResourceLimitTests(SimpleFSTestCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_no_free_inode_after_31_files(self):
        for i in range(31):
            self.write_file(f"f{i:02}", b"")
            self.add_ok(f"f{i:02}")
        image = self.image()
        self.assertEqual(image.marked_bits(INODE_BITMAP_BLOCK, 32), list(range(32)))
        self.assertEqual(image.inode(1).size, 64 * 33)

        self.write_file("one_too_many", b"x")
        before = self.raw()
        self.assert_rejected(self.add("one_too_many"), "Error: no free inode available.", before)

    def test_out_of_data_blocks_leaves_the_image_untouched(self):
        for i in range(19):  # 19 * 3 blocks + the root block = 58 of 60
            self.write_file(f"big{i}", pattern(MAX_FILE_SIZE))
            self.add_ok(f"big{i}")

        self.write_file("needs_three", pattern(MAX_FILE_SIZE))
        before = self.raw()
        self.assert_rejected(self.add("needs_three"),
                             "Error: not enough free data blocks available.", before)

        # the failed attempt must not have leaked its partial allocation
        self.write_file("needs_two", pattern(2 * BLOCK_SIZE))
        self.add_ok("needs_two")
        self.assertEqual(self.image().inode(21).direct, (62, 63, 0))

        self.write_file("needs_one", b"x")
        before = self.raw()
        self.assert_rejected(self.add("needs_one"),
                             "Error: not enough free data blocks available.", before)
        self.assertEqual(self.image().marked_bits(DATA_BITMAP_BLOCK, DATA_BLOCKS), list(range(DATA_BLOCKS)))

    def test_full_root_directory(self):
        raw = bytearray(self.raw())
        for slot in range(2, DIRENTS):
            offset = ROOT_DATA_BLOCK * BLOCK_SIZE + slot * DIRENT.size
            DIRENT.pack_into(raw, offset, 2, 1, f"filler{slot}".encode())
        self.write_file("full.img", bytes(raw))
        self.assert_rejected(self.add("test2.txt", image="full.img"),
                             "Error: root directory is full.", bytes(raw), "full.img")


class CheckerTests(SimpleFSTestCase):
    """The consistency checker is only worth trusting if it fails on broken images."""

    def setUp(self):
        super().setUp()
        self.build()
        self.write_file("five.bin", pattern(5000))
        for name in ("test1.txt", "five.bin", "test3.txt"):
            self.add_ok(name)
        self.good = bytearray(self.raw())

    def problems_after(self, mutate):
        broken = bytearray(self.good)
        mutate(broken)
        return Image(broken).check()

    def test_good_image_has_no_problems(self):
        self.assertEqual(Image(self.good).check(), [])

    def test_detects_corruption(self):
        inode_offset = INODE_TABLE_BLOCK * BLOCK_SIZE
        mutations = {
            "inode bit cleared": lambda b: b.__setitem__(INODE_BITMAP_BLOCK * BLOCK_SIZE, 0x01),
            "stray data bit": lambda b: b.__setitem__(DATA_BITMAP_BLOCK * BLOCK_SIZE + 1, 0x01),
            "data bit cleared": lambda b: b.__setitem__(DATA_BITMAP_BLOCK * BLOCK_SIZE, 0x03),
            "wrong file size": lambda b: struct.pack_into("<I", b, inode_offset + 128 + 4, 9999),
            "pointer outside data region": lambda b: struct.pack_into("<I", b, inode_offset + 128 + 8, 2),
            "two files share a block": lambda b: struct.pack_into("<I", b, inode_offset + 2 * 128 + 8, 5),
            "root size not updated": lambda b: struct.pack_into("<I", b, inode_offset + 4, 128),
            "duplicate name": lambda b: b.__setitem__(
                slice(ROOT_DATA_BLOCK * BLOCK_SIZE + 3 * 64 + 5, ROOT_DATA_BLOCK * BLOCK_SIZE + 3 * 64 + 14),
                b"test1.txt"),
            "dirent inode out of range": lambda b: struct.pack_into(
                "<I", b, ROOT_DATA_BLOCK * BLOCK_SIZE + 2 * 64, 40),
            "garbage in a free block": lambda b: b.__setitem__(30 * BLOCK_SIZE, 0xFF),
            "garbage after end of file": lambda b: b.__setitem__(5 * BLOCK_SIZE + 100, 0x41),
            "stale bytes in unused inode": lambda b: b.__setitem__(inode_offset + 20 * 128, 0x01),
            "bad superblock": lambda b: struct.pack_into("<I", b, 0, 0xDEADBEEF),
            "dot entry missing": lambda b: b.__setitem__(slice(ROOT_DATA_BLOCK * BLOCK_SIZE,
                                                                 ROOT_DATA_BLOCK * BLOCK_SIZE + 64), bytes(64)),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertNotEqual(self.problems_after(mutate), [], f"checker missed: {label}")

    def test_command_line(self):
        script = str(ROOT / "tools" / "simplefs_inspect.py")

        def run(*args):
            return subprocess.run([sys.executable, script, *args], cwd=self.cwd, capture_output=True)

        result = run("disk.img", "--check")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b"Consistency check: OK"))
        self.assertEqual(run("disk.img", "--cat", "test1.txt").stdout, (self.cwd / "test1.txt").read_bytes())
        self.assertEqual(run("disk.img", "--cat", "nope").returncode, 1)
        self.assertEqual(run("missing.img").returncode, 2)
        self.assertIn(b"test3.txt", run("disk.img").stdout)

        self.write_file("broken.img", bytes(IMAGE_SIZE))
        result = run("broken.img", "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"FAILED", result.stdout)


if __name__ == "__main__":
    unittest.main()
