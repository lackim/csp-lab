/* Local bounds guard for the pinned libcsp v2.1 build. MIT licensed. */
#ifndef CSP_LAB_ZMQ_FRAME_H
#define CSP_LAB_ZMQ_FRAME_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/csp_id.h>

/* Validate the complete ZMQ frame before changing the destination packet. */
static inline int csp_lab_zmq_frame_copy(csp_packet_t *packet,
                                        const void *wire, size_t wire_length) {
    if (packet == NULL || wire == NULL ||
        (csp_conf.version != 1 && csp_conf.version != 2)) {
        return CSP_ERR_INVAL;
    }
    const size_t prefix = csp_conf.version == 1 ? 1 : 0;
    const size_t header = (size_t)csp_id_get_header_size();
    const size_t minimum = prefix + header;
    if (wire_length < minimum || wire_length - minimum > sizeof(packet->data)) {
        return CSP_ERR_INVAL;
    }

    csp_id_setup_rx(packet);
    memcpy(packet->frame_begin, (const uint8_t *)wire + prefix, wire_length - prefix);
    packet->frame_length = (uint16_t)(wire_length - prefix);
    return csp_id_strip_fixup_cspv1(packet);
}

#endif
