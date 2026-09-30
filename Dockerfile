FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build pkg-config libzmq3-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY CMakeLists.txt ./
COPY native ./native
COPY vendor/libcsp ./vendor/libcsp
RUN cmake -G Ninja -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --target csp-lab-native zmqproxy

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends libzmq5 && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /app/build/csp-lab-native /usr/local/bin/csp-lab-native
COPY --from=build /app/build/vendor/libcsp/examples/zmqproxy /usr/local/bin/zmqproxy
COPY LICENSE /usr/share/licenses/csp-lab/LICENSE
COPY vendor/libcsp/LICENSE /usr/share/licenses/libcsp/LICENSE
COPY vendor/libcsp/AUTHORS /usr/share/licenses/libcsp/AUTHORS
COPY THIRD_PARTY_NOTICES.md /usr/share/licenses/csp-lab/THIRD_PARTY_NOTICES.md
ENTRYPOINT ["/usr/local/bin/csp-lab-native"]
