// ─── Scheduler Test Suite ────────────────────────────────────────────────────
// Self-contained test harness — no external frameworks required.
// Compile:  g++ -std=c++17 -I../include scheduler_tests.cpp ../src/scheduler.cpp -o tests
// Run:      ./tests

#include "scheduler.h"

#include <cmath>
#include <iostream>
#include <set>
#include <sstream>
#include <string>
#include <vector>

// ─── Minimal test infrastructure ─────────────────────────────────────────────

static int g_total   = 0;
static int g_passed  = 0;
static int g_failed  = 0;

#define ASSERT_EQ(a, b) \
    do { \
        if ((a) != (b)) { \
            std::cerr << "  FAIL  " << __FILE__ << ":" << __LINE__ \
                      << "  expected " << (b) << ", got " << (a) << "\n"; \
            return false; \
        } \
    } while (0)

#define ASSERT_TRUE(cond) \
    do { \
        if (!(cond)) { \
            std::cerr << "  FAIL  " << __FILE__ << ":" << __LINE__ \
                      << "  condition false: " #cond "\n"; \
            return false; \
        } \
    } while (0)

#define ASSERT_NEAR(a, b, eps) \
    do { \
        if (std::fabs((a) - (b)) > (eps)) { \
            std::cerr << "  FAIL  " << __FILE__ << ":" << __LINE__ \
                      << "  expected ~" << (b) << ", got " << (a) << "\n"; \
            return false; \
        } \
    } while (0)

#define ASSERT_THROWS(expr) \
    do { \
        bool threw = false; \
        try { expr; } catch (const std::invalid_argument&) { threw = true; } \
        if (!threw) { \
            std::cerr << "  FAIL  " << __FILE__ << ":" << __LINE__ \
                      << "  expected exception not thrown\n"; \
            return false; \
        } \
    } while (0)

static void run_test(const char* name, bool (*fn)()) {
    ++g_total;
    std::cout << "  " << name << " ... ";
    if (fn()) {
        ++g_passed;
        std::cout << "PASS\n";
    } else {
        ++g_failed;
        std::cout << "FAIL\n";
    }
}

// ─── Consistency helpers ─────────────────────────────────────────────────────

