#!/usr/bin/env bash
# Provision the HDL toolchain for this project.
# Containers here are ephemeral, so this runs fresh each session.
set -euo pipefail

VERILATOR_VERSION="${VERILATOR_VERSION:-v5.040}"
VERILATOR_PREFIX="${VERILATOR_PREFIX:-/opt/verilator}"

export DEBIAN_FRONTEND=noninteractive

echo "==> apt packages"
apt-get update -qq || true
apt-get install -y -qq \
  iverilog yosys gtkwave \
  build-essential git help2man perl perl-doc \
  autoconf flex bison libfl2 libfl-dev zlib1g-dev \
  ccache libgoogle-perftools-dev numactl

echo "==> python packages"
pip3 install --quiet cocotb cocotb-bus pytest pytest-xdist

# Ubuntu noble ships Verilator 5.020; cocotb 2.x requires >= 5.036, so build it.
if [ ! -x "$VERILATOR_PREFIX/bin/verilator" ]; then
  echo "==> building Verilator $VERILATOR_VERSION (this takes several minutes)"
  src="$(mktemp -d)"
  git clone --depth 1 --branch "$VERILATOR_VERSION" \
    https://github.com/verilator/verilator.git "$src"
  (
    cd "$src"
    autoconf
    ./configure --prefix="$VERILATOR_PREFIX"
    make -j"$(nproc)"
    make install
  )
  rm -rf "$src"
else
  echo "==> Verilator already present at $VERILATOR_PREFIX"
fi

# Put Verilator ahead of the apt copy on PATH.
if ! grep -qs "$VERILATOR_PREFIX/bin" /etc/profile.d/verilator.sh 2>/dev/null; then
  echo "export PATH=$VERILATOR_PREFIX/bin:\$PATH" > /etc/profile.d/verilator.sh
fi
export PATH="$VERILATOR_PREFIX/bin:$PATH"

echo "==> versions"
verilator --version
iverilog -V | head -1
yosys -V
echo "cocotb $(cocotb-config --version)"
