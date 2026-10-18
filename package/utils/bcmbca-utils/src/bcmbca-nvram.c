// SPDX-License-Identifier: GPL-2.0-only
/* Read-only access to the Genexis XG6846B's Broadcom NVRAM v6. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#define NVRAM_SIZE 1024
#define NVRAM_PRIMARY 0x10580
#define NVRAM_MIRROR 0x20008
#define NVRAM_BOARD 260
#define NVRAM_MAC_COUNT 284
#define NVRAM_BASE_MAC 288
#define NVRAM_CRC 1020

static uint32_t get_le32(const unsigned char *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
	       (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int read_nvram(FILE *fp, long offset, unsigned char *data)
{
	uint32_t checksum;

	if (fseek(fp, offset, SEEK_SET) ||
	    fread(data, 1, NVRAM_SIZE, fp) != NVRAM_SIZE)
		return -1;
	checksum = get_le32(data + NVRAM_CRC);
	memset(data + NVRAM_CRC, 0, 4);
	if (((uint32_t)crc32(0, data, NVRAM_SIZE) ^ UINT32_MAX) != checksum ||
	    get_le32(data) != 6 || memcmp(data + NVRAM_BOARD, "XG6846B", 8) ||
	    !get_le32(data + NVRAM_MAC_COUNT) ||
	    get_le32(data + NVRAM_MAC_COUNT) > 64 ||
	    (data[NVRAM_BASE_MAC] & 1) ||
	    !memcmp(data + NVRAM_BASE_MAC, "\0\0\0\0\0\0", 6))
		return -1;
	return 0;
}

int main(int argc, char **argv)
{
	unsigned char data[NVRAM_SIZE], signature[8], *mac;
	unsigned long index;
	uint32_t suffix;
	char *end;
	FILE *fp;
	int ret;

	if (argc != 3) {
		fprintf(stderr, "Usage: %s loader-device mac-index\n", argv[0]);
		return 1;
	}
	index = strtoul(argv[2], &end, 10);
	if (!*argv[2] || *end || index >= 64)
		return 1;
	fp = fopen(argv[1], "rb");
	if (!fp) {
		perror(argv[1]);
		return 1;
	}
	ret = read_nvram(fp, NVRAM_PRIMARY, data);
	if (ret && !fseek(fp, NVRAM_MIRROR - 8, SEEK_SET) &&
	    fread(signature, 1, 8, fp) == 8 &&
	    !memcmp(signature, "nVrAmDaT", 8))
		ret = read_nvram(fp, NVRAM_MIRROR, data);
	fclose(fp);
	if (ret || index >= get_le32(data + NVRAM_MAC_COUNT)) {
		fprintf(stderr, "No valid XG6846B NVRAM MAC address for index %lu\n", index);
		return 1;
	}
	mac = data + NVRAM_BASE_MAC;
	/* The vendor allocator increments the low 24 bits, preserving the OUI. */
	suffix = (((uint32_t)mac[3] << 16 | mac[4] << 8 | mac[5]) + index) & 0xffffff;
	printf("%02x:%02x:%02x:%02x:%02x:%02x\n", mac[0], mac[1], mac[2],
	       (suffix >> 16) & 255, (suffix >> 8) & 255, suffix & 255);
	return 0;
}
