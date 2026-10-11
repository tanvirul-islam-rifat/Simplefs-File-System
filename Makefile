CFLAGS ?= -O2 -g
ALL_CFLAGS = -std=c11 -Wall -Wextra -Wpedantic -Iinclude $(CFLAGS)

# make SANITIZE=1 rebuilds with AddressSanitizer + UBSan
ifeq ($(SANITIZE),1)
ALL_CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
LDFLAGS += -fsanitize=address,undefined
endif

BUILD := build
PROGS := $(BUILD)/simplefs_builder $(BUILD)/simplefs_adder

all: $(PROGS)

$(BUILD)/%: src/%.c src/common.c include/simplefs.h | $(BUILD)
	$(CC) $(ALL_CFLAGS) $< src/common.c -o $@ $(LDFLAGS)

$(BUILD):
	mkdir -p $@

test: all
	python3 -m unittest discover -s tests -v

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
