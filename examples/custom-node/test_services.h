/* Opt-in test instrumentation, excluded from the normal example image. */
#include <string.h>
#include <zmq.h>
#include <csp/csp_id.h>

static uint32_t application_sends = 0;
static void *raw_publisher;

static void raw_reply(const csp_packet_t *request, uint16_t source,
                      uint8_t port, size_t length) {
    csp_packet_t header = {0};
    header.id.src = source;
    header.id.dst = request->id.src;
    header.id.sport = port;
    header.id.dport = request->id.sport;
    csp_id_prepend_fixup_cspv1(&header);
    uint8_t wire[65536 + 6];
    size_t prefix = csp_conf.version == 1 ? 1 : 0;
    if (prefix) wire[0] = (uint8_t)request->id.src;
    memcpy(wire + prefix, header.frame_begin, header.frame_length);
    size_t start = prefix + header.frame_length;
    memset(wire + start, 'x', length);
    zmq_send(raw_publisher, wire, start + length, 0);
}

static bool test_service(csp_conn_t *conn, csp_packet_t *packet, uint16_t address) {
    unsigned int port = csp_conn_dport(conn);
    if (port == 10) {
        ++application_sends;
        return false;
    }
    if (port == 11) {
        for (size_t i = 0; i < 4; ++i)
            packet->data[i] = (uint8_t)(application_sends >> (24 - 8 * i));
        packet->length = 4;
        csp_send(conn, packet);
        return true;
    }
    if (port == 12) {
        ++application_sends;
        csp_buffer_free(packet);  /* Execute once and intentionally lose the reply. */
        return true;
    }
    if (port == 13) {
        packet->length = 0;
        csp_send(conn, packet);
        return true;
    }
    if (port == 14) {
        raw_reply(packet, address, 14, 257);
        raw_reply(packet, address, 14, 4096);
        raw_reply(packet, address, 14, 65536);
    } else if (port == 15) {
        raw_reply(packet, address == 6 ? 7 : 6, 15, 3);
    } else if (port == 16) {
        raw_reply(packet, address, 15, 3);
    } else {
        return false;
    }
    csp_buffer_free(packet);
    return true;
}
