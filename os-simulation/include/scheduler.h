#ifndef SCHEDULER_H
#define SCHEDULER_H

#include <string>
#include <vector>
#include <stdexcept>

// ─── Process model ────────────────────────────────────────────────────────────
// Represents a single process/job submitted to the scheduler.
// All fields are immutable once constructed; the scheduler never mutates these.
struct Process {
    std::string process_id;   // unique, non-empty identifier
    int arrival_time;         // >= 0
    int burst_time;           // > 0
    int priority;             // lower value  ⇒  higher priority
};

// ─── Per-process result ───────────────────────────────────────────────────────
struct ProcessResult {
    std::string process_id;
    int arrival_time;
    int burst_time;
    int priority;
    int completion_time;      // CT
    int turnaround_time;      // TAT = CT - AT
    int waiting_time;         // WT  = TAT - BT
};

// ─── Gantt-chart entry ────────────────────────────────────────────────────────
// An entry with process_id == "IDLE" represents a CPU idle interval.
struct GanttEntry {
    std::string process_id;   // "IDLE" for idle intervals
    int start_time;
    int finish_time;
};

// ─── Aggregate scheduling result ──────────────────────────────────────────────
struct ScheduleResult {
    std::vector<ProcessResult> process_results;
    std::vector<GanttEntry>    gantt_chart;
    double avg_waiting_time;
    double avg_turnaround_time;
};

// ─── Validation ───────────────────────────────────────────────────────────────
// Throws std::invalid_argument on:
//   • empty process_id
//   • duplicate process_id
//   • negative arrival_time
//   • non-positive burst_time
void validate_processes(const std::vector<Process>& processes);

// ─── Scheduling algorithms ───────────────────────────────────────────────────
// Each function returns a ScheduleResult and never mutates the input vector.

ScheduleResult schedule_fcfs(const std::vector<Process>& processes);
ScheduleResult schedule_sjf(const std::vector<Process>& processes);
ScheduleResult schedule_priority(const std::vector<Process>& processes);

#endif // SCHEDULER_H
