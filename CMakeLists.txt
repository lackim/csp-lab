cmake_minimum_required(VERSION 3.20)
project(csp_lab C)
if(CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_CURRENT_BINARY_DIR)
  message(FATAL_ERROR "Use an out-of-source build, for example cmake -B build")
endif()
find_package(Threads REQUIRED)

set(CSP_USE_RTABLE ON CACHE BOOL "Enable libcsp routing" FORCE)
set(CSP_BUILD_SAMPLES ON CACHE BOOL "Build libcsp ZMQ proxy" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Build static libcsp" FORCE)

# Patch a disposable build-tree copy, never the pinned source submodule.
find_program(CSP_LAB_PATCH patch REQUIRED)
set(CSP_LAB_LIBCSP_SOURCE "${CMAKE_CURRENT_BINARY_DIR}/_deps/libcsp-src")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/patches/libcsp/0001-zmq-frame-bounds.patch")
file(REMOVE_RECURSE "${CSP_LAB_LIBCSP_SOURCE}")
file(COPY "${CMAKE_CURRENT_SOURCE_DIR}/vendor/libcsp/"
     DESTINATION "${CSP_LAB_LIBCSP_SOURCE}" PATTERN ".git" EXCLUDE)
execute_process(
  COMMAND "${CSP_LAB_PATCH}" --batch -p1 -i "${CMAKE_CURRENT_SOURCE_DIR}/patches/libcsp/0001-zmq-frame-bounds.patch"
  WORKING_DIRECTORY "${CSP_LAB_LIBCSP_SOURCE}"
  COMMAND_ERROR_IS_FATAL ANY)
configure_file(native/zmq_frame.h
  "${CSP_LAB_LIBCSP_SOURCE}/include/csp/interfaces/csp_lab_zmq_frame.h" COPYONLY)
add_subdirectory("${CSP_LAB_LIBCSP_SOURCE}" vendor/libcsp)

add_executable(csp-lab-native native/main.c)
target_link_libraries(csp-lab-native PRIVATE csp csp_common Threads::Threads)

option(CSP_LAB_BUILD_TESTS "Build native frame bounds tests" OFF)
if(CSP_LAB_BUILD_TESTS)
  add_executable(csp-lab-frame-tests test/native-bounds/frame_copy.c)
  target_include_directories(csp-lab-frame-tests PRIVATE native)
  target_link_libraries(csp-lab-frame-tests PRIVATE csp csp_common Threads::Threads)
  add_executable(csp-lab-frame-injector test/native-bounds/inject.c)
  target_link_libraries(csp-lab-frame-injector PRIVATE csp csp_common Threads::Threads zmq)
endif()
