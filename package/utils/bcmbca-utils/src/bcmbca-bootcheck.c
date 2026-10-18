// SPDX-License-Identifier: GPL-2.0-only
/* Check the legacy CFE file records before touching the live UBI volumes. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#define MAX_BUNDLE (128 * 126976)

static uint32_t be32(const unsigned char *p)
{
	return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 |
	       (uint32_t)p[2] << 8 | p[3];
}

static uint32_t le32(const unsigned char *p)
{
	return (uint32_t)p[3] << 24 | (uint32_t)p[2] << 16 |
	       (uint32_t)p[1] << 8 | p[0];
}

static int check_bundle(const unsigned char *buf, size_t size)
{
	static const char * const names[] = { "cferam.000", "vmlinux.lz", "96846.dtb" };
	size_t pos = 0, end, start, len, next, i, j, name_len;
	const unsigned char *data;

	for (i = 0; i < 3; i++) {
		if (size - pos < 16)
			return -1;
		/* Compare differences before adding lengths, including on ARM32. */
		next = be32(buf + pos);
		start = be32(buf + pos + 4);
		len = be32(buf + pos + 8);
		if (next < 16 || next > size - pos - 4 || start < 8 ||
		    start > next - 8 || len != next - 8 - start ||
		    ((uint32_t)crc32(0, buf + pos, 12) ^ UINT32_MAX) != be32(buf + pos + 12))
			return -1;
		end = pos + next;
		start += pos + 8;
		name_len = strlen(names[i]) + 1;
		if (start - pos - 16 < name_len ||
		    memcmp(buf + pos + 16, names[i], name_len))
			return -1;
		for (j = pos + 16 + name_len; j < start; j++)
			if (buf[j])
				return -1;
		if (((uint32_t)crc32(0, buf + pos + 16, next - 16) ^ UINT32_MAX) != be32(buf + end))
			return -1;
		data = buf + start;
		switch (i) {
		case 0:
			/* Supported CFE RAM stage, not a kernel-only upgrade. */
			if (len != 686316 || crc32(0, data, len) != 0xa0024b86)
				return -1;
			break;
		case 1:
			if (len < 33 || le32(data) != 0x00200000 ||
			    le32(data + 4) != 0x00200000 || le32(data + 8) != len - 20 ||
			    memcmp(data + 12, "BRCM", 4) || le32(data + 16))
				return -1;
			break;
		case 2:
			if (len < 40 || be32(data) != 0xd00dfeed || be32(data + 4) != len)
				return -1;
			break;
		}
		pos = end + 4;
	}
	if (size - pos != 16 || memcmp(buf + pos, (unsigned char[16]) { 0 }, 16))
		return -1;
	return 0;
}

int main(int argc, char **argv)
{
	unsigned char *buf;
	FILE *fp;
	long size;
	int ret = 1;

	if (argc != 2) {
		fprintf(stderr, "Usage: %s kernel-bundle\n", argv[0]);
		return 1;
	}
	fp = fopen(argv[1], "rb");
	if (!fp)
		return 1;
	if (fseek(fp, 0, SEEK_END))
		goto close;
	size = ftell(fp);
	if (size < 16 || size > MAX_BUNDLE)
		goto close;
	rewind(fp);
	buf = malloc(size);
	if (!buf)
		goto close;
	if (fread(buf, 1, size, fp) == (size_t)size && !check_bundle(buf, size))
		ret = 0;
	free(buf);
close:
	fclose(fp);
	if (ret)
		fprintf(stderr, "Invalid XG6846B CFE boot bundle\n");
	return ret;
}
