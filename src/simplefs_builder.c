#include "simplefs.h"

static int format_image(FILE *fp)
{
    unsigned char zero_block[BLOCK_SIZE] = {0};
    unsigned char inode_bitmap[BLOCK_SIZE] = {0};
    unsigned char data_bitmap[BLOCK_SIZE] = {0};
    superblock_t sb;
    inode_t root = {0};
    dirent_t dot = {0}, dotdot = {0};

    for (int block = 0; block < TOTAL_BLOCKS; block++) {
        if (write_at(fp, block_offset(block), zero_block, BLOCK_SIZE) != 0) {
            return -1;
        }
    }

    superblock_fill(&sb);
    if (write_at(fp, block_offset(SUPERBLOCK_BLOCK), &sb, sizeof sb) != 0) {
        return -1;
    }

    set_bit(inode_bitmap, INODE_BIT(ROOT_INODE));
    if (write_at(fp, block_offset(INODE_BITMAP_BLOCK), inode_bitmap, BLOCK_SIZE) != 0) {
        return -1;
    }

    set_bit(data_bitmap, DATA_BIT(ROOT_DATA_BLOCK));
    if (write_at(fp, block_offset(DATA_BITMAP_BLOCK), data_bitmap, BLOCK_SIZE) != 0) {
        return -1;
    }

    root.type = TYPE_DIRECTORY;
    root.links = 2;
    root.size = 2 * sizeof(dirent_t);
    root.direct[0] = ROOT_DATA_BLOCK;
    if (write_at(fp, inode_offset(ROOT_INODE), &root, sizeof root) != 0) {
        return -1;
    }

    /* the root has no parent, so ".." points back at itself */
    dot.inode_no = ROOT_INODE;
    dot.type = TYPE_DIRECTORY;
    strcpy(dot.name, ".");
    dotdot.inode_no = ROOT_INODE;
    dotdot.type = TYPE_DIRECTORY;
    strcpy(dotdot.name, "..");
    if (write_at(fp, dirent_offset(0), &dot, sizeof dot) != 0 ||
        write_at(fp, dirent_offset(1), &dotdot, sizeof dotdot) != 0) {
        return -1;
    }

    return 0;
}

int main(int argc, char *argv[])
{
    const char *image_name;
    FILE *fp;

    if (argc != 3) {
        fprintf(stderr, "Usage: %s --image <image_name>\n", argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "--image") != 0) {
        fprintf(stderr, "Error: expected --image option.\n");
        return 1;
    }
    image_name = argv[2];

    fp = fopen(image_name, "wb+");
    if (!fp) {
        fprintf(stderr, "Error: could not create image file.\n");
        return 1;
    }

    int failed = format_image(fp) != 0;
    if (fclose(fp) != 0) {
        failed = 1;
    }
    if (failed) {
        fprintf(stderr, "Error: could not initialize image.\n");
        remove(image_name);
        return 1;
    }

    printf("SimpleFS image created successfully: %s\n", image_name);
    return 0;
}
