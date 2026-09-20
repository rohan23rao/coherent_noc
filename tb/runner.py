"""Shared cocotb-runner plumbing.

Every test module calls :func:`run` from a plain pytest function. The cocotb
coroutines themselves live in the same file and are picked up by cocotb when it
imports the module inside the simulator.

Builds are keyed by toplevel *and* parameter set, so two tests that elaborate
the same module with different parameters do not fight over one build
directory.
"""

import hashlib
import os
from pathlib import Path

from cocotb_tools.runner import get_runner

# cocotb 2.x refuses Verilator older than 5.036 and the distro package is
# 5.020, so make sure the locally built 5.040 is found first no matter how the
# test was invoked.
_VERILATOR_BIN = Path("/opt/verilator/bin")
if _VERILATOR_BIN.is_dir():
    os.environ["PATH"] = f"{_VERILATOR_BIN}:{os.environ.get('PATH', '')}"

TB_DIR = Path(__file__).resolve().parent
REPO_ROOT = TB_DIR.parent
RTL_DIR = REPO_ROOT / "rtl"
SIM_DIR = REPO_ROOT / "sim"
WAVE_DIR = SIM_DIR / "waves"

PKG = RTL_DIR / "pkg" / "coh_pkg.sv"

# The same waiver list `make lint` uses. Passing it here keeps the unit-test
# build and the lint build honest about exactly the same set of exceptions --
# a warning waived for one and not the other is how a lint gate rots.
WAIVERS = REPO_ROOT / "lint" / "waivers.vlt"
TEST_DIR = TB_DIR / "tests"

# The simulator re-imports the test module in its own Python process, which does
# not inherit pytest's sys.path. Both tb/ (for `runner`) and tb/tests/ (for the
# test module itself) have to be on PYTHONPATH explicitly.
_pypath = [str(TB_DIR), str(TEST_DIR)]
_existing = os.environ.get("PYTHONPATH", "")
if _existing:
    _pypath.append(_existing)
os.environ["PYTHONPATH"] = os.pathsep.join(_pypath)

# -Wall here as well as in `make lint`: a module that lints clean standalone can
# still warn when elaborated with non-default parameters, and that is exactly
# the case a unit test exercises.
BUILD_ARGS = [
    "-Wall",
    "--timing",
    "--assert",
    "-Wno-DECLFILENAME",
]


def lib(name: str) -> Path:
    return RTL_DIR / "lib" / f"{name}.sv"


def _build_dir(toplevel: str, parameters: dict | None) -> Path:
    key = repr(sorted((parameters or {}).items()))
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return SIM_DIR / "sim_build" / f"{toplevel}_{digest}"


def run(
    toplevel: str,
    test_module: str,
    sources: list,
    parameters: dict | None = None,
    seed: int | None = None,
):
    """Build `toplevel` from `sources` and run the cocotb tests in `test_module`."""
    parameters = parameters or {}
    waves = os.environ.get("COCOTB_TRACE", "0") == "1"

    build_args = list(BUILD_ARGS)
    if waves:
        build_args += ["--trace", "--trace-structs"]

    runner = get_runner("verilator")
    # coh_pkg always goes first: every module imports it, and a missing package
    # shows up as a confusing "Import package not found" rather than as a
    # missing-file error.
    ordered = [str(WAIVERS), str(PKG)] + [str(s) for s in sources if Path(s) != PKG]
    runner.build(
        sources=ordered,
        hdl_toplevel=toplevel,
        parameters=parameters,
        build_args=build_args,
        build_dir=str(_build_dir(toplevel, parameters)),
        always=True,
    )
    runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        test_dir=str(TEST_DIR),
        build_dir=str(_build_dir(toplevel, parameters)),
        seed=seed,
        waves=waves,
    )
