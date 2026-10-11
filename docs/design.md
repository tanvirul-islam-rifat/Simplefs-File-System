# SimpleFS design notes

## Goals and non-goals

SimpleFS is an educational file system stored in one 262,144-byte file. The goal is to make the core structures (superblock, inode, bitmap, directory entry, direct block pointers) concrete and inspectable. It is not mountable, concurrent, or crash-safe, and has no subdirectories, deletion, indirect blocks, permissions or timestamps.

## On-disk layout

| Block | Byte offset | Contents |
|---|---|---|
| 0 | 0 | Superblock (36-byte struct, rest of the block zero) |
| 1 | 4096 | Inode bitmap (bits 0–31 meaningful, rest zero) |
| 2 | 8192 | Data bitmap (bits 0–59 meaningful, rest zero) |
| 3 | 12288 | Inode table: 32 × 128-byte inodes |
| 4 | 16384 | Root directory: 64 × 64-byte entries |
| 5–63 | 20480 | File data |

### Structures (little-endian, no padding)

```
superblock_t  36 B   magic 0x53465331, block_size, total_blocks, inode_count,
                     inode_bitmap_block, data_bitmap_block, inode_table_block,
                     data_region_block, root_inode
inode_t      128 B   u16 type (1 file, 2 dir), u16 links, u32 size,
                     u32 direct[3] (absolute block numbers, 0 = unused), 108 B reserved
dirent_t      64 B   u32 inode_no (0 = unused slot), u8 type, char name[59]
```

`include/simplefs.h` asserts these sizes at compile time, because the structs are written to disk verbatim.

## Index spaces

| Quantity | Range | Maps to |
|---|---|---|
| Inode number | 1–32 | inode bitmap bit `n − 1`; table offset `3·4096 + (n − 1)·128` |
| Data bitmap bit | 0–59 | absolute block `4 + bit` |
| Absolute block | 4–63 | what `direct[]` stores; byte offset `block · 4096` |
| Directory slot | 0–63 | byte offset `4·4096 + slot·64`; slots 0 and 1 are reserved |

## Formatting (`simplefs_builder`)

Zero 64 blocks, then write: superblock; inode bitmap with bit 0 set; data bitmap with bit 0 set; root inode (type 2, links 2, size 128, `direct = {4, 0, 0}`); entries `.` and `..`, both pointing at inode 1. The output is deterministic.

## Adding a file (`simplefs_adder`)

1. Open the image read/write; read the superblock and compare **every field** with the expected values; require the image to be exactly 262,144 bytes.
2. Open the source file. Reduce its path to the base name. Reject an empty name or one over 58 characters; reject a file over 12,288 bytes.
3. Read the root directory block; reject a duplicate name (`.` and `..` count).
4. Read the inode bitmap; first-fit search over bits 1–31 for a free inode (bit 0 is the root).
5. Read the data bitmap; first-fit allocate `ceil(size / 4096)` blocks, marking them **in the in-memory copy only**. Running out part-way returns an error with nothing written.
6. Find the first free directory slot in 2–63.
7. Write, in order: data blocks (zero-padded), inode (real size, absolute block numbers), inode bitmap and data bitmap, directory entry, root inode with `size += 64`.

### Failure behaviour

Steps 1–6 only read, so every validation or allocation failure leaves the image byte-for-byte unchanged (tested). Step 7 is not atomic. The order is chosen so that a failure part-way leaves data or an inode that nothing refers to, rather than a name pointing at garbage. There is no recovery tool; re-running the builder is the fix.

The "root directory is full" error can never occur on a well-formed image, because the 31 free inodes run out before the 62 free directory slots do. It is exercised only with a tampered image.

## Testing strategy

- `tools/simplefs_inspect.py` parses images with Python's `struct`, independently of the C structs, and enforces the invariants of a consistent image (see `Image.check`).
- `tests/test_simplefs.py` drives the real binaries and checks outputs both against specific expected values from the specification and against the invariants.
- `tests/mutation_check.py` verifies that the suite fails when single-line bugs are planted in the C sources.
- A golden SHA-256 ties the output for the two sample files to the image produced by the original course submission.
