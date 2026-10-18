#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the patched kernel's XRDP initialization against a host PMB/MMIO model.

Usage: test_xrdp_power.py --kernel path/to/prepared/linux-6.18.x
No hardware is accessed. The actual reset callbacks, polling macros and
Ethernet initialization functions are extracted from the prepared kernel.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def macro(source, name):
    lines = source.splitlines(True)
    start = next(i for i, line in enumerate(lines)
                 if line.startswith(f"#define {name}("))
    end = start
    while lines[end].rstrip().endswith("\\"):
        end += 1
    return "".join(lines[start:end + 1])


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t ktime_t;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((UINT32_MAX >> (31 - (h))) & (UINT32_MAX << (l)))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define IS_ALIGNED(x, a) (!((x) & ((a) - 1)))
#define __iomem
#define BCM6846_RESET_XRDP 0
#define BCM6846_RESET_PCIE0 1
#define BCM6846_RESET_PCIE1 2
#define BCM6846_RESET_PVTMON 3
#define dev_err(...) ((void)0)
#define dev_err_probe(dev, err, ...) (err)
#define might_sleep_if(x) ((void)(x))
#define barrier() ((void)0)
#define cpu_relax() ((void)0)
static ktime_t ticks;
static ktime_t ktime_get(void) { return ++ticks; }
static ktime_t ktime_add_us(ktime_t t, u64 us) { return t + us; }
static int ktime_compare(ktime_t a, ktime_t b) { return (a > b) - (a < b); }
static void usleep_range(unsigned a, unsigned b) { (void)a; ticks += b; }
struct reset_controller_dev { void *dev; };
struct bcmbca_pmb { int unused; };
static u32 regs[2][32][64];
static unsigned pending[32][64];
static int operations, fail_at, locked, lock_error, stuck;
static int zone_writes, reset_writes, mmio_count, reset_result, clock_result;
static bool mmio_ready;
static const unsigned devices[] = {12, 14, 15, 16, 11};
static const unsigned zones[] = {3, 1, 1, 1, 3};
static bool all_on(void)
{
    for (unsigned i = 0; i < 5; i++)
        for (unsigned z = 0; z < zones[i]; z++)
            if (!(regs[1][devices[i]][32 + z * 8] & 0x04000000)) return false;
    return true;
}
static int bcmbca_pmb_lock(struct bcmbca_pmb *p)
{
    (void)p; assert(!locked);
    if (lock_error) return lock_error;
    locked = 1; return 0;
}
static void bcmbca_pmb_unlock(struct bcmbca_pmb *p)
{
    (void)p; assert(locked); locked = 0;
}
static int bcmbca_pmb_read(struct bcmbca_pmb *p, u8 bus, u8 dev, u16 off, u32 *val)
{
    (void)p; assert(locked && bus < 2 && dev < 32 && off < 64);
    if (++operations == fail_at) return -EIO;
    if (bus == 1 && pending[dev][off] && !stuck && !--pending[dev][off])
        regs[bus][dev][off] = (regs[bus][dev][off] & ~0x02000000) | 0x04000000;
    *val = regs[bus][dev][off]; return 0;
}
static int bcmbca_pmb_write(struct bcmbca_pmb *p, u8 bus, u8 dev, u16 off, u32 val)
{
    (void)p; assert(locked && bus < 2 && dev < 32 && off < 64);
    if (++operations == fail_at) return -EIO;
    if (bus == 1 && off >= 32) {
        /* Vendor 6846/BPCM.h: preserve other bits, clear down, set 8/10/11/12. */
        assert(val == ((regs[bus][dev][off] & ~0x200U) | 0x1d00U));
        assert(off == 32 || off == 40 || off == 48);
        pending[dev][off] = 2;
        zone_writes++;
    } else if (bus == 1 && off == 5) {
        assert(dev == 12 || dev == 14 || dev == 15 || dev == 16);
        assert((val & 0xf0000000) == 0xa0000000);
        if ((val & 0x0fffffff) == 0x0fffffff) assert(all_on());
        else assert((val & 0x0fffffff) == (dev == 12 ? 7 : 0));
        reset_writes++;
    }
    regs[bus][dev][off] = val; return 0;
}
static void model_init(bool on)
{
    memset(regs, 0, sizeof(regs)); memset(pending, 0, sizeof(pending));
    operations = fail_at = locked = lock_error = stuck = 0;
    zone_writes = reset_writes = mmio_count = reset_result = clock_result = 0;
    mmio_ready = false; ticks = 0;
    for (unsigned i = 0; i < 5; i++) {
        regs[1][devices[i]][5] = 0xafffffff;
        for (unsigned z = 0; z < zones[i]; z++)
            regs[1][devices[i]][32 + z * 8] = 0x01004001 | (on ? 0x04000000 : 0x02000200);
    }
}
'''

MMIO = r'''
static struct bcmbca_pmb bus;
static struct bcmbca_pmb_reset rst;
static int reset_control_reset(void *p)
{
    (void)p; assert(!mmio_ready);
    return reset_result ?: bcmbca_pmb_reset(&rst.rcdev, 0);
}
struct clk_bulk_data { int unused; };
static int clk_bulk_prepare_enable(int num, struct clk_bulk_data *clks)
{
    (void)num; (void)clks;
    assert(!mmio_ready && !reset_result && all_on());
    if (clock_result) return clock_result;
    mmio_ready = true;
    return 0;
}
static void clk_bulk_disable_unprepare(int num, struct clk_bulk_data *clks)
{
    (void)num; (void)clks; assert(mmio_ready);
    mmio_ready = false;
}
static u32 readl(const void *p)
{
    (void)p; assert(mmio_ready); mmio_count++; return 0;
}
static void writel(u32 v, void *p) { (void)v; (void)readl(p); }
#define writew writel
#define writeb writel
#define iowrite32be writel
static void memset_io(void *p, int v, size_t n) { (void)v; (void)n; (void)readl(p); }
#define readl_poll_timeout(addr, val, cond, sleep, timeout) \
    read_poll_timeout(readl, val, cond, sleep, timeout, false, addr)
