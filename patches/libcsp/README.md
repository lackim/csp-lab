# Local libcsp build patch

The source submodule remains pinned to v2.1 commit
`48f7fb0b57f610bf65bab1aa2d1357c3b9722782`.

At CMake configuration time, the build copies it into its own
`_deps/libcsp-src` directory, applies `0001-zmq-frame-bounds.patch`, and copies
`native/zmq_frame.h` into that copy's include tree. Configuration fails if the
patch does not apply. Reconfigure after updating the pinned source.

Both the ZMQ interface and the hub diagnostic receiver call the same guard. It
checks the complete wire length before touching the destination packet:

- CSP v1: one ZMQ destination prefix byte plus four CSP header bytes.
- CSP v2: six CSP header bytes.
- Payload: at most the compiled packet buffer capacity, currently 256 bytes.

Short and oversized messages are dropped. The hub allocates an actual
`csp_packet_t`, replacing its former fixed 1024-byte allocation. Successful frame
decoding retains the existing v1 little-endian compatibility handling.

Run `bash test/native-bounds.sh` with Docker. The direct tests count copies and
check unchanged destination memory/canaries for invalid frames. The live tests
inject short and oversized messages through the hub, then require successful
empty and 256-byte echoes and a stock ping for both versions. All managed native
code in this test image is built with AddressSanitizer. Leak detection is disabled
because the server and hub intentionally run until terminated; bounds failures
remain fatal. The runtime image does not contain sanitizer instrumentation.
