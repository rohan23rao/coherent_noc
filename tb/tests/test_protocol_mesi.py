"""MESI over a direct connection, no network (Phase 7).

The scenario bodies live in models/scenarios.py and are shared with
test_protocol_noc.py, which runs the identical code over the 2x2 mesh. Keeping
them in one place is what makes the Phase 7 / Phase 8 comparison meaningful: if
a scenario passes here and fails there, the protocol is known good and the
network is the suspect.
"""

import cocotb
import pytest
from cocotb.clock import Clock

from models.multicore import MultiCoreDriver
from models.net_delay import NetDelay
from models import scenarios as sc
from runner import HARNESS_DIR, RTL_DIR, lib, run
from tbutil import reset_dut

L1 = RTL_DIR / "l1"
L2 = RTL_DIR / "l2"
MEM = RTL_DIR / "mem"

MEM_LINES = 1024
MEM_LATENCY = 8


def _sources():
    return [
        lib("sram_1rw"), lib("fifo"), lib("rr_arbiter"), MEM / "mem_model.sv",
        L1 / "l1_coh_fsm.sv", L1 / "mshr_file.sv", L1 / "l1_cache.sv",
        L2 / "dir_coh_fsm.sv", L2 / "l2_bank.sv", L2 / "tbe_file.sv",
        L2 / "dir_ctrl.sv",
        HARNESS_DIR / "msg_mux.sv", HARNESS_DIR / "msg_delay.sv",
        HARNESS_DIR / "coh_direct_top.sv",
    ]


async def _setup(dut, seed=0):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = MultiCoreDriver(dut, seed=seed)
    drv.idle()
    dut.dbg_set_i.value = 0
    dut.dbg_dir_set_i.value = 0
    dut.dbg_dir_way_i.value = 0
    delay = NetDelay(dut)
    await reset_dut(dut, drive={})
    return drv, delay


@cocotb.test()
async def test_lone_reader_gets_exclusive(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_lone_reader_gets_e(dut, drv))


@cocotb.test()
async def test_e_to_m_upgrade_is_silent(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_silent_upgrade(dut, drv))


@cocotb.test()
async def test_two_cores_share_a_line(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_two_cores_share(dut, drv))


@cocotb.test()
async def test_store_invalidates_sharers(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_store_invalidates(dut, drv))


@cocotb.test()
async def test_read_of_a_dirty_line_downgrades_the_owner(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_dirty_read_downgrades(dut, drv))


@cocotb.test()
async def test_r11_silent_upgrade_then_eviction(dut):
    drv, _ = await _setup(dut)
    dut._log.info("%s", await sc.scenario_r11(dut, drv))


@cocotb.test()
async def test_r5_put_from_non_owner(dut):
    drv, delay = await _setup(dut)
    dut._log.info("hook: %s", delay.flavour)
    dut._log.info("%s", await sc.scenario_r5(dut, drv, delay))


@cocotb.test()
async def test_random_mesi_with_swmr(dut):
    drv, _ = await _setup(dut, seed=0xE5E5)
    dut._log.info("%s", await sc.scenario_random(dut, drv, mem_lines=MEM_LINES))


@pytest.mark.protocol
def test_mesi():
    run(
        toplevel="coh_direct_top",
        test_module="test_protocol_mesi",
        sources=_sources(),
        parameters={"MEM_LINES": MEM_LINES, "MEM_LATENCY_P": MEM_LATENCY,
                    "ENABLE_E": 1},
    )