#define UMAC_CMD 8
#define CMD_RX_EN 1
#define CMD_TX_EN 2
struct bcmbca_xrdp_enet {
    void *dev, *base, *reset;
    struct clk_bulk_data *clks;
    int num_clks;
    const struct bcmbca_xrdp_enet_variant *variant;
    bool hw_accessible;
};
'''

MAIN = r'''
int main(void)
{
    unsigned cases = 0;
    rst.pmb = &bus; rst.variant = &bcm6846_pmb_variant;
    model_init(false);
    assert(!bcmbca_pmb_reset(&rst.rcdev, 0));
    assert(!locked && all_on() && zone_writes == 9 && reset_writes == 8); cases++;
    int successful_operations = operations;
    model_init(true);
    assert(!bcmbca_pmb_reset(&rst.rcdev, 0));
    assert(!locked && zone_writes == 0 && reset_writes == 8); cases++;
    model_init(true); regs[1][14][32] = 0x02000200;
    assert(!bcmbca_pmb_reset(&rst.rcdev, 0));
    assert(zone_writes == 1 && all_on()); cases++;
    for (int i = 1; i <= successful_operations; i++) {
        model_init(false); fail_at = i;
        assert(bcmbca_pmb_reset(&rst.rcdev, 0) == -EIO && !locked);
        for (unsigned d = 0; d < 4; d++)
            assert((regs[1][devices[d]][5] & 0x0fffffff) == (d ? 0 : 7));
        cases++;
    }
    model_init(false); stuck = 1;
    assert(bcmbca_pmb_reset(&rst.rcdev, 0) == -ETIMEDOUT && !locked);
    assert(operations < 2000); cases++;
    model_init(false); lock_error = -EBUSY;
    assert(bcmbca_pmb_reset(&rst.rcdev, 0) == -EBUSY && !operations && !locked); cases++;
    for (unsigned id = 1; id < 4; id++) {
        model_init(false);
        assert(!bcmbca_pmb_reset(&rst.rcdev, id));
        assert(!zone_writes && !locked); cases++;
    }
    static char registers[4096];
    static const struct bcmbca_xrdp_port_data port = {.mac = 0x10, .bbh_rx = 0x30};
    static const struct bcmbca_xrdp_enet_variant variant = {
        .ports = &port, .num_ports = 1, .num_runners = 3, .runner_stride = 16,
        .runner_ctrl = 0x200, .sbpm = 0x400, .reg_size = sizeof(registers),
    };
    struct bcmbca_xrdp_enet enet = {.base = registers, .variant = &variant};
    model_init(false);
    bcmbca_xrdp_quiesce(&enet); assert(!mmio_count); cases++;
    assert(!bcmbca_xrdp_hw_init(&enet) && enet.hw_accessible && mmio_count); cases++;
    /* A subsequent reset failure must make cleanup skip all MMIO. */
    reset_result = -ETIMEDOUT;
    assert(bcmbca_xrdp_hw_init(&enet) == -ETIMEDOUT && !enet.hw_accessible);
    int count = mmio_count;
    bcmbca_xrdp_quiesce(&enet); assert(mmio_count == count); cases++;
    /* A failed clock acquisition must also leave cleanup MMIO-free. */
    model_init(false); clock_result = -EIO;
    assert(bcmbca_xrdp_hw_init(&enet) == -EIO && !enet.hw_accessible);
    bcmbca_xrdp_hw_stop(&enet); assert(!mmio_count && !mmio_ready); cases++;
    clock_result = 0;
    assert(!bcmbca_xrdp_hw_init(&enet) && mmio_ready);
    bcmbca_xrdp_hw_stop(&enet); assert(!mmio_ready && !enet.hw_accessible);
    count = mmio_count;
    bcmbca_xrdp_hw_stop(&enet); assert(mmio_count == count); cases++;
    /* Repeat with every power/reset transaction failing during first probe. */
    for (int i = 1; i <= successful_operations; i++) {
        model_init(false); fail_at = i; enet.hw_accessible = false;
        assert(bcmbca_xrdp_hw_init(&enet) == -EIO && !enet.hw_accessible);
        bcmbca_xrdp_quiesce(&enet); assert(!mmio_count); cases++;
    }
    printf("PASS: %u XRDP cold/warm power, reset, timeout and MMIO error-path cases\n", cases);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    args = parser.parse_args()
    kernel = args.kernel
    reset = (kernel / 'drivers/reset/reset-bcmbca-pmb.c').read_text()
    reset = reset[reset.index('#define BCM6846_ZONE_CONTROL'):reset.index('static const struct reset_control_ops')]
    eth = (kernel / 'drivers/net/ethernet/broadcom/bcmbca_xrdp.c').read_text()
    defines = eth[eth.index('#define RUNNER_ENABLE'):eth.index('#include "bcm6846_enet_data.h"')]
    functions = eth[eth.index('static void bcmbca_xrdp_quiesce'):eth.index('static int bcmbca_xrdp_sbpm_wait')]
    header = (kernel / 'drivers/net/ethernet/broadcom/bcmbca_xrdp.h').read_text()
    types = header[header.index('struct bcmbca_xrdp_init_entry'):header.index('struct bcmbca_xrdp_rx_info')]
    poll = (kernel / 'include/linux/iopoll.h').read_text()
    code = PRELUDE + macro(poll, 'poll_timeout_us') + macro(poll, 'read_poll_timeout')
    code += reset + defines + types + MMIO + functions + MAIN
    with tempfile.TemporaryDirectory(prefix='bcmbca-power-') as name:
        tmp = Path(name)
        (tmp / 'test.c').write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=undefined',
                        '-o', str(tmp / 'test'), str(tmp / 'test.c')], check=True)
        subprocess.run([tmp / 'test'], check=True)


if __name__ == '__main__':
    main()
