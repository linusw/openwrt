#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the kernel's BCM6846 quad-PHY power-up with mocked OF/MDIO/MMIO."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((UINT32_MAX >> (31 - (h))) & (UINT32_MAX << (l)))
#define FIELD_PREP(mask, val) (((val) << __builtin_ctz(mask)) & (mask))
#define __iomem
#define __free(x) __attribute__((cleanup(free_##x)))
#define EPROBE_DEFER 517
struct device_node {
    struct device_node *child, *next, *phy;
    bool available;
    u32 id;
    int error, refs;
};
static void of_node_put(struct device_node *np)
{ if (np) { assert(np->refs > 0); np->refs--; } }
static void free_device_node(struct device_node **np) { of_node_put(*np); }
static struct device_node *next_child(struct device_node *p, struct device_node *np)
{
    struct device_node *next = np ? np->next : p->child;
    of_node_put(np);
    while (next && !next->available) next = next->next;
    if (next) next->refs++;
    return next;
}
#define for_each_available_child_of_node_scoped(p, np) \
    for (struct device_node *np __free(device_node) = next_child(p, NULL); \
         np; np = next_child(p, np))
static int of_property_read_u32(struct device_node *np, const char *s, u32 *id)
{ assert(!strcmp(s, "reg")); *id = np->id; return np->error; }
static struct device_node *of_parse_phandle(struct device_node *np,
                                          const char *s, int index)
{
    assert(!strcmp(s, "phy-handle") && index == 0);
    if (np->phy) np->phy->refs++;
    return np->phy;
}
struct device { int refs; };
struct phy_device { struct { struct device dev; } mdio; };
static struct phy_device phy;
static struct device_node phy_node;
static bool mdio_ready;
static int locked, reads, writes, delays;
static u32 regval, fault, trace[6];
static uint8_t mmio[0xdb220c];
static struct phy_device *of_phy_find_device(struct device_node *np)
{
    assert(np == &phy_node && np->refs == 1);
    if (!mdio_ready) return NULL;
    phy.mdio.dev.refs++;
    return &phy;
}
static void put_device(struct device *dev)
{ assert(dev->refs == 1); dev->refs--; }
static void phy_lock_mdio_bus(struct phy_device *p)
{ assert(p == &phy && !locked); locked = 1; }
static void phy_unlock_mdio_bus(struct phy_device *p)
{ assert(p == &phy && locked); locked = 0; }
static u32 readl(void *addr)
{
    assert(locked && addr == mmio + 0xdb2208);
    return regval ^ (++reads == 2 ? fault : 0);
}
static void writel(u32 val, void *addr)
{
    assert(locked && addr == mmio + 0xdb2208 && writes < 6);
    assert(delays == (writes < 5 ? writes : 4));
    regval = val;
    trace[writes++] = val;
}
static void usleep_range(unsigned int lo, unsigned int hi)
{
    assert(locked && lo >= 1000 && hi >= lo && delays < 5);
    assert(writes == (delays < 4 ? delays + 1 : 6));
    delays++;
}
struct bcmbca_xrdp_enet { void *base; };
'''


MAIN = r'''
static struct device_node ports, children[5];
static void setup(unsigned int enabled, u32 initial)
{
    memset(children, 0, sizeof(children));
    for (unsigned int i = 0; i < 5; i++) {
        children[i].available = !!(enabled & BIT(i));
        children[i].id = i;
        children[i].phy = &phy_node;
        children[i].next = i < 4 ? &children[i + 1] : NULL;
    }
    ports.child = children;
    mdio_ready = true;
    regval = initial;
    reads = writes = delays = locked = fault = 0;
}
static int run(void)
{
    struct bcmbca_xrdp_enet enet = { .base = mmio };
    int ret = bcm6846_xrdp_phy_init(&enet, &ports);
    assert(!locked && !phy.mdio.dev.refs && !phy_node.refs);
    for (unsigned int i = 0; i < 5; i++) assert(!children[i].refs);
    return ret;
}
int main(void)
{
    /* Independent trace from the board's CFE _phy_cfg(0xf). */
    const u32 expected[] = { 0x0010115f, 0x0030115f, 0x0030111e,
                             0x00301100, 0x00101100, 0x00101000 };
    const u32 changed = 0x0020015f;
    unsigned int cases = 0;
    setup(0x1f, 0x0010105f);
    assert(run() == 0 && writes == 6 && delays == 5 && reads == 2);
    assert(!memcmp(trace, expected, sizeof(expected)));
    cases++;
    for (unsigned int enabled = 1; enabled <= 15; enabled++) {
        for (unsigned int bit = 0; bit < 32; bit++) {
            u32 initial = 0x0010105f ^ BIT(bit);
            setup(enabled | BIT(4), initial);
            assert(run() == 0 && writes == 6 && delays == 5);
            assert((regval & changed) == ((~enabled & 15) << 1));
            assert((regval & ~changed) == (initial & ~changed));
            cases++;
        }
    }
    for (unsigned int bit = 0; bit < 32; bit++) {
        if (!(changed & BIT(bit))) continue;
        setup(15, 0x0010105f);
        fault = BIT(bit);
        assert(run() == -EIO && writes == 6 && delays == 5);
        cases++;
    }
    setup(0, 0); assert(run() == 0 && !reads && !writes); cases++;
    setup(16, 0); assert(run() == 0 && !reads && !writes); cases++;
    setup(15, 0); mdio_ready = false;
    assert(run() == -EPROBE_DEFER && !reads && !writes); cases++;
    setup(15, 0); children[0].phy = NULL;
    assert(run() == -EINVAL && !reads && !writes); cases++;
    setup(15, 0); children[2].error = -EINVAL;
    assert(run() == -EINVAL && !reads && !writes); cases++;
    printf("PASS: %u QEGPHY cases: CFE trace, all port masks, preserved bits, "
           "timing, MDIO locking, readback errors, deferral and OF references\n", cases);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    args = parser.parse_args()
    source = (args.kernel / 'drivers/net/ethernet/broadcom/bcmbca_xrdp.c').read_text()
    definitions = '\n'.join(re.findall(r'^#define BCM6846_QEGPHY_.*$', source, re.M))
    functions = source[source.index('static int bcm6846_xrdp_qegphy_powerup'):
                       source.index('static int bcmbca_xrdp_probe')]
    with tempfile.TemporaryDirectory(prefix='bcmbca-qegphy-') as tmp:
        cfile = Path(tmp) / 'qegphy.c'
        binary = Path(tmp) / 'qegphy'
        cfile.write_text(PRELUDE + definitions + '\n' + functions + MAIN)
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                        str(cfile), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
