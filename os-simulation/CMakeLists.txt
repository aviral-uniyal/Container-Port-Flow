cmake_minimum_required(VERSION 3.10)
project(os-simulation LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ─── Include path ────────────────────────────────────────────────────────────
include_directories(${CMAKE_SOURCE_DIR}/include)

# ─── Library (scheduler logic) ───────────────────────────────────────────────
add_library(scheduler_lib src/scheduler.cpp)

# ─── Main demo executable ───────────────────────────────────────────────────
add_executable(scheduler_demo src/main.cpp)
target_link_libraries(scheduler_demo PRIVATE scheduler_lib)

# ─── Test executable ─────────────────────────────────────────────────────────
enable_testing()
add_executable(scheduler_tests tests/scheduler_tests.cpp)
target_link_libraries(scheduler_tests PRIVATE scheduler_lib)
add_test(NAME SchedulerTests COMMAND scheduler_tests)
