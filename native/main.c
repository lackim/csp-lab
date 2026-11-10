#include <csp/csp.h>
#include <csp/interfaces/csp_if_zmqhub.h>

#include <pthread.h>
#include <errno.h>
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
    if (*text == '\0') return -1;
    for (const char *p = text; *p; ++p) {
        if (*p < '0' || *p > '9') return -1;
    }
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 10);
    if (errno == ERANGE || *end != '\0' || parsed > limit) {
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

#define MAX_TARGETS 16
/* libcsp's pinned build has eight connections; leave room for late closes. */
#define PING_BATCH 4

/* Ping one batch concurrently so unreachable nodes share a single timeout. */
static void ping_batch(const uint16_t *targets, size_t count, int *rtt) {
    const uint32_t timeout = 1500;
    const uint64_t start = now_ms();
    csp_conn_t *conns[PING_BATCH] = {0};
    uint8_t tokens[PING_BATCH][16];
    size_t pending = 0;

    for (size_t i = 0; i < count; i++) {
        rtt[i] = -1;
        conns[i] = csp_connect(CSP_PRIO_NORM, targets[i], CSP_PING, timeout, CSP_O_NONE);
        if (conns[i] == NULL) continue;
        csp_packet_t *request = csp_buffer_get(0);
        if (request == NULL) {
            csp_close(conns[i]);
            conns[i] = NULL;
            continue;
        }
        uint64_t nonce = start ^ ((uint64_t)getpid() << 32) ^ ((uint64_t)i << 48) ^ targets[i];
        for (size_t b = 0; b < sizeof(tokens[i]); b++)
            tokens[i][b] = (uint8_t)(nonce >> ((b % 8) * 8));
        memcpy(request->data, tokens[i], sizeof(tokens[i]));
        request->length = sizeof(tokens[i]);
        csp_send(conns[i], request);
        pending++;
    }

    while (pending > 0 && now_ms() - start < timeout) {
        for (size_t i = 0; i < count; i++) {
            if (conns[i] == NULL || rtt[i] >= 0) continue;
            uint64_t elapsed = now_ms() - start;
            if (elapsed >= timeout) break;
            uint32_t remaining = (uint32_t)(timeout - elapsed);
            /* A lone pending connection may block; several are polled in turn. */
            csp_packet_t *reply = csp_read(conns[i], pending == 1 ? remaining : 10);
            if (reply == NULL) {
                if (pending == 1) pending = 0;
                continue;
            }
            bool matches = reply->id.src == targets[i] && reply->length == sizeof(tokens[i]) &&
                           memcmp(reply->data, tokens[i], sizeof(tokens[i])) == 0;
            csp_buffer_free(reply);
            if (matches) {
                rtt[i] = (int)(now_ms() - start);
                pending--;
            }
        }
    }
    for (size_t i = 0; i < count; i++) {
        if (conns[i] != NULL) csp_close(conns[i]);
    }
}

static int hex_digit(int ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

static int read_payload(uint8_t payload[256], size_t *length) {
    size_t digits = 0;
    int high = 0, ch;
    while ((ch = getchar()) != '\n') {
        int digit = hex_digit(ch);
        if (digit < 0 || digits >= 512) return -1;
        if (digits % 2 == 0) high = digit;
        else payload[digits / 2] = (uint8_t)((high << 4) | digit);
        ++digits;
    }
    if (digits % 2 || getchar() != EOF) return -1;
    *length = digits / 2;
    return 0;
}

static int request_reply(uint16_t address, uint16_t target, uint8_t port,
                         uint32_t timeout, const uint8_t *payload, size_t length) {
    csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, target, port, 0, CSP_O_NONE);
    if (conn == NULL) return 1;
    csp_packet_t *packet = csp_buffer_get(0);
    if (packet == NULL) {
        csp_close(conn);
        return 1;
    }
    memcpy(packet->data, payload, length);
    packet->length = (uint16_t)length;
    const uint64_t deadline = now_ms() + timeout;
    csp_send(conn, packet);  /* One application send; no retries or RDP. */
    while (true) {
        uint64_t current = now_ms();
        if (current >= deadline) break;
        csp_packet_t *reply = csp_read(conn, (uint32_t)(deadline - current));
        if (reply == NULL) break;
        bool matches = reply->id.src == target && reply->id.sport == port &&
                       reply->id.dst == address &&
                       reply->id.dport == (unsigned int)csp_conn_dport(conn);
        if (matches && reply->length <= sizeof(reply->data)) {
            printf("{\"status\":\"ok\",\"reply\":\"");
            for (size_t i = 0; i < reply->length; ++i) printf("%02x", reply->data[i]);
            printf("\"}\n");
            csp_buffer_free(reply);
            csp_close(conn);
            return 0;
        }
        csp_buffer_free(reply);
    }
    csp_close(conn);
    printf("{\"status\":\"timeout\"}\n");
    return 0;
}

int main(int argc, char **argv) {
    bool request = argc == 7 && strcmp(argv[1], "request") == 0;
    bool server = argc == 5 && strcmp(argv[1], "server") == 0;
    bool ping = argc >= 5 && argc <= 4 + MAX_TARGETS && strcmp(argv[1], "probe") == 0;
    if (!request && !server && !ping) {
        fprintf(stderr, "usage: csp-lab-native server VERSION ADDRESS HUB\n"
                        "       csp-lab-native probe VERSION ADDRESS TARGET...\n"
                        "       csp-lab-native request VERSION ADDRESS TARGET PORT TIMEOUT_MS\n");
        return 2;
    }

    unsigned int version, address, target = 0, port = 0, timeout = 0;
    uint16_t targets[MAX_TARGETS];
    size_t target_count = 0;
    uint8_t payload[256];
    size_t length = 0;
    if (parse_uint(argv[2], 2, &version) != 0 || version < 1 ||
        parse_uint(argv[3], version == 1 ? (request ? 30 : 31) :
                   (request ? 16382 : 16383), &address) != 0 || address == 0) {
        fprintf(stderr, "invalid CSP version or address\n");
        return 2;
    }

    bool probe = request || ping;
    const char *hub = argv[4];
    if (probe) {
        const int last = request ? 4 : argc - 1;
        for (int i = 4; i <= last; i++) {
            if (parse_uint(argv[i], version == 1 ? (request ? 30 : 31) :
                          (request ? 16382 : 16383), &target) != 0 || target == 0) {
                fprintf(stderr, "invalid target\n");
                return 2;
            }
            targets[target_count++] = (uint16_t)target;
        }
        hub = "hub";
    }
    if (request && (target == 2 || target == 3 || target == 4 ||
        parse_uint(argv[5], 63, &port) != 0 || port < 10 ||
        parse_uint(argv[6], 10000, &timeout) != 0 || timeout < 100 ||
        read_payload(payload, &length) != 0)) {
        fprintf(stderr, "invalid request arguments or bounded hexadecimal input\n");
        return 2;
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
        if (request) return request_reply((uint16_t)address, (uint16_t)target,
                                          (uint8_t)port, timeout, payload, length);
        for (size_t first = 0; first < target_count; first += PING_BATCH) {
            size_t count = target_count - first < PING_BATCH ? target_count - first : PING_BATCH;
            int rtt[PING_BATCH];
            ping_batch(targets + first, count, rtt);
            for (size_t i = 0; i < count; i++) {
                printf("{\"target\":%u,\"reachable\":%s,\"rttMs\":%d}\n",
                       (unsigned int)targets[first + i], rtt[i] >= 0 ? "true" : "false", rtt[i]);
            }
        }
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
