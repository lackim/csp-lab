FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build pkg-config libzmq3-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY CMakeLists.txt ./
COPY native ./native
COPY vendor/libcsp ./vendor/libcsp
RUN cmake -G Ninja -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --target csp-lab-native zmqproxy

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libzmq5 && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /app/build/csp-lab-native /usr/local/bin/csp-lab-native
COPY --from=build /app/build/vendor/libcsp/examples/zmqproxy /usr/local/bin/zmqproxy
ENTRYPOINT ["/usr/local/bin/csp-lab-native"]
