#!/bin/bash
# RTL functional-equivalence sanity check for the PPA agent.
#
# Usage: check_equiv.sh <orig_src_dir> <mod_src_dir> <design_name>
#
# Exit codes:
#   0  = equivalent (or skipped because no TB exists for this design)
#   1  = FAIL: output mismatch (agent broke functionality)
#   2  = FAIL: could not build orig or mod design (syntax error etc.)
#   3  = usage / internal error
#
# Philosophy: sanity gate, not formal verification. Pipeline-depth changes
# and handshake-timing changes MUST pass. Only functional divergence fails.

set -u

EQUIV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TB_DIR="$EQUIV_DIR/tb"

# Prefer the more capable simulator if available. (Verilator rejects some of
# the designs' non-standard constructs, so we stick with iverilog for now.)
# Language level defaults to 2012 to accept .sv + logic + packages.
IVERILOG_LANG="${IVERILOG_LANG:-2012}"

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <orig_src_dir> <mod_src_dir> <design_name>" >&2
    exit 3
fi

ORIG_DIR="$1"
MOD_DIR="$2"
DESIGN="$3"
TB_FILE="$TB_DIR/${DESIGN}_tb.v"
RUN_DIR="$(mktemp -d -t equiv_${DESIGN}_XXXXXX)"
trap 'rm -rf "$RUN_DIR"' EXIT

if [ ! -f "$TB_FILE" ]; then
    # Designs without a TB are skipped — e.g. ibex_core (iverilog does not
    # support the SystemVerilog features it uses) or riscv_top (non-standard
    # wire-driven-from-always constructs iverilog rejects).
    echo "SKIP: no TB for design '$DESIGN'"
    exit 0
fi
if [ ! -d "$ORIG_DIR" ] || [ ! -d "$MOD_DIR" ]; then
    echo "ERROR: missing src dir (orig=$ORIG_DIR mod=$MOD_DIR)" >&2
    exit 3
fi

# Event-matched TBs diff line-by-line; latency-sweep TBs use compare.py.
case "$DESIGN" in
    fsm|ethmac|uart|riscv_top|jpeg_encoder)
        MODE="sweep" ;;
    *)
        MODE="event" ;;
esac

compile() {
    local label="$1" srcdir="$2" out="$3"
    # Pick up .v and .sv; skip include/ (headers pulled in via `include).
    mapfile -t srcs < <(find "$srcdir" \
        \( -name '*.v' -o -name '*.sv' \) -type f \
        -not -path '*/include/*' | sort)
    if [ "${#srcs[@]}" -eq 0 ]; then
        echo "ERROR: no .v/.sv files in $srcdir" >&2
        return 1
    fi
    local inc_args=()
    while IFS= read -r d; do
        inc_args+=( -I "$d" )
    done < <(find "$srcdir" -type d)
    iverilog -g"$IVERILOG_LANG" -Wno-timescale -o "$out" "${inc_args[@]}" \
        -s equiv_tb "${srcs[@]}" "$TB_FILE" 2> "$RUN_DIR/${label}_build.log"
}

run() {
    local vvp="$1" log="$2"
    # Hard wall-clock cap protects against simulation hangs caused by an
    # agent edit that deadlocks a handshake loop (e.g. req_rdy never asserts).
    timeout 60 vvp -n "$vvp" +LOG="$log" > "${log}.stdout" 2>&1
}

if ! compile orig "$ORIG_DIR" "$RUN_DIR/orig.vvp"; then
    echo "FAIL_BUILD_ORIG"; cat "$RUN_DIR/orig_build.log" >&2; exit 2
fi
if ! compile mod "$MOD_DIR" "$RUN_DIR/mod.vvp"; then
    echo "FAIL_BUILD_MOD"; cat "$RUN_DIR/mod_build.log" >&2; exit 2
fi
run "$RUN_DIR/orig.vvp" "$RUN_DIR/orig.log"
run "$RUN_DIR/mod.vvp"  "$RUN_DIR/mod.log"

if [ ! -s "$RUN_DIR/orig.log" ] || [ ! -s "$RUN_DIR/mod.log" ]; then
    echo "FAIL: empty log (tb didn't produce output)"
    echo "-- orig stdout --"; cat "$RUN_DIR/orig.log.stdout"
    echo "-- mod stdout --";  cat "$RUN_DIR/mod.log.stdout"
    exit 1
fi

if [ "$MODE" = "event" ]; then
    n_orig=$(wc -l < "$RUN_DIR/orig.log")
    n_mod=$(wc -l < "$RUN_DIR/mod.log")
    # If one run produced fewer than half the events of the other, the mod
    # DUT likely deadlocked (handshake broken) — call that a fail even if
    # the shorter prefix happens to match.
    lo=$(( n_orig < n_mod ? n_orig : n_mod ))
    hi=$(( n_orig > n_mod ? n_orig : n_mod ))
    if [ "$hi" -gt 0 ] && [ "$lo" -lt $(( hi / 2 )) ]; then
        echo "FAIL: event-count mismatch (orig=$n_orig mod=$n_mod) — likely deadlock"
        exit 1
    fi
    n="$lo"
    head -n "$n" "$RUN_DIR/orig.log" > "$RUN_DIR/orig.trim"
    head -n "$n" "$RUN_DIR/mod.log"  > "$RUN_DIR/mod.trim"
    if diff -q "$RUN_DIR/orig.trim" "$RUN_DIR/mod.trim" > /dev/null; then
        echo "PASS: $n events matched"
        exit 0
    fi
    echo "FAIL: event-stream mismatch"
    diff "$RUN_DIR/orig.trim" "$RUN_DIR/mod.trim" | head -20
    exit 1
else
    python3 "$EQUIV_DIR/compare.py" --sweep 16 --warmup 8 \
        "$RUN_DIR/orig.log" "$RUN_DIR/mod.log"
    exit $?
fi
