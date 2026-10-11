#include "simplefs.h"

void set_bit(unsigned char *bitmap, int index)
{
    bitmap[index / 8] |= (unsigned char)(1u << (index % 8));
}

int is_bit_set(const unsigned char *bitmap, int index)
{
    return (bitmap[index / 8] >> (index % 8)) & 1;
}

/* Returns an inode number. Bit 0 belongs to the root, so the search starts at 1. */
int find_free_inode(const unsigned char *bitmap)
{
    for (int index = 1; index < TOTAL_INODES; index++) {
        if (!is_bit_set(bitmap, index)) {
            return index + 1;
        }
    }
    return -1;
}

/* Returns an absolute block number, not a bitmap index. */
int find_free_data_block(const unsigned char *bitmap)
{
    for (int index = 0; index < DATA_BLOCKS; index++) {
        if (!is_bit_set(bitmap, index)) {
            return DATA_REGION_BLOCK + index;
        }
    }
    return -1;
}

long block_offset(int block)
{
    return (long)block * BLOCK_SIZE;
}

long inode_offset(int inode_number)
{
    return block_offset(INODE_TABLE_BLOCK) + (long)(inode_number - 1) * (long)sizeof(inode_t);
}

long dirent_offset(int index)
{
    return block_offset(ROOT_DATA_BLOCK) + (long)index * (long)sizeof(dirent_t);
}

int read_at(FILE *fp, long offset, void *buf, size_t len)
{
    if (fseek(fp, offset, SEEK_SET) != 0) {
        return -1;
    }
    return fread(buf, len, 1, fp) == 1 ? 0 : -1;
}

int write_at(FILE *fp, long offset, const void *buf, size_t len)
{
    if (fseek(fp, offset, SEEK_SET) != 0) {
        return -1;
    }
    return fwrite(buf, len, 1, fp) == 1 ? 0 : -1;
}

void superblock_fill(superblock_t *sb)
{
    memset(sb, 0, sizeof *sb);
    sb->magic = MAGIC_NUMBER;
    sb->block_size = BLOCK_SIZE;
    sb->total_blocks = TOTAL_BLOCKS;
    sb->inode_count = TOTAL_INODES;
    sb->inode_bitmap_block = INODE_BITMAP_BLOCK;
    sb->data_bitmap_block = DATA_BITMAP_BLOCK;
    sb->inode_table_block = INODE_TABLE_BLOCK;
    sb->data_region_block = DATA_REGION_BLOCK;
    sb->root_inode = ROOT_INODE;
}

/* The struct has no padding (see the static asserts), so memcmp is safe. */
int superblock_valid(const superblock_t *sb)
{
    superblock_t expected;
    superblock_fill(&expected);
    return memcmp(sb, &expected, sizeof expected) == 0;
}
