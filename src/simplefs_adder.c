#include "simplefs.h"

#define STR_(x) #x
#define STR(x) STR_(x)

static const char *check_image(FILE *image)
{
    superblock_t sb;

    if (read_at(image, block_offset(SUPERBLOCK_BLOCK), &sb, sizeof sb) != 0) {
        return "could not read superblock.";
    }
    if (!superblock_valid(&sb)) {
        return "invalid SimpleFS image.";
    }
    /* a truncated image would otherwise be silently grown by the first write */
    if (fseek(image, 0, SEEK_END) != 0 || ftell(image) != IMAGE_SIZE) {
        return "invalid SimpleFS image.";
    }
    return NULL;
}

static int find_dirent(const dirent_t *dir, const char *name)
{
    for (int i = 0; i < DIRENTS_PER_BLOCK; i++) {
        if (dir[i].inode_no != 0 && strncmp(dir[i].name, name, sizeof dir[i].name) == 0) {
            return i;
        }
    }
    return -1;
}

/* Slots 0 and 1 are "." and "..", so user files start at slot 2. */
static int find_free_dirent(const dirent_t *dir)
{
    for (int i = 2; i < DIRENTS_PER_BLOCK; i++) {
        if (dir[i].inode_no == 0) {
            return i;
        }
    }
    return -1;
}

/* Returns NULL on success, otherwise a message describing what went wrong. */
static const char *add_file(FILE *image, FILE *source, const char *path)
{
    const char *slash = strrchr(path, '/');
    const char *name = slash ? slash + 1 : path;
    unsigned char inode_bitmap[BLOCK_SIZE], data_bitmap[BLOCK_SIZE];
    dirent_t root_dir[DIRENTS_PER_BLOCK];
    dirent_t entry = {0};
    inode_t inode = {0}, root_inode;
    int blocks[MAX_DIRECT_BLOCKS] = {0};
    int required_blocks, free_inode, slot;
    long file_size, remaining;

    if (name[0] == '\0') {
        return "invalid file name.";
    }
    if (strlen(name) > MAX_NAME_LENGTH) {
        return "file name is longer than " STR(MAX_NAME_LENGTH) " characters.";
    }

    if (fseek(source, 0, SEEK_END) != 0 || (file_size = ftell(source)) < 0 ||
        fseek(source, 0, SEEK_SET) != 0) {
        return "could not determine source file size.";
    }
    if (file_size > MAX_FILE_SIZE) {
        return "file is too large for SimpleFS.";
    }
    required_blocks = (int)((file_size + BLOCK_SIZE - 1) / BLOCK_SIZE);

    if (read_at(image, block_offset(ROOT_DATA_BLOCK), root_dir, sizeof root_dir) != 0) {
        return "could not read the root directory.";
    }
    if (find_dirent(root_dir, name) >= 0) {
        return "file already exists in SimpleFS.";
    }

    if (read_at(image, block_offset(INODE_BITMAP_BLOCK), inode_bitmap, BLOCK_SIZE) != 0) {
        return "could not read the inode bitmap.";
    }
    free_inode = find_free_inode(inode_bitmap);
    if (free_inode < 0) {
        return "no free inode available.";
    }

    if (read_at(image, block_offset(DATA_BITMAP_BLOCK), data_bitmap, BLOCK_SIZE) != 0) {
        return "could not read the data bitmap.";
    }
    /* Allocation only touches the in-memory bitmap, so running out of blocks
       part-way leaves the image exactly as it was. */
    for (int i = 0; i < required_blocks; i++) {
        int block = find_free_data_block(data_bitmap);
        if (block < 0) {
            return "not enough free data blocks available.";
        }
        set_bit(data_bitmap, DATA_BIT(block));
        blocks[i] = block;
    }

    slot = find_free_dirent(root_dir);
    if (slot < 0) {
        return "root directory is full.";
    }

    /* Write order: data, inode, bitmaps, then the directory entry. A failure
       part-way leaves the file unreachable by name rather than half-visible. */
    remaining = file_size;
    for (int i = 0; i < required_blocks; i++) {
        unsigned char buffer[BLOCK_SIZE] = {0};
        size_t chunk = remaining < BLOCK_SIZE ? (size_t)remaining : BLOCK_SIZE;

        if (fread(buffer, 1, chunk, source) != chunk) {
            return "could not read the source file.";
        }
        if (write_at(image, block_offset(blocks[i]), buffer, BLOCK_SIZE) != 0) {
            return "could not write file data to the image.";
        }
        remaining -= (long)chunk;
    }

    inode.type = TYPE_FILE;
    inode.links = 1;
    inode.size = (uint32_t)file_size;   /* real size, not the allocated size */
    for (int i = 0; i < required_blocks; i++) {
        inode.direct[i] = (uint32_t)blocks[i];
    }
    if (write_at(image, inode_offset(free_inode), &inode, sizeof inode) != 0) {
        return "could not write the new inode.";
    }

    set_bit(inode_bitmap, INODE_BIT(free_inode));
    if (write_at(image, block_offset(INODE_BITMAP_BLOCK), inode_bitmap, BLOCK_SIZE) != 0 ||
        write_at(image, block_offset(DATA_BITMAP_BLOCK), data_bitmap, BLOCK_SIZE) != 0) {
        return "could not write the bitmaps.";
    }

    entry.inode_no = (uint32_t)free_inode;
    entry.type = TYPE_FILE;
    memcpy(entry.name, name, strlen(name));   /* length checked above; the rest is already zero */
    if (write_at(image, dirent_offset(slot), &entry, sizeof entry) != 0) {
        return "could not write the directory entry.";
    }

    if (read_at(image, inode_offset(ROOT_INODE), &root_inode, sizeof root_inode) != 0) {
        return "could not read the root inode.";
    }
    root_inode.size += sizeof(dirent_t);
    if (write_at(image, inode_offset(ROOT_INODE), &root_inode, sizeof root_inode) != 0) {
        return "could not update the root inode.";
    }

    return NULL;
}

int main(int argc, char *argv[])
{
    const char *image_name, *source_name, *error;
    FILE *image, *source = NULL;

    if (argc != 5) {
        fprintf(stderr, "Usage: %s --input <image> --file <file>\n", argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "--input") != 0 || strcmp(argv[3], "--file") != 0) {
        fprintf(stderr, "Error: invalid command-line arguments.\n");
        return 1;
    }
    image_name = argv[2];
    source_name = argv[4];

    image = fopen(image_name, "rb+");
    if (!image) {
        fprintf(stderr, "Error: file-system image not found.\n");
        return 1;
    }

    error = check_image(image);
    if (!error) {
        source = fopen(source_name, "rb");
        if (!source) {
            error = "source file not found.";
        }
    }
    if (!error) {
        error = add_file(image, source, source_name);
    }

    if (source) {
        fclose(source);
    }
    if (fclose(image) != 0 && !error) {
        error = "could not finish writing the image.";
    }

    if (error) {
        fprintf(stderr, "Error: %s\n", error);
        return 1;
    }
    printf("%s added successfully to %s\n", source_name, image_name);
    return 0;
}
