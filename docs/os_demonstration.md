# OS demonstration reference

Use this sequence to explain the existing C++ OS simulation during a project evaluation. The dashboard uses the live `PortDispatcher` through FastAPI; the scheduler demo and deadlock/Banker's Algorithm tests are separate C++ exercises.

## Live dashboard

On the configured Windows development machine, start the application with `start_all.bat`. Use **Reset** before each new scenario and select a different available ship for each job. Reset clears the current simulation and releases its resources while keeping PostgreSQL history.

1. **Scheduling:** Initialize with one berth, one crane, and the selected policy; register one inventory crane. Turn on **Batch Mode**, submit two jobs with arrival time `0`, then click **Dispatch All**. For FCFS, submit the earlier job first; for SJF, give the later submitted job less total work; for Priority, give it a lower priority number. Compare the dispatch/queue state after each scenario. Repeat after Reset for each policy. The jobs table establishes its display order on submission and keeps it as execution changes; the live dispatch and queue state show actual scheduling decisions.
2. **Arrival and clock:** Submit a job with arrival time `2`. Use **Step +1** to advance simulated time and observe that it is ineligible until its arrival tick. Arrival times are simulated ticks, not historical dates. **Run** advances steps automatically; **Pause** stops automatic stepping and **Run** resumes it.
3. **Concurrent jobs and resources:** Initialize with at least two berths and two cranes, register two cranes, then submit two arrival-`0` jobs that each request one berth and one crane. Step or Run to show both jobs active and progressing, with two allocations visible in the resource summary. On completion, their resources and crane assignments are released. A completed workload leaves the run open for more submissions until Reset.
4. **Failure and recovery:** While a job uses a crane, fail that crane in the Crane Registry. Show the affected job's interruption and the crane's failed state; recover the crane and continue execution. Check the job and resource summary after recovery. Reset also stops execution and releases current allocations.

**Batch Mode** holds submitted jobs pending until **Dispatch All**. Without it, eligible jobs may dispatch as they are submitted, so an already active job is not preempted merely because a shorter or higher-priority job arrives.

## Standalone OS exercises

The repository's `os-simulation/CMakeLists.txt` defines these existing executables. With CMake and a C++14 compiler installed, build and run them from the repository root:

```text
cmake -S os-simulation -B os-simulation/build
cmake --build os-simulation/build
.\os-simulation\build\scheduler_demo.exe
.\os-simulation\build\deadlock_manager_tests.exe
```

`scheduler_demo` prints FCFS, non-preemptive SJF, and non-preemptive Priority examples with completion, waiting, turnaround, and Gantt results. `deadlock_manager_tests` exercises deadlock detection and Banker's safety/resource-request checks, including safe and unsafe states. These checks do **not** participate in live `PortDispatcher` allocation. For the live dispatcher, JSON interface, resources, registry, and disruptions, run the corresponding CMake test targets (`port_dispatcher_tests`, `port_json_iface_tests`, `resource_manager_tests`, `crane_registry_tests`, and `disruption_manager_tests`) or run `ctest` from the build directory.
