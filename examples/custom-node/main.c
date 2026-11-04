/* An independent application: standard CSP services and ASCII uppercase. */
#include <csp/csp.h>
#include <csp/interfaces/csp_if_zmqhub.h>

#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#ifdef CSP_LAB_EXAMPLE_TESTING
#include "test_services.h"
#endif

static void *route(void *unused) {
    (void)unused;
    while (true) csp_route_work();
    return NULL;
}

static unsigned int number(const char *name, unsigned int maximum) {
    const char *text = getenv(name);
    if (text == NULL || *text == '\0') {
        fprintf(stderr, "missing %s\n", name);
        exit(2);
    }
    for (const char *p = text; *p; ++p) {
        if (*p < '0' || *p > '9') {
            fprintf(stderr, "invalid %s\n", name);
            exit(2);
        }
    }
    char *end;
    unsigned long value = strtoul(text, &end, 10);
    if (*end || value == 0 || value > maximum) {
        fprintf(stderr, "invalid %s\n", name);
        exit(2);
    }
    return (unsigned int)value;
}

int main(void) {
    unsigned int protocol = number("CSP_LAB_PROTOCOL", 2);
    unsigned int address = number("CSP_LAB_ADDRESS", protocol == 1 ? 30 : 16382);
    const char *publish = getenv("CSP_LAB_ZMQ_PUBLISH_ENDPOINT");
    const char *subscribe = getenv("CSP_LAB_ZMQ_SUBSCRIBE_ENDPOINT");
    if (publish == NULL || subscribe == NULL) {
        fprintf(stderr, "both ZMQ endpoints are required\n");
        return 2;
    }

    csp_conf.version = (uint8_t)protocol;
    csp_init();
    pthread_t router;
    if (pthread_create(&router, NULL, route, NULL) != 0) return 1;
    csp_iface_t *iface = NULL;
    if (csp_zmqhub_init_w_endpoints((uint16_t)address, publish, subscribe, 0, &iface)
        != CSP_ERR_NONE || iface == NULL) return 1;
    iface->netmask = (uint16_t)(protocol == 1 ? 5 : 14);
    iface->is_default = 1;
    if (csp_rtable_set(0, 0, iface, CSP_NO_VIA_ADDRESS) != CSP_ERR_NONE) return 1;

#ifdef CSP_LAB_EXAMPLE_TESTING
    void *raw_context = zmq_ctx_new();
    raw_publisher = zmq_socket(raw_context, ZMQ_PUB);
    if (raw_publisher == NULL || zmq_connect(raw_publisher, publish) != 0) return 1;
#endif

    csp_socket_t socket = {0};
    if (csp_bind(&socket, CSP_ANY) != CSP_ERR_NONE || csp_listen(&socket, 8) != CSP_ERR_NONE)
        return 1;
    fprintf(stderr, "uppercase CSP v%u address %u ready (uid %u)\n",
            protocol, address, (unsigned int)getuid());
    while (true) {
        csp_conn_t *conn = csp_accept(&socket, 1000);
        if (conn == NULL) continue;
        csp_packet_t *packet;
        while ((packet = csp_read(conn, 100)) != NULL) {
#ifdef CSP_LAB_EXAMPLE_TESTING
            if (test_service(conn, packet, (uint16_t)address)) continue;
#endif
            if (csp_conn_dport(conn) != 10) {
                csp_service_handler(packet);
                continue;
            }
            for (size_t i = 0; i < packet->length; ++i) {
                if (packet->data[i] >= 'a' && packet->data[i] <= 'z')
                    packet->data[i] -= 'a' - 'A';
            }
            csp_send(conn, packet);
        }
        csp_close(conn);
    }
}