// Verify every process executes exactly once, all times are consistent,
// and the Gantt chart is contiguous.
static bool verify_consistency(const ScheduleResult& res,
                               const std::vector<Process>& input) {
    // Same number of results.
    ASSERT_EQ(res.process_results.size(), input.size());

    // Every process present exactly once.
    std::set<std::string> ids;
    for (const auto& r : res.process_results) {
        ASSERT_TRUE(ids.insert(r.process_id).second);
    }
    for (const auto& p : input) {
        ASSERT_TRUE(ids.count(p.process_id) == 1);
    }

    // Per-process metric consistency.
    for (const auto& r : res.process_results) {
        ASSERT_EQ(r.turnaround_time, r.completion_time - r.arrival_time);
        ASSERT_EQ(r.waiting_time, r.turnaround_time - r.burst_time);
        ASSERT_TRUE(r.waiting_time >= 0);
        ASSERT_TRUE(r.completion_time > r.arrival_time ||
                    (r.completion_time == r.arrival_time + r.burst_time));
    }

    // Gantt chart contiguity.
    for (size_t i = 1; i < res.gantt_chart.size(); ++i) {
        ASSERT_EQ(res.gantt_chart[i].start_time,
                  res.gantt_chart[i - 1].finish_time);
    }

    // Average sanity.
    if (!res.process_results.empty()) {
        double sum_wt = 0, sum_tat = 0;
        for (const auto& r : res.process_results) {
            sum_wt  += r.waiting_time;
            sum_tat += r.turnaround_time;
        }
        ASSERT_NEAR(res.avg_waiting_time,
                    sum_wt / res.process_results.size(), 1e-6);
        ASSERT_NEAR(res.avg_turnaround_time,
                    sum_tat / res.process_results.size(), 1e-6);
    }
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 1: Basic FCFS example
// ═════════════════════════════════════════════════════════════════════════════
static bool test_fcfs_basic() {
    std::vector<Process> procs = {
        {"P1", 0, 4, 0},
        {"P2", 1, 3, 0},
        {"P3", 2, 1, 0},
    };
    auto res = schedule_fcfs(procs);
    if (!verify_consistency(res, procs)) return false;

    // Order: P1(0-4), P2(4-7), P3(7-8)
    ASSERT_EQ(res.gantt_chart.size(), 3u);
    ASSERT_EQ(res.gantt_chart[0].process_id, "P1");
    ASSERT_EQ(res.gantt_chart[0].start_time, 0);
    ASSERT_EQ(res.gantt_chart[0].finish_time, 4);

    ASSERT_EQ(res.gantt_chart[1].process_id, "P2");
    ASSERT_EQ(res.gantt_chart[1].start_time, 4);
    ASSERT_EQ(res.gantt_chart[1].finish_time, 7);

    ASSERT_EQ(res.gantt_chart[2].process_id, "P3");
    ASSERT_EQ(res.gantt_chart[2].start_time, 7);
    ASSERT_EQ(res.gantt_chart[2].finish_time, 8);

    // P1: CT=4 TAT=4 WT=0
    // P2: CT=7 TAT=6 WT=3
    // P3: CT=8 TAT=6 WT=5
    ASSERT_EQ(res.process_results[0].completion_time, 4);
    ASSERT_EQ(res.process_results[0].waiting_time, 0);
    ASSERT_EQ(res.process_results[1].completion_time, 7);
    ASSERT_EQ(res.process_results[1].waiting_time, 3);
    ASSERT_EQ(res.process_results[2].completion_time, 8);
    ASSERT_EQ(res.process_results[2].waiting_time, 5);

    ASSERT_NEAR(res.avg_waiting_time, 8.0 / 3.0, 1e-6);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 2: Basic SJF example
// ═════════════════════════════════════════════════════════════════════════════
static bool test_sjf_basic() {
    std::vector<Process> procs = {
        {"P1", 0, 6, 0},
        {"P2", 0, 2, 0},
        {"P3", 0, 8, 0},
        {"P4", 0, 3, 0},
    };
    auto res = schedule_sjf(procs);
    if (!verify_consistency(res, procs)) return false;

    // Order by burst: P2(2), P4(3), P1(6), P3(8)
    ASSERT_EQ(res.gantt_chart[0].process_id, "P2");
    ASSERT_EQ(res.gantt_chart[1].process_id, "P4");
    ASSERT_EQ(res.gantt_chart[2].process_id, "P1");
    ASSERT_EQ(res.gantt_chart[3].process_id, "P3");

    // P2: CT=2  TAT=2  WT=0
    // P4: CT=5  TAT=5  WT=2
    // P1: CT=11 TAT=11 WT=5
    // P3: CT=19 TAT=19 WT=11
    ASSERT_EQ(res.process_results[0].waiting_time, 0);   // P2
    ASSERT_EQ(res.process_results[1].waiting_time, 2);   // P4
    ASSERT_EQ(res.process_results[2].waiting_time, 5);   // P1
    ASSERT_EQ(res.process_results[3].waiting_time, 11);  // P3

    ASSERT_NEAR(res.avg_waiting_time, 18.0 / 4.0, 1e-6);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 3: Basic Priority example
// ═════════════════════════════════════════════════════════════════════════════
static bool test_priority_basic() {
    std::vector<Process> procs = {
        {"P1", 0, 4, 3},
        {"P2", 0, 3, 1},
        {"P3", 0, 5, 2},
    };
    auto res = schedule_priority(procs);
    if (!verify_consistency(res, procs)) return false;

    // Order by priority: P2(1), P3(2), P1(3)
    ASSERT_EQ(res.gantt_chart[0].process_id, "P2");
    ASSERT_EQ(res.gantt_chart[1].process_id, "P3");
    ASSERT_EQ(res.gantt_chart[2].process_id, "P1");

    ASSERT_EQ(res.process_results[0].waiting_time, 0);   // P2
    ASSERT_EQ(res.process_results[1].waiting_time, 3);   // P3
    ASSERT_EQ(res.process_results[2].waiting_time, 8);   // P1

    ASSERT_NEAR(res.avg_waiting_time, 11.0 / 3.0, 1e-6);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 4: Nonzero arrival times
// ═════════════════════════════════════════════════════════════════════════════
static bool test_nonzero_arrivals() {
    std::vector<Process> procs = {
        {"A", 2, 3, 0},
        {"B", 5, 2, 0},
    };
    auto res = schedule_fcfs(procs);
    if (!verify_consistency(res, procs)) return false;

    // Idle 0-2, A 2-5, B 5-7
    ASSERT_EQ(res.gantt_chart.size(), 3u);
    ASSERT_EQ(res.gantt_chart[0].process_id, "IDLE");
    ASSERT_EQ(res.gantt_chart[0].start_time, 0);
    ASSERT_EQ(res.gantt_chart[0].finish_time, 2);
    ASSERT_EQ(res.gantt_chart[1].process_id, "A");
    ASSERT_EQ(res.gantt_chart[2].process_id, "B");
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 5: CPU idle before first process AND between processes
// ═════════════════════════════════════════════════════════════════════════════
static bool test_idle_gaps() {
    std::vector<Process> procs = {
        {"X", 3, 2, 0},
        {"Y", 10, 1, 0},
    };
    auto res = schedule_fcfs(procs);
    if (!verify_consistency(res, procs)) return false;

    // Idle 0-3, X 3-5, Idle 5-10, Y 10-11
    ASSERT_EQ(res.gantt_chart.size(), 4u);
    ASSERT_EQ(res.gantt_chart[0].process_id, "IDLE");
    ASSERT_EQ(res.gantt_chart[0].start_time, 0);
    ASSERT_EQ(res.gantt_chart[0].finish_time, 3);
    ASSERT_EQ(res.gantt_chart[1].process_id, "X");
    ASSERT_EQ(res.gantt_chart[1].finish_time, 5);
    ASSERT_EQ(res.gantt_chart[2].process_id, "IDLE");
    ASSERT_EQ(res.gantt_chart[2].start_time, 5);
    ASSERT_EQ(res.gantt_chart[2].finish_time, 10);
    ASSERT_EQ(res.gantt_chart[3].process_id, "Y");
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 6: Multiple processes arriving simultaneously
// ═════════════════════════════════════════════════════════════════════════════
static bool test_simultaneous_arrival_fcfs() {
    // All arrive at 0; FCFS tie-break is by process_id.
    std::vector<Process> procs = {
        {"C", 0, 5, 0},
        {"A", 0, 3, 0},
        {"B", 0, 4, 0},
    };
    auto res = schedule_fcfs(procs);
    if (!verify_consistency(res, procs)) return false;

    // Alphabetical order: A, B, C
    ASSERT_EQ(res.gantt_chart[0].process_id, "A");
    ASSERT_EQ(res.gantt_chart[1].process_id, "B");
    ASSERT_EQ(res.gantt_chart[2].process_id, "C");
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 7: Equal burst times (SJF tie-break by arrival then ID)
// ═════════════════════════════════════════════════════════════════════════════
static bool test_sjf_equal_bursts() {
    std::vector<Process> procs = {
        {"P3", 0, 4, 0},
        {"P1", 0, 4, 0},
        {"P2", 1, 4, 0},
    };
    auto res = schedule_sjf(procs);
    if (!verify_consistency(res, procs)) return false;

    // All burst=4. At t=0: P1 and P3 ready; tie-break AT then ID → P1 first.
    // At t=4: P3 and P2 ready; P3 arrived at 0, P2 at 1 → P3 next.
    // At t=8: P2.
    ASSERT_EQ(res.gantt_chart[0].process_id, "P1");
    ASSERT_EQ(res.gantt_chart[1].process_id, "P3");
    ASSERT_EQ(res.gantt_chart[2].process_id, "P2");
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 8: Equal priorities (Priority tie-break by arrival then ID)
// ═════════════════════════════════════════════════════════════════════════════
static bool test_priority_equal_priorities() {
    std::vector<Process> procs = {
        {"Z", 0, 2, 5},
        {"A", 0, 3, 5},
        {"M", 1, 1, 5},
    };
    auto res = schedule_priority(procs);
    if (!verify_consistency(res, procs)) return false;

    // Same priority=5.  At t=0 ready: Z(AT=0) and A(AT=0); tie by ID → A first.
    // At t=3: Z(AT=0) and M(AT=1) ready; Z has earlier AT → Z next.
    // At t=5: M.
    ASSERT_EQ(res.gantt_chart[0].process_id, "A");
    ASSERT_EQ(res.gantt_chart[1].process_id, "Z");
    ASSERT_EQ(res.gantt_chart[2].process_id, "M");
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 9: SJF and Priority produce DIFFERENT orders
// ═════════════════════════════════════════════════════════════════════════════
static bool test_sjf_vs_priority_differ() {
    // P1: burst=5, priority=1 (high)
    // P2: burst=2, priority=3 (low)
    // SJF picks P2 first (shorter burst); Priority picks P1 first (higher pri).
    std::vector<Process> procs = {
        {"P1", 0, 5, 1},
        {"P2", 0, 2, 3},
    };

    auto sjf_res = schedule_sjf(procs);
    auto pri_res = schedule_priority(procs);

    ASSERT_EQ(sjf_res.gantt_chart[0].process_id, "P2");
    ASSERT_EQ(sjf_res.gantt_chart[1].process_id, "P1");

    ASSERT_EQ(pri_res.gantt_chart[0].process_id, "P1");
    ASSERT_EQ(pri_res.gantt_chart[1].process_id, "P2");

    // Confirm they are truly different.
    ASSERT_TRUE(sjf_res.gantt_chart[0].process_id !=
                pri_res.gantt_chart[0].process_id);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 10: Correct CT, TAT, WT, and averages — detailed check
// ═════════════════════════════════════════════════════════════════════════════
static bool test_detailed_metrics() {
    std::vector<Process> procs = {
        {"P1", 0, 6, 3},
        {"P2", 1, 8, 1},
        {"P3", 2, 7, 4},
        {"P4", 3, 3, 2},
    };

    // ── FCFS ──
    // Order: P1(0-6), P2(6-14), P3(14-21), P4(21-24)
    auto fcfs = schedule_fcfs(procs);
    if (!verify_consistency(fcfs, procs)) return false;
    ASSERT_EQ(fcfs.process_results[0].completion_time, 6);   // P1
    ASSERT_EQ(fcfs.process_results[1].completion_time, 14);  // P2
    ASSERT_EQ(fcfs.process_results[2].completion_time, 21);  // P3
    ASSERT_EQ(fcfs.process_results[3].completion_time, 24);  // P4
    // WT: P1=0, P2=5, P3=12, P4=18 → avg=35/4=8.75
    ASSERT_NEAR(fcfs.avg_waiting_time, 8.75, 1e-6);
    // TAT: 6,13,19,21 → avg=59/4=14.75
    ASSERT_NEAR(fcfs.avg_turnaround_time, 14.75, 1e-6);

    // ── SJF ──
    // t=0: only P1 ready → P1 runs 0-6.
    // t=6: P2(BT=8), P3(BT=7), P4(BT=3) ready → P4 first.
    // t=9: P2(8), P3(7) → P3 first.
    // t=16: P2 → runs 16-24.
    auto sjf = schedule_sjf(procs);
    if (!verify_consistency(sjf, procs)) return false;
    ASSERT_EQ(sjf.gantt_chart[0].process_id, "P1");
    ASSERT_EQ(sjf.gantt_chart[1].process_id, "P4");
    ASSERT_EQ(sjf.gantt_chart[2].process_id, "P3");
    ASSERT_EQ(sjf.gantt_chart[3].process_id, "P2");
    // WT: P1=0, P4=3, P3=7, P2=15 → avg=25/4=6.25
    ASSERT_NEAR(sjf.avg_waiting_time, 6.25, 1e-6);

    // ── Priority ──
    // t=0: only P1 (pri=3) ready → P1 runs 0-6.
    // t=6: P2(pri=1), P3(pri=4), P4(pri=2) → P2 first (pri=1).
    // t=14: P3(pri=4), P4(pri=2) → P4 (pri=2).
    // t=17: P3.
    auto pri = schedule_priority(procs);
    if (!verify_consistency(pri, procs)) return false;
    ASSERT_EQ(pri.gantt_chart[0].process_id, "P1");
    ASSERT_EQ(pri.gantt_chart[1].process_id, "P2");
    ASSERT_EQ(pri.gantt_chart[2].process_id, "P4");
    ASSERT_EQ(pri.gantt_chart[3].process_id, "P3");
    // WT: P1=0, P2=5, P4=11, P3=15 → avg=31/4=7.75
    ASSERT_NEAR(pri.avg_waiting_time, 7.75, 1e-6);

    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 11: Invalid inputs
// ═════════════════════════════════════════════════════════════════════════════
static bool test_invalid_empty_id() {
    std::vector<Process> procs = {{"", 0, 1, 0}};
    ASSERT_THROWS(schedule_fcfs(procs));
    return true;
}

static bool test_invalid_duplicate_id() {
    std::vector<Process> procs = {
        {"P1", 0, 1, 0},
        {"P1", 1, 2, 0},
    };
    ASSERT_THROWS(schedule_fcfs(procs));
    return true;
}

static bool test_invalid_negative_arrival() {
    std::vector<Process> procs = {{"P1", -1, 1, 0}};
    ASSERT_THROWS(schedule_sjf(procs));
    return true;
}

static bool test_invalid_zero_burst() {
    std::vector<Process> procs = {{"P1", 0, 0, 0}};
    ASSERT_THROWS(schedule_priority(procs));
    return true;
}

static bool test_invalid_negative_burst() {
    std::vector<Process> procs = {{"P1", 0, -3, 0}};
    ASSERT_THROWS(schedule_fcfs(procs));
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 12: Empty input
// ═════════════════════════════════════════════════════════════════════════════
static bool test_empty_input() {
    std::vector<Process> empty;

    auto r1 = schedule_fcfs(empty);
    ASSERT_EQ(r1.process_results.size(), 0u);
    ASSERT_EQ(r1.gantt_chart.size(), 0u);
    ASSERT_NEAR(r1.avg_waiting_time, 0.0, 1e-9);

    auto r2 = schedule_sjf(empty);
    ASSERT_EQ(r2.process_results.size(), 0u);

    auto r3 = schedule_priority(empty);
    ASSERT_EQ(r3.process_results.size(), 0u);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 13: Single process
// ═════════════════════════════════════════════════════════════════════════════
static bool test_single_process() {
    std::vector<Process> procs = {{"Solo", 5, 3, 7}};
    auto res = schedule_fcfs(procs);
    if (!verify_consistency(res, procs)) return false;

    // Idle 0-5, Solo 5-8
    ASSERT_EQ(res.gantt_chart.size(), 2u);
    ASSERT_EQ(res.gantt_chart[0].process_id, "IDLE");
    ASSERT_EQ(res.gantt_chart[0].start_time, 0);
    ASSERT_EQ(res.gantt_chart[0].finish_time, 5);
    ASSERT_EQ(res.gantt_chart[1].process_id, "Solo");
    ASSERT_EQ(res.gantt_chart[1].start_time, 5);
    ASSERT_EQ(res.gantt_chart[1].finish_time, 8);

    ASSERT_EQ(res.process_results[0].completion_time, 8);
    ASSERT_EQ(res.process_results[0].turnaround_time, 3);
    ASSERT_EQ(res.process_results[0].waiting_time, 0);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 14: Input vector is not mutated
// ═════════════════════════════════════════════════════════════════════════════
static bool test_input_not_mutated() {
    std::vector<Process> procs = {
        {"P1", 0, 4, 2},
        {"P2", 1, 2, 1},
    };
    // Take a copy to compare.
    auto original = procs;

    schedule_fcfs(procs);
    schedule_sjf(procs);
    schedule_priority(procs);

    ASSERT_EQ(procs.size(), original.size());
    for (size_t i = 0; i < procs.size(); ++i) {
        ASSERT_EQ(procs[i].process_id, original[i].process_id);
        ASSERT_EQ(procs[i].arrival_time, original[i].arrival_time);
        ASSERT_EQ(procs[i].burst_time, original[i].burst_time);
        ASSERT_EQ(procs[i].priority, original[i].priority);
    }
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// Test 15: SJF with staggered arrivals (processes arrive during execution)
// ═════════════════════════════════════════════════════════════════════════════
static bool test_sjf_staggered() {
    std::vector<Process> procs = {
        {"P1", 0, 7, 0},
        {"P2", 2, 4, 0},
        {"P3", 4, 1, 0},
        {"P4", 5, 4, 0},
    };
    auto res = schedule_sjf(procs);
    if (!verify_consistency(res, procs)) return false;

    // t=0: only P1 → runs 0-7.
    // t=7: P2(BT=4), P3(BT=1), P4(BT=4) ready. Shortest = P3.
    // t=8: P2(BT=4), P4(BT=4). Tie on BT → earliest AT → P2(AT=2) before P4(AT=5).
    // t=12: P4.
    ASSERT_EQ(res.gantt_chart[0].process_id, "P1");
    ASSERT_EQ(res.gantt_chart[1].process_id, "P3");
    ASSERT_EQ(res.gantt_chart[2].process_id, "P2");
    ASSERT_EQ(res.gantt_chart[3].process_id, "P4");
    ASSERT_EQ(res.gantt_chart[3].finish_time, 16);
    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// main
// ═════════════════════════════════════════════════════════════════════════════

int main() {
    std::cout << "\n=== Scheduler Test Suite ===\n\n";

    run_test("FCFS basic",                     test_fcfs_basic);
    run_test("SJF basic",                      test_sjf_basic);
    run_test("Priority basic",                 test_priority_basic);
    run_test("Nonzero arrival times",          test_nonzero_arrivals);
    run_test("Idle gaps (before + between)",   test_idle_gaps);
    run_test("Simultaneous arrival (FCFS)",    test_simultaneous_arrival_fcfs);
    run_test("SJF equal bursts tie-break",     test_sjf_equal_bursts);
    run_test("Priority equal priorities",      test_priority_equal_priorities);
    run_test("SJF vs Priority differ",         test_sjf_vs_priority_differ);
    run_test("Detailed metrics (all algos)",   test_detailed_metrics);
    run_test("Invalid: empty ID",              test_invalid_empty_id);
    run_test("Invalid: duplicate ID",          test_invalid_duplicate_id);
    run_test("Invalid: negative arrival",      test_invalid_negative_arrival);
    run_test("Invalid: zero burst",            test_invalid_zero_burst);
    run_test("Invalid: negative burst",        test_invalid_negative_burst);
    run_test("Empty input",                    test_empty_input);
    run_test("Single process",                 test_single_process);
    run_test("Input not mutated",              test_input_not_mutated);
    run_test("SJF staggered arrivals",         test_sjf_staggered);

    std::cout << "\n─── Summary ────────────────────────\n";
    std::cout << "  Total:  " << g_total  << "\n";
    std::cout << "  Passed: " << g_passed << "\n";
    std::cout << "  Failed: " << g_failed << "\n";
    std::cout << "────────────────────────────────────\n\n";

    return g_failed == 0 ? 0 : 1;
}
