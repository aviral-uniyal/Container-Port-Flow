#include "scheduler.h"

#include <algorithm>
#include <numeric>
#include <set>
#include <tuple>

// ─── Input validation ─────────────────────────────────────────────────────────

void validate_processes(const std::vector<Process>& processes) {
    std::set<std::string> seen_ids;
    for (const auto& p : processes) {
        if (p.process_id.empty()) {
            throw std::invalid_argument("Process ID must not be empty.");
        }
        if (!seen_ids.insert(p.process_id).second) {
            throw std::invalid_argument(
                "Duplicate process ID: " + p.process_id);
        }
        if (p.arrival_time < 0) {
            throw std::invalid_argument(
                "Arrival time must be non-negative for process " +
                p.process_id);
        }
        if (p.burst_time <= 0) {
            throw std::invalid_argument(
                "Burst time must be positive for process " + p.process_id);
        }
    }
}

// ─── Helper: compute averages and build result ────────────────────────────────

static ScheduleResult build_result(
    std::vector<ProcessResult> results,
    std::vector<GanttEntry> gantt) {

    double avg_wt  = 0.0;
    double avg_tat = 0.0;
    if (!results.empty()) {
        for (const auto& r : results) {
            avg_wt  += r.waiting_time;
            avg_tat += r.turnaround_time;
        }
        avg_wt  /= static_cast<double>(results.size());
        avg_tat /= static_cast<double>(results.size());
    }
    return {std::move(results), std::move(gantt), avg_wt, avg_tat};
}

// ─── Generic non-preemptive scheduler ─────────────────────────────────────────
// The three algorithms differ only in how they rank waiting processes.
// `comparator` receives two Process references and returns true when the first
// should be scheduled before the second (strict weak ordering).

template <typename Compare>
static ScheduleResult schedule_generic(
    const std::vector<Process>& processes,
    Compare comparator) {

    if (processes.empty()) {
        return {{}, {}, 0.0, 0.0};
    }
    validate_processes(processes);

    const size_t n = processes.size();

    // Work on indices to avoid mutating input.
    std::vector<size_t> indices(n);
    std::iota(indices.begin(), indices.end(), 0);

    std::vector<bool> completed(n, false);
    std::vector<ProcessResult> results;
    results.reserve(n);
    std::vector<GanttEntry> gantt;

    int current_time = 0;
    size_t done = 0;

    while (done < n) {
        // Collect indices of processes that have arrived and are waiting.
        std::vector<size_t> ready;
        for (size_t i = 0; i < n; ++i) {
            if (!completed[i] && processes[i].arrival_time <= current_time) {
                ready.push_back(i);
            }
        }

        if (ready.empty()) {
            // CPU is idle — advance time to the next arrival.
            int next_arrival = std::numeric_limits<int>::max();
            for (size_t i = 0; i < n; ++i) {
                if (!completed[i]) {
                    next_arrival =
                        std::min(next_arrival, processes[i].arrival_time);
                }
            }
            gantt.push_back({"IDLE", current_time, next_arrival});
            current_time = next_arrival;
            continue;
        }

        // Pick the best candidate according to the algorithm's comparator.
        size_t best = ready[0];
        for (size_t k = 1; k < ready.size(); ++k) {
            if (comparator(processes[ready[k]], processes[best])) {
                best = ready[k];
            }
        }

        const Process& p = processes[best];
        int start  = current_time;
        int finish = start + p.burst_time;

        gantt.push_back({p.process_id, start, finish});

        ProcessResult pr;
        pr.process_id      = p.process_id;
        pr.arrival_time    = p.arrival_time;
        pr.burst_time      = p.burst_time;
        pr.priority        = p.priority;
        pr.completion_time = finish;
        pr.turnaround_time = finish - p.arrival_time;
        pr.waiting_time    = pr.turnaround_time - p.burst_time;
        results.push_back(std::move(pr));

        completed[best] = true;
        current_time = finish;
        ++done;
    }

    return build_result(std::move(results), std::move(gantt));
}

// ─── FCFS ─────────────────────────────────────────────────────────────────────
// Tie-break: earliest arrival time, then process ID (lexicographic).

ScheduleResult schedule_fcfs(const std::vector<Process>& processes) {
    return schedule_generic(processes,
        [](const Process& a, const Process& b) {
            return std::tie(a.arrival_time, a.process_id)
                 < std::tie(b.arrival_time, b.process_id);
        });
}

// ─── SJF (non-preemptive) ─────────────────────────────────────────────────────
// Tie-break: shortest burst, then earliest arrival, then process ID.

ScheduleResult schedule_sjf(const std::vector<Process>& processes) {
    return schedule_generic(processes,
        [](const Process& a, const Process& b) {
            return std::tie(a.burst_time, a.arrival_time, a.process_id)
                 < std::tie(b.burst_time, b.arrival_time, b.process_id);
        });
}

// ─── Priority (non-preemptive) ────────────────────────────────────────────────
// Tie-break: smallest priority number, then earliest arrival, then process ID.

ScheduleResult schedule_priority(const std::vector<Process>& processes) {
    return schedule_generic(processes,
        [](const Process& a, const Process& b) {
            return std::tie(a.priority, a.arrival_time, a.process_id)
                 < std::tie(b.priority, b.arrival_time, b.process_id);
        });
}
