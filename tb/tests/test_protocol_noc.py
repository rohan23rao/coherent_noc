"""The full system over the 2x2 mesh (Phase 8).

Every scenario here is the same code that runs on the direct-connect harness in
test_protocol_mesi.py -- the bodies live in models/scenarios.py and both suites
call them. That is deliberate: if something passes directly and fails here, the
protocol is already known good and the network is the suspect, and the two runs
are comparable because they are literally the same test.

What this adds over Phase 7 is everything between the caches: packetization
into flits, virtual-network assignment, VC allocation, XY routing across four
routers, credit flow control, and reassembly. The VNet-assignment assertion in
tile_nic and the routing assertions in noc_top are active throughout.
"""

import cocotb
import pytest
from cocotb.clock import Clock

from models.multicore import MultiCoreDriver
from models.net_delay import NetDelay
from models import scenarios as sc
from runner import RTL_DIR, lib, run
from tbutil import reset_dut, step

L1 = RTL_DIR / "l1"
L2 = RTL_DIR / "l2"
MEM = RTL_DIR / "mem"
NOC = RTL_DIR / "noc"
TILE = RTL_DIR / "tile"
TOP = RTL_DIR / "top"

MEM_LINES = 1024
MEM_LAT = 8


def _sources():
    return [
        lib("sram_1rw"), lib("fifo"), lib("rr_arbiter"), lib("credit_counter"),
        lib("reset_sync"), lib("msg_hold"),
        MEM / "mem_model.sv",
        L1 / "l1_coh_fsm.sv", L1 / "mshr_file.sv", L1 / "l1_cache.sv",
        L2 / "dir_coh_fsm.sv", L2 / "l2_bank.sv", L2 / "tbe_file.sv",
        L2 / "dir_ctrl.sv",
        NOC / "route_compute.sv", NOC / "input_unit.sv", NOC / "vc_allocator.sv",
        NOC / "switch_allocator.sv", NOC / "crossbar.sv", NOC / "router.sv",
        NOC / "noc_top.sv",
        TILE / "tile_nic.sv", TILE / "tile_top.sv",
        TOP / "system_top.sv",
    ]


async def _setup(dut, seed=0):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    delay = NetDelay(dut)
    # system_top takes the RAW asynchronous reset and synchronizes it once,
    # internally, which is the whole point of having one synchronizer.
    await reset_dut(dut, drive={}, rst_name="arst_n")
    return drv, delay


@cocotb.test()
async def test_two_cores_share_over_network(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_two_cores_share(dut, drv))


@cocotb.test()
async def test_store_invalidates_over_network(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_store_invalidates(dut, drv))


@cocotb.test()
async def test_lone_reader_gets_e_over_network(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_lone_reader_gets_e(dut, drv))


@cocotb.test()
async def test_silent_upgrade_over_network(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_silent_upgrade(dut, drv))


@cocotb.test()
async def test_dirty_read_downgrades_over_network(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_dirty_read_downgrades(dut, drv))


@cocotb.test()
async def test_r11_over_network(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_r11(dut, drv))


@cocotb.test()
async def test_r5_over_network(dut):
    drv, delay = await _setup(dut)
    dut._log.info("hook: %s", delay.flavour)
    dut._log.info("%s", await sc.scenario_r5(dut, drv, delay))


@cocotb.test()
async def test_random_over_network(dut):
    """10k randomized requests across four cores, over the mesh."""
    drv, _ = await _setup(dut, seed=0x0C0FFEE)
    dut._log.info("%s", await sc.scenario_random(dut, drv, mem_lines=MEM_LINES))


@pytest.mark.protocol
def test_noc_system():
    run(
        toplevel="system_top",
        test_module="test_protocol_noc",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LAT": MEM_LAT, "ENABLE_E": 1},
    )
