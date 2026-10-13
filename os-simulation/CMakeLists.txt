cmake_minimum_required(VERSION 3.10)
project(os-simulation LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ─── Include path ────────────────────────────────────────────────────────────
include_directories(${CMAKE_SOURCE_DIR}/include)

# ─── Library (scheduler logic) ───────────────────────────────────────────────
add_library(scheduler_lib src/scheduler.cpp)

# ─── Library (resource manager logic) ────────────────────────────────────────
add_library(resource_manager_lib src/resource_manager.cpp)

# ─── Main demo executable ───────────────────────────────────────────────────
add_executable(scheduler_demo src/main.cpp)
target_link_libraries(scheduler_demo PRIVATE scheduler_lib)

# ─── Test executables ────────────────────────────────────────────────────────
enable_testing()

add_executable(scheduler_tests tests/scheduler_tests.cpp)
target_link_libraries(scheduler_tests PRIVATE scheduler_lib)
add_test(NAME SchedulerTests COMMAND scheduler_tests)

add_executable(resource_manager_tests tests/resource_manager_tests.cpp)
target_link_libraries(resource_manager_tests PRIVATE resource_manager_lib)
add_test(NAME ResourceManagerTests COMMAND resource_manager_tests)

# ─── Library (deadlock manager logic) ────────────────────────────────────────
add_library(deadlock_manager_lib src/deadlock_manager.cpp)
target_link_libraries(deadlock_manager_lib PUBLIC resource_manager_lib)

add_executable(deadlock_manager_tests tests/deadlock_manager_tests.cpp)
target_link_libraries(deadlock_manager_tests PRIVATE deadlock_manager_lib)
add_test(NAME DeadlockManagerTests COMMAND deadlock_manager_tests)