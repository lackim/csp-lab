#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <zmq.h>

#include <csp/csp.h>
#include <csp/csp_id.h>
#include <csp/interfaces/csp_lab_zmq_frame.h>

static uint64_t now_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

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

static int expect_echo(void *subscriber, const uint8_t *payload, size_t length) {
    uint64_t deadline = now_ms() + 3000;
    while (now_ms() < deadline) {
        zmq_pollitem_t item = {.socket = subscriber, .events = ZMQ_POLLIN};
        if (zmq_poll(&item, 1, 100) <= 0) continue;
        zmq_msg_t message;
        zmq_msg_init(&message);
        if (zmq_msg_recv(&message, subscriber, 0) < 0) {
            zmq_msg_close(&message);
            return 1;
        }
        csp_packet_t packet = {0};
        int parsed = csp_lab_zmq_frame_copy(&packet, zmq_msg_data(&message), zmq_msg_size(&message));
        zmq_msg_close(&message);
        if (parsed == 0 && packet.id.src == 2 && packet.id.dst == 5 &&
            packet.length == length && memcmp(packet.data, payload, length) == 0) {
            return 0;
        }
    }
    fprintf(stderr, "No valid echo after malformed frames\n");
    return 1;
}

int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "1") != 0 && strcmp(argv[1], "2") != 0)) return 2;
    csp_conf.version = (uint8_t)atoi(argv[1]);
    void *context = zmq_ctx_new();
    void *publisher = zmq_socket(context, ZMQ_PUB);
    void *subscriber = zmq_socket(context, ZMQ_SUB);
    int linger = 0;
    zmq_setsockopt(publisher, ZMQ_LINGER, &linger, sizeof(linger));
    zmq_setsockopt(subscriber, ZMQ_LINGER, &linger, sizeof(linger));
    if (zmq_connect(publisher, "tcp://127.0.0.1:6000") != 0 ||
        zmq_connect(subscriber, "tcp://127.0.0.1:7000") != 0 ||
        zmq_setsockopt(subscriber, ZMQ_SUBSCRIBE, "", 0) != 0) return 1;
    usleep(500000);
    uint8_t wire[65536 + 6];
    size_t header = make_wire(wire, 0);
    /* The hub sees even frames too short to match a node's topic. */
    for (size_t length = 0; length < header; length++) {
        if (zmq_send(publisher, wire, length, 0) < 0) return 1;
    }
    const size_t oversized[] = {257, 4096, 65536};
    for (size_t i = 0; i < sizeof(oversized) / sizeof(oversized[0]); i++) {
        size_t length = make_wire(wire, oversized[i]);
        if (zmq_send(publisher, wire, length, 0) < 0) return 1;
    }
    int result = 0;
    const size_t accepted[] = {0, 256};
    for (size_t i = 0; i < sizeof(accepted) / sizeof(accepted[0]); i++) {
        size_t length = make_wire(wire, accepted[i]);
        if (zmq_send(publisher, wire, length, 0) < 0 ||
            expect_echo(subscriber, wire + header, accepted[i]) != 0) {
            result = 1;
            break;
        }
    }
    zmq_close(publisher);
    zmq_close(subscriber);
    zmq_ctx_term(context);
    if (result == 0) printf("CSP v%u: live hub/node accepted boundary payloads after invalid frames\n", csp_conf.version);
    return result;
}
