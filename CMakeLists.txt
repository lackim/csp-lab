cmake_minimum_required(VERSION 3.20)
project(csp_lab C)
find_package(Threads REQUIRED)

set(CSP_USE_RTABLE ON CACHE BOOL "Enable libcsp routing" FORCE)
set(CSP_BUILD_SAMPLES ON CACHE BOOL "Build libcsp ZMQ proxy" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Build static libcsp" FORCE)
add_subdirectory(vendor/libcsp)

add_executable(csp-lab-native native/main.c)
target_include_directories(csp-lab-native PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/vendor/libcsp/include vendor/libcsp/include)
target_link_libraries(csp-lab-native PRIVATE csp csp_common Threads::Threads)
