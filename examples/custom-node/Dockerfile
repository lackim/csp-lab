# Build from the repository root: docker build -f examples/custom-node/Dockerfile .
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build patch pkg-config libzmq3-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY CMakeLists.txt ./
COPY native ./native
COPY patches ./patches
COPY vendor/libcsp ./vendor/libcsp
COPY examples/custom-node ./examples/custom-node
ARG TESTING=OFF
ARG INCORRECT=OFF
RUN cmake -G Ninja -S examples/custom-node -B build -DCMAKE_BUILD_TYPE=Release \
    -DCSP_LAB_EXAMPLE_TESTING=${TESTING} -DCSP_LAB_EXAMPLE_INCORRECT=${INCORRECT} && \
    cmake --build build --target uppercase-node

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends libzmq5 && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /app/build/uppercase-node /usr/local/bin/uppercase-node
COPY LICENSE /usr/share/licenses/csp-lab/LICENSE
COPY vendor/libcsp/LICENSE /usr/share/licenses/libcsp/LICENSE
COPY vendor/libcsp/AUTHORS /usr/share/licenses/libcsp/AUTHORS
COPY patches/libcsp /usr/share/licenses/libcsp/patches
COPY native/zmq_frame.h /usr/share/licenses/libcsp/patches/csp_lab_zmq_frame.h
COPY THIRD_PARTY_NOTICES.md /usr/share/licenses/csp-lab/THIRD_PARTY_NOTICES.md
USER 10001
ENTRYPOINT ["/usr/local/bin/uppercase-node"]
CMD ["serve"]
