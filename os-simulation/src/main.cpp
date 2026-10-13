#include "scheduler.h"
#include <iostream>
#include <iomanip>

// ─── Pretty-print helpers ─────────────────────────────────────────────────────

static void print_gantt(const std::vector<GanttEntry>& gantt) {
    std::cout << "  Gantt chart: ";
    for (const auto& g : gantt) {
        std::cout << "[" << g.process_id
                  << " " << g.start_time << "-" << g.finish_time << "] ";
    }
    std::cout << "\n";
}

static void print_results(const ScheduleResult& res) {
    std::cout << std::left
              << std::setw(10) << "PID"
              << std::setw(6)  << "AT"
              << std::setw(6)  << "BT"
              << std::setw(6)  << "Pri"
              << std::setw(6)  << "CT"
              << std::setw(6)  << "TAT"
              << std::setw(6)  << "WT"
              << "\n";
    std::cout << std::string(46, '-') << "\n";
    for (const auto& r : res.process_results) {
        std::cout << std::setw(10) << r.process_id
                  << std::setw(6)  << r.arrival_time
                  << std::setw(6)  << r.burst_time
                  << std::setw(6)  << r.priority
                  << std::setw(6)  << r.completion_time
                  << std::setw(6)  << r.turnaround_time
                  << std::setw(6)  << r.waiting_time
                  << "\n";
    }
    std::cout << std::fixed << std::setprecision(2);
    std::cout << "  Avg WT  = " << res.avg_waiting_time    << "\n";
    std::cout << "  Avg TAT = " << res.avg_turnaround_time << "\n";
    print_gantt(res.gantt_chart);
    std::cout << "\n";
}

// ─── main ─────────────────────────────────────────────────────────────────────

int main() {
    // Example process set (same as used in the textbook-style demos).
    std::vector<Process> procs = {
        {"P1", 0, 6, 3},
        {"P2", 1, 8, 1},
        {"P3", 2, 7, 4},
        {"P4", 3, 3, 2},
    };

    std::cout << "========== FCFS ==========\n";
    print_results(schedule_fcfs(procs));

    std::cout << "========== SJF (non-preemptive) ==========\n";
    print_results(schedule_sjf(procs));

    std::cout << "========== Priority (non-preemptive) ==========\n";
    print_results(schedule_priority(procs));

    return 0;
}
