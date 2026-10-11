#ifndef SIMPLEFS_H
#define SIMPLEFS_H

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define BLOCK_SIZE 4096
#define TOTAL_BLOCKS 64
#define TOTAL_INODES 32
#define IMAGE_SIZE (TOTAL_BLOCKS * BLOCK_SIZE)

#define SUPERBLOCK_BLOCK 0
#define INODE_BITMAP_BLOCK 1
#define DATA_BITMAP_BLOCK 2
#define INODE_TABLE_BLOCK 3
#define DATA_REGION_BLOCK 4
#define DATA_BLOCKS 60

#define ROOT_INODE 1
#define ROOT_DATA_BLOCK 4
#define MAGIC_NUMBER 0x53465331

#define TYPE_FILE 1
#define TYPE_DIRECTORY 2

#define MAX_DIRECT_BLOCKS 3
#define MAX_FILE_SIZE (MAX_DIRECT_BLOCKS * BLOCK_SIZE)
#define DIRENT_SIZE 64
#define DIRENTS_PER_BLOCK (BLOCK_SIZE / DIRENT_SIZE)
#define MAX_NAME_LENGTH 58

/* Three numbering schemes are in play; these keep the conversions in one place. */
#define INODE_BIT(inode_no) ((inode_no) - 1)
#define DATA_BIT(block_no)  ((block_no) - DATA_REGION_BLOCK)

typedef struct {
    uint32_t magic, block_size, total_blocks, inode_count;
    uint32_t inode_bitmap_block, data_bitmap_block, inode_table_block, data_region_block;
    uint32_t root_inode;
} superblock_t;

typedef struct {
    uint16_t type, links;
    uint32_t size;
    uint32_t direct[3];
    uint8_t reserved[108];
} inode_t;

typedef struct {
    uint32_t inode_no;
    uint8_t type;
    char name[59];
} dirent_t;

/* The structs are written to disk as-is, so any padding would break the format. */
_Static_assert(sizeof(superblock_t) == 36, "superblock_t must be 36 bytes");
_Static_assert(sizeof(inode_t) == 128, "inode_t must be 128 bytes");
_Static_assert(sizeof(dirent_t) == DIRENT_SIZE, "dirent_t must be 64 bytes");
_Static_assert(TOTAL_INODES * sizeof(inode_t) == BLOCK_SIZE, "inode table must fill one block");

/* bitmaps */
void set_bit(unsigned char *bitmap, int index);
int is_bit_set(const unsigned char *bitmap, int index);
int find_free_inode(const unsigned char *bitmap);
int find_free_data_block(const unsigned char *bitmap);

/* byte offsets inside the image */
long block_offset(int block);
long inode_offset(int inode_number);
long dirent_offset(int index);

/* checked I/O: 0 on success, -1 on any seek/read/write failure */
int read_at(FILE *fp, long offset, void *buf, size_t len);
int write_at(FILE *fp, long offset, const void *buf, size_t len);

/* superblock */
void superblock_fill(superblock_t *sb);
int superblock_valid(const superblock_t *sb);

#endif
