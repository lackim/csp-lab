#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/csp_id.h>

static unsigned int copies;

static void *tracked_copy(void *destination, const void *source, size_t length) {
    copies++;
    return memcpy(destination, source, length);
}

#define memcpy tracked_copy
#include "zmq_frame.h"
#undef memcpy

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #condition); \
        return 1; \
    } \
} while (0)

struct guarded_packet {
    uint64_t before;
    csp_packet_t packet;
    uint64_t after;
};

static size_t make_wire(uint8_t *wire, size_t payload) {
    csp_packet_t packet = {0};
    packet.id.src = 5;
    packet.id.dst = 2;
    packet.id.sport = 17;
    packet.id.dport = CSP_PING;
    csp_id_prepend_fixup_cspv1(&packet);
    size_t prefix = csp_conf.version == 1 ? 1 : 0;
    if (prefix) wire[0] = 2;
    memcpy(wire + prefix, packet.frame_begin, packet.frame_length);
    size_t header = prefix + packet.frame_length;
    for (size_t i = 0; i < payload; i++) wire[header + i] = (uint8_t)i;
    return header + payload;
}

static int reject_unchanged(const uint8_t *wire, size_t length) {
    struct guarded_packet destination;
    memset(&destination, 0xa5, sizeof(destination));
    struct guarded_packet snapshot;
    memcpy(&snapshot, &destination, sizeof(destination));
    copies = 0;
    CHECK(csp_lab_zmq_frame_copy(&destination.packet, wire, length) != 0);
    CHECK(copies == 0);
    CHECK(memcmp(&destination, &snapshot, sizeof(destination)) == 0);
    return 0;
}

int main(void) {
    uint8_t wire[65536 + 6];
    CHECK(CSP_BUFFER_SIZE == 256);
    for (unsigned int version = 1; version <= 2; version++) {
        csp_conf.version = (uint8_t)version;
        size_t header = make_wire(wire, 0);
        CHECK(header == (version == 1 ? 5U : 6U));
        for (size_t length = 0; length < header; length++) {
            CHECK(reject_unchanged(wire, length) == 0);
        }
        const size_t oversized[] = {257, 4096, 65536};
        for (size_t i = 0; i < sizeof(oversized) / sizeof(oversized[0]); i++) {
            size_t length = make_wire(wire, oversized[i]);
            CHECK(reject_unchanged(wire, length) == 0);
        }
        const size_t accepted[] = {0, 1, 255, 256};
        for (size_t i = 0; i < sizeof(accepted) / sizeof(accepted[0]); i++) {
            size_t length = make_wire(wire, accepted[i]);
            struct guarded_packet destination;
            memset(&destination, 0xa5, sizeof(destination));
            uint64_t before = destination.before;
            uint64_t after = destination.after;
            copies = 0;
            CHECK(csp_lab_zmq_frame_copy(&destination.packet, wire, length) == 0);
            CHECK(copies == 1);
            CHECK(destination.before == before && destination.after == after);
            CHECK(destination.packet.length == accepted[i]);
            CHECK(destination.packet.id.src == 5 && destination.packet.id.dst == 2);
            CHECK(memcmp(destination.packet.data, wire + header, accepted[i]) == 0);
        }
        CHECK(reject_unchanged(NULL, header) == 0);
        printf("CSP v%u: direct copy-count, unchanged destination, and canary checks passed\n", version);
    }
    csp_conf.version = 3;
    CHECK(reject_unchanged(wire, 6) == 0);
    return 0;
}
