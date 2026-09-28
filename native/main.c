#include <csp/csp.h>
#include <csp/interfaces/csp_if_zmqhub.h>

#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void *route_loop(void *unused) {
    (void)unused;
    while (true) {
        csp_route_work();
    }
    return NULL;
}

static int parse_uint(const char *text, unsigned int limit, unsigned int *value) {
    char *end = NULL;
    unsigned long parsed = strtoul(text, &end, 10);
    if (text[0] == '\0' || *end != '\0' || parsed > limit) {
        return -1;
    }
    *value = (unsigned int)parsed;
    return 0;
}

static uint64_t now_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

static int ping_exact_node(uint16_t target) {
    const uint32_t timeout = 1500;
    const uint64_t start = now_ms();
    csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, target, CSP_PING, timeout, CSP_O_NONE);
    if (conn == NULL) return -1;

    csp_packet_t *request = csp_buffer_get(0);
    if (request == NULL) {
        csp_close(conn);
        return -1;
    }
    uint8_t token[16];
    uint64_t nonce = start ^ ((uint64_t)getpid() << 32) ^ target;
    for (size_t i = 0; i < sizeof(token); i++) token[i] = (uint8_t)(nonce >> ((i % 8) * 8));
    memcpy(request->data, token, sizeof(token));
    request->length = sizeof(token);
    csp_send(conn, request);

    int rtt = -1;
    while (now_ms() - start < timeout) {
        uint32_t remaining = (uint32_t)(timeout - (now_ms() - start));
        csp_packet_t *reply = csp_read(conn, remaining);
        if (reply == NULL) break;
        bool matches = reply->id.src == target && reply->length == sizeof(token) &&
                       memcmp(reply->data, token, sizeof(token)) == 0;
        csp_buffer_free(reply);
        if (matches) {
            rtt = (int)(now_ms() - start);
            break;
        }
    }
    csp_close(conn);
    return rtt;
}

int main(int argc, char **argv) {
    if (argc != 5 || (strcmp(argv[1], "server") != 0 && strcmp(argv[1], "probe") != 0)) {
        fprintf(stderr, "usage: csp-lab-native server|probe VERSION ADDRESS HUB_OR_TARGET\n");
        return 2;
    }

    unsigned int version, address, target = 0;
    if (parse_uint(argv[2], 2, &version) != 0 || version < 1 ||
        parse_uint(argv[3], version == 1 ? 31 : 16383, &address) != 0 || address == 0) {
        fprintf(stderr, "invalid CSP version or address\n");
        return 2;
    }

    bool probe = strcmp(argv[1], "probe") == 0;
    const char *hub = argv[4];
    if (probe) {
        if (argc != 5 || parse_uint(argv[4], version == 1 ? 31 : 16383, &target) != 0 || target == 0) {
            fprintf(stderr, "invalid target\n");
            return 2;
        }
        hub = "hub";
    }

    csp_conf.version = (uint8_t)version;
    csp_init();

    pthread_t router;
    if (pthread_create(&router, NULL, route_loop, NULL) != 0) {
        fprintf(stderr, "unable to start CSP router\n");
        return 1;
    }

    csp_iface_t *iface = NULL;
    if (csp_zmqhub_init((uint16_t)address, hub, 0, &iface) != CSP_ERR_NONE || iface == NULL) {
        fprintf(stderr, "unable to connect to ZMQ hub\n");
        return 1;
    }
    /* A zero netmask makes every destination look like a subnet broadcast. */
    iface->netmask = (uint16_t)(version == 1 ? 5 : 14);
    iface->is_default = 1;
    if (csp_rtable_set(0, 0, iface, CSP_NO_VIA_ADDRESS) != CSP_ERR_NONE) {
        fprintf(stderr, "unable to set default route\n");
        return 1;
    }

    if (probe) {
        usleep(500000);
        int rtt = ping_exact_node((uint16_t)target);
        printf("{\"target\":%u,\"reachable\":%s,\"rttMs\":%d}\n",
               target, rtt >= 0 ? "true" : "false", rtt);
        return 0;
    }

    csp_socket_t socket = {0};
    if (csp_bind(&socket, CSP_PING) != CSP_ERR_NONE || csp_listen(&socket, 8) != CSP_ERR_NONE) {
        fprintf(stderr, "unable to listen for ping\n");
        return 1;
    }
    fprintf(stderr, "CSP v%u node %u ready\n", version, address);
    while (true) {
        csp_conn_t *conn = csp_accept(&socket, 1000);
        if (conn == NULL) continue;
        csp_packet_t *packet;
        while ((packet = csp_read(conn, 100)) != NULL) {
            csp_service_handler(packet);
        }
        csp_close(conn);
    }
}
