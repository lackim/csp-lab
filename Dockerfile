FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS native-source
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build patch pkg-config libzmq3-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY CMakeLists.txt ./
COPY native ./native
COPY patches ./patches
COPY vendor/libcsp ./vendor/libcsp

FROM native-source AS build
RUN cmake -G Ninja -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --target csp-lab-native zmqproxy

FROM native-source AS native-tests
COPY test/native-bounds ./test/native-bounds
RUN cmake -G Ninja -B build -DCMAKE_BUILD_TYPE=Debug -DCSP_LAB_BUILD_TESTS=ON \
    -DCMAKE_C_FLAGS='-fsanitize=address -fno-omit-frame-pointer' \
    -DCMAKE_EXE_LINKER_FLAGS='-fsanitize=address' && \
    cmake --build build --target csp-lab-frame-tests csp-lab-frame-injector csp-lab-native zmqproxy
ENV ASAN_OPTIONS=detect_leaks=0:abort_on_error=1
ENTRYPOINT ["bash", "/app/test/native-bounds/run.sh"]

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends libzmq5 && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /app/build/csp-lab-native /usr/local/bin/csp-lab-native
COPY --from=build /app/build/vendor/libcsp/examples/zmqproxy /usr/local/bin/zmqproxy
COPY LICENSE /usr/share/licenses/csp-lab/LICENSE
COPY vendor/libcsp/LICENSE /usr/share/licenses/libcsp/LICENSE
COPY vendor/libcsp/AUTHORS /usr/share/licenses/libcsp/AUTHORS
COPY patches/libcsp /usr/share/licenses/libcsp/patches
COPY native/zmq_frame.h /usr/share/licenses/libcsp/patches/csp_lab_zmq_frame.h
COPY THIRD_PARTY_NOTICES.md /usr/share/licenses/csp-lab/THIRD_PARTY_NOTICES.md
USER 10001
ENTRYPOINT ["/usr/local/bin/csp-lab-native"]
