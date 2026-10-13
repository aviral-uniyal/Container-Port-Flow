// ─── Deadlock Manager Test Suite ─────────────────────────────────────────────
// Self-contained test harness — no external frameworks required.
// Compile:  g++ -std=c++14 -I../include deadlock_manager_tests.cpp ../src/deadlock_manager.cpp -o deadlock_manager_tests
// Run:      ./deadlock_manager_tests

#include "deadlock_manager.h"

#include <algorithm>
#include <iostream>
#include <set>
#include <string>
#include <vector>

// ─── Minimal test infrastructure ─────────────────────────────────────────────
// Mirrors conventions from scheduler_tests.cpp and resource_manager_tests.cpp.

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

#define ASSERT_FALSE(cond) \
    do { \
        if ((cond)) { \
            std::cerr << "  FAIL  " << __FILE__ << ":" << __LINE__ \
                      << "  condition true: " #cond "\n"; \
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

// ═════════════════════════════════════════════════════════════════════════════
//  All test fixtures use synthetic capacities.
//
//  Classic Banker's textbook example adapted to port resources:
//    Total:     (10 berths, 5 cranes, 7 trucks)
//    5 jobs with allocations and max needs as specified per test.
// ═════════════════════════════════════════════════════════════════════════════

// ─── Helper: validate that a safe sequence is actually valid ─────────────────
// Simulates the Banker's finish-order and confirms every job can finish.
static bool validate_safe_sequence(const BankerState& state,
                                   const std::vector<std::string>& seq) {
    if (seq.size() != state.jobs.size()) return false;

    // Build index map.
    std::vector<size_t> order;
    for (const auto& id : seq) {
        bool found = false;
        for (size_t i = 0; i < state.jobs.size(); ++i) {
            if (state.jobs[i].job_id == id) {
                order.push_back(i);
                found = true;
                break;
            }
        }
        if (!found) return false;
    }

    ResourceSet work = state.available;
    for (size_t idx : order) {
        const auto& job = state.jobs[idx];
        ResourceSet need = {job.max_need.berths - job.allocation.berths,
                            job.max_need.cranes - job.allocation.cranes,
                            job.max_need.trucks - job.allocation.trucks};
        if (need.berths > work.berths ||
            need.cranes > work.cranes ||
            need.trucks > work.trucks) {
            return false;
        }
        work.berths += job.allocation.berths;
        work.cranes += job.allocation.cranes;
        work.trucks += job.allocation.trucks;
    }
    return true;
}


// ═════════════════════════════════════════════════════════════════════════════
// BANKER'S SAFETY ALGORITHM TESTS
// ═════════════════════════════════════════════════════════════════════════════

// ─── Test 1: Known safe state with valid safe sequence ───────────────────────
//
// Synthetic fixture (3 resource types: berths, cranes, trucks):
//   Total:     (10, 5, 7)
//   Available: ( 3, 3, 2)
//
//   Job  Alloc(B,C,T)  Max(B,C,T)  Need(B,C,T)
//   V0    (0,1,0)       (7,5,3)     (7,4,3)
//   V1    (2,0,0)       (3,2,2)     (1,2,2)
//   V2    (3,0,2)       (9,0,2)     (6,0,0)
//   V3    (2,1,1)       (2,2,2)     (0,1,1)
//   V4    (0,0,2)       (4,3,3)     (4,3,1)
//
// sum(alloc) = (7,2,5), available = (3,3,2), total = (10,5,7). ✓
//
// Safe sequence: V1 can go (need(1,2,2) <= work(3,3,2)),
//   work becomes (5,3,2); V3 need(0,1,1) <= (5,3,2) → work(7,4,3);
//   V4 need(4,3,1) <= (7,4,3) → work(7,4,5);
//   V2 need(6,0,0) <= (7,4,5) → work(10,4,7);
//   V0 need(7,4,3) <= (10,4,7) → done.
// One valid sequence: <V1, V3, V4, V2, V0>.

static bool test_safety_known_safe() {
    BankerState state;
    state.total     = {10, 5, 7};
    state.available = { 3, 3, 2};
    state.jobs = {
        {"V0", {0,1,0}, {7,5,3}},
        {"V1", {2,0,0}, {3,2,2}},
        {"V2", {3,0,2}, {9,0,2}},
        {"V3", {2,1,1}, {2,2,2}},
        {"V4", {0,0,2}, {4,3,3}},
    };

    auto result = check_safety(state);
    ASSERT_TRUE(result.is_safe);
    ASSERT_EQ(result.safe_sequence.size(), 5u);

    // Verify the returned sequence is actually valid.
    ASSERT_TRUE(validate_safe_sequence(state, result.safe_sequence));

    return true;
}

// ─── Test 2: Unsafe state ────────────────────────────────────────────────────
//
// Same jobs but with V1 having taken 1 more berth:
//   V1 alloc: (3,0,0) instead of (2,0,0)
//   available becomes (2,3,2)
//   V1 need: (0,2,2)
//
// Try the algorithm:
//   V1 need(0,2,2) <= work(2,3,2) → work(5,3,2).
//   V3 need(0,1,1) <= (5,3,2)    → work(7,4,3).
//   V4 need(4,3,1) <= (7,4,3)    → work(7,4,5).
//   V0 need(7,4,3) <= (7,4,5)    → work(7,5,5).
//   V2 need(6,0,0) <= (7,5,5)    → safe!
//
// That's actually still safe. Let me construct a genuinely unsafe state.
//
// Unsafe fixture:
//   Total: (4, 4, 4), Available: (0, 0, 0)
//   Job  Alloc    Max      Need
//   A    (2,1,1)  (3,3,3)  (1,2,2)
//   B    (2,3,3)  (3,4,4)  (1,1,1)
//
// sum(alloc)=(4,4,4), avail=(0,0,0), total=(4,4,4). ✓
// Work=(0,0,0). Neither A need(1,2,2) nor B need(1,1,1) <= (0,0,0). Unsafe.

static bool test_safety_unsafe() {
    BankerState state;
    state.total     = {4, 4, 4};
    state.available = {0, 0, 0};
    state.jobs = {
        {"A", {2,1,1}, {3,3,3}},
        {"B", {2,3,3}, {3,4,4}},
    };

    auto result = check_safety(state);
    ASSERT_FALSE(result.is_safe);
    ASSERT_TRUE(result.safe_sequence.empty());

    return true;
}

// ─── Test 3: Empty job list ──────────────────────────────────────────────────

static bool test_safety_empty_jobs() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {5, 5, 5};
    state.jobs = {};

    auto result = check_safety(state);
    ASSERT_TRUE(result.is_safe);
    ASSERT_EQ(result.safe_sequence.size(), 0u);

    return true;
}

// ─── Test 4: Single job, safe ────────────────────────────────────────────────

static bool test_safety_single_job_safe() {
    // Total: (5,5,5), V1 holds (2,1,3), max (4,3,5), avail (3,4,2)
    // Need = (2,2,2) <= avail (3,4,2) → safe.
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {3, 4, 2};
    state.jobs = {{"V1", {2,1,3}, {4,3,5}}};

    auto result = check_safety(state);
    ASSERT_TRUE(result.is_safe);
    ASSERT_EQ(result.safe_sequence.size(), 1u);
    ASSERT_EQ(result.safe_sequence[0], "V1");

    return true;
}

// ─── Test 5: Zero resource system ────────────────────────────────────────────

static bool test_safety_zero_resources() {
    // Total=(0,0,0). Jobs with alloc=(0,0,0), max=(0,0,0). Need=(0,0,0).
    // Trivially safe.
    BankerState state;
    state.total     = {0, 0, 0};
    state.available = {0, 0, 0};
    state.jobs = {
        {"V1", {0,0,0}, {0,0,0}},
        {"V2", {0,0,0}, {0,0,0}},
    };

    auto result = check_safety(state);
    ASSERT_TRUE(result.is_safe);
    ASSERT_EQ(result.safe_sequence.size(), 2u);

    return true;
}

// ─── Test 6: Validation — duplicate job IDs ──────────────────────────────────

static bool test_safety_duplicate_ids() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {5, 5, 5};
    state.jobs = {
        {"V1", {0,0,0}, {1,1,1}},
        {"V1", {0,0,0}, {2,2,2}},
    };

    ASSERT_THROWS(check_safety(state));
    return true;
}

// ─── Test 7: Validation — empty job ID ───────────────────────────────────────

static bool test_safety_empty_job_id() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {5, 5, 5};
    state.jobs = {{"", {0,0,0}, {1,1,1}}};

    ASSERT_THROWS(check_safety(state));
    return true;
}

// ─── Test 8: Validation — negative resource quantities ───────────────────────

static bool test_safety_negative_quantities() {
    // Negative allocation.
    {
        BankerState state;
        state.total     = {5, 5, 5};
        state.available = {5, 5, 5};
        state.jobs = {{"V1", {-1,0,0}, {1,1,1}}};
        ASSERT_THROWS(check_safety(state));
    }
    // Negative max_need.
    {
        BankerState state;
        state.total     = {5, 5, 5};
        state.available = {5, 5, 5};
        state.jobs = {{"V1", {0,0,0}, {-1,1,1}}};
        ASSERT_THROWS(check_safety(state));
    }
    // Negative available.
    {
        BankerState state;
        state.total     = {5, 5, 5};
        state.available = {-1, 5, 5};
        state.jobs = {};
        ASSERT_THROWS(check_safety(state));
    }
    // Negative total.
    {
        BankerState state;
        state.total     = {-1, 5, 5};
        state.available = {0, 5, 5};
        state.jobs = {};
        ASSERT_THROWS(check_safety(state));
    }

    return true;
}

// ─── Test 9: Validation — allocation exceeds max need ────────────────────────

static bool test_safety_alloc_exceeds_max() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {2, 5, 5};
    state.jobs = {{"V1", {3,0,0}, {2,1,1}}};  // alloc.berths > max.berths

    ASSERT_THROWS(check_safety(state));
    return true;
}

// ─── Test 10: Validation — allocations exceed total capacity ─────────────────

static bool test_safety_alloc_exceeds_total() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {1, 1, 1};
    // sum(alloc) = (3,3,3), but available=(1,1,1), so total should be (4,4,4)
    // but we say total=(5,5,5) → inconsistent.
    state.jobs = {
        {"V1", {1,1,1}, {2,2,2}},
        {"V2", {2,2,2}, {3,3,3}},
    };

    ASSERT_THROWS(check_safety(state));
    return true;
}


// ═════════════════════════════════════════════════════════════════════════════
// REQUEST EVALUATION TESTS
// ═════════════════════════════════════════════════════════════════════════════

// Build the classic safe state for request tests.
static BankerState make_safe_state() {
    BankerState s;
    s.total     = {10, 5, 7};
    s.available = { 3, 3, 2};
    s.jobs = {
        {"V0", {0,1,0}, {7,5,3}},
        {"V1", {2,0,0}, {3,2,2}},
        {"V2", {3,0,2}, {9,0,2}},
        {"V3", {2,1,1}, {2,2,2}},
        {"V4", {0,0,2}, {4,3,3}},
    };
    return s;
}

// ─── Test 11: Request that can be safely granted ─────────────────────────────
//
// V1's need = (1,2,2).  Request (1,0,2) for V1.
// After granting:  V1 alloc=(3,0,2), need=(0,2,0), avail=(2,3,0).
// Safety check on new state:
//   V3 need(0,1,1)? trucks need=1, work.trucks=0 → no.
//   V1 need(0,2,0) <= (2,3,0) → work=(5,3,2).
//   V3 need(0,1,1) <= (5,3,2) → work=(7,4,3).
//   V4 need(4,3,1) <= (7,4,3) → work=(7,4,5).
//   V0 need(7,4,3) <= (7,4,5) → work=(7,5,5).
//   V2 need(6,0,0) <= (7,5,5) → safe!

static bool test_request_granted() {
    auto state = make_safe_state();
    auto result = evaluate_request(state, "V1", {1, 0, 2});

    ASSERT_EQ(static_cast<int>(result.status),
              static_cast<int>(RequestStatus::GRANTED));
    ASSERT_TRUE(result.safety.is_safe);
    ASSERT_EQ(result.safety.safe_sequence.size(), 5u);

    return true;
}

// ─── Test 12: Request exceeds available ──────────────────────────────────────
//
// Available = (3,3,2).  V0 need = (7,4,3).  Request (4,0,0) for V0.
// 4 berths > 3 available → EXCEEDS_AVAILABLE.

static bool test_request_exceeds_available() {
    auto state = make_safe_state();
    auto result = evaluate_request(state, "V0", {4, 0, 0});

    ASSERT_EQ(static_cast<int>(result.status),
              static_cast<int>(RequestStatus::EXCEEDS_AVAILABLE));

    return true;
}

// ─── Test 13: Request exceeds remaining need ─────────────────────────────────
//
// V1 need = (1,2,2).  Request (2,0,0) → berths request 2 > need 1.

static bool test_request_exceeds_need() {
    auto state = make_safe_state();
    auto result = evaluate_request(state, "V1", {2, 0, 0});

    ASSERT_EQ(static_cast<int>(result.status),
              static_cast<int>(RequestStatus::EXCEEDS_NEED));

    return true;
}

// ─── Test 14: Request would make state unsafe ────────────────────────────────
//
// Construct a state where granting a feasible request leads to unsafe.
//
// Synthetic fixture:
//   Total: (4, 2, 2), Available: (1, 0, 0)
//   Job  Alloc    Max      Need
//   A    (1,1,1)  (3,2,2)  (2,1,1)
//   B    (2,1,1)  (4,2,2)  (2,1,1)
//
// sum(alloc)=(3,2,2), avail=(1,0,0), total=(4,2,2). ✓
//
// Current state is safe: A need(2,1,1)? No (cranes 1>0). B need(2,1,1)? No.
// Wait — neither can proceed. That's unsafe as a base state!
//
// Let me construct a proper one.
//
// Total: (6, 4, 4), Available: (1, 1, 1)
//   A: alloc(2,1,1), max(4,2,2), need(2,1,1)
//   B: alloc(3,2,2), max(5,4,4), need(2,2,2)
// sum(alloc)=(5,3,3), avail=(1,1,1), total=(6,4,4). ✓
//
// Safety: A need(2,1,1) <= (1,1,1)? No (berths 2>1).
//         B need(2,2,2) <= (1,1,1)? No. → UNSAFE as base!
//
// I need a safe base where a particular request makes it unsafe.
//
// Total: (6, 4, 4), Available: (2, 1, 1)
//   A: alloc(2,1,1), max(4,2,2), need(2,1,1)
//   B: alloc(2,2,2), max(5,4,4), need(3,2,2)
// sum(alloc)=(4,3,3), avail=(2,1,1), total=(6,4,4). ✓
//
// Safety check:
//   A need(2,1,1) <= (2,1,1) → work=(4,2,2).
//   B need(3,2,2) <= (4,2,2) → work=(6,4,4). Safe!
//
// Now request (1,0,0) for B:
//   B alloc→(3,2,2), need→(2,2,2), avail→(1,1,1).
//   A need(2,1,1) <= (1,1,1)? berths 2>1 → No.
//   B need(2,2,2) <= (1,1,1)? No. → UNSAFE!

static bool test_request_unsafe() {
    BankerState state;
    state.total     = {6, 4, 4};
    state.available = {2, 1, 1};
    state.jobs = {
        {"A", {2,1,1}, {4,2,2}},
        {"B", {2,2,2}, {5,4,4}},
    };

    // Confirm base state is safe.
    auto base_safety = check_safety(state);
    ASSERT_TRUE(base_safety.is_safe);

    // Request (1,0,0) for B.
    auto result = evaluate_request(state, "B", {1, 0, 0});
    ASSERT_EQ(static_cast<int>(result.status),
              static_cast<int>(RequestStatus::UNSAFE));
    ASSERT_FALSE(result.safety.is_safe);

    return true;
}

// ─── Test 15: evaluate_request does not modify state ─────────────────────────

static bool test_request_no_side_effects() {
    auto state = make_safe_state();

    // Snapshot original state.
    auto orig_avail = state.available;
    auto orig_jobs  = state.jobs;

    // Evaluate a request that would be granted.
    evaluate_request(state, "V1", {1, 0, 2});
    // Evaluate one that would be unsafe or exceeds.
    evaluate_request(state, "V0", {4, 0, 0});

    // Verify nothing changed.
    ASSERT_EQ(state.available.berths, orig_avail.berths);
    ASSERT_EQ(state.available.cranes, orig_avail.cranes);
    ASSERT_EQ(state.available.trucks, orig_avail.trucks);

    for (size_t i = 0; i < state.jobs.size(); ++i) {
        ASSERT_EQ(state.jobs[i].job_id, orig_jobs[i].job_id);
        ASSERT_EQ(state.jobs[i].allocation.berths, orig_jobs[i].allocation.berths);
        ASSERT_EQ(state.jobs[i].allocation.cranes, orig_jobs[i].allocation.cranes);
        ASSERT_EQ(state.jobs[i].allocation.trucks, orig_jobs[i].allocation.trucks);
        ASSERT_EQ(state.jobs[i].max_need.berths, orig_jobs[i].max_need.berths);
        ASSERT_EQ(state.jobs[i].max_need.cranes, orig_jobs[i].max_need.cranes);
        ASSERT_EQ(state.jobs[i].max_need.trucks, orig_jobs[i].max_need.trucks);
    }

    return true;
}

// ─── Test 16: Request validation — unknown job ───────────────────────────────

static bool test_request_unknown_job() {
    auto state = make_safe_state();
    ASSERT_THROWS(evaluate_request(state, "UNKNOWN", {1, 0, 0}));
    return true;
}

// ─── Test 17: Request validation — empty job ID ──────────────────────────────

static bool test_request_empty_job_id() {
    auto state = make_safe_state();
    ASSERT_THROWS(evaluate_request(state, "", {1, 0, 0}));
    return true;
}

// ─── Test 18: Request validation — negative request ──────────────────────────

static bool test_request_negative() {
    auto state = make_safe_state();
    ASSERT_THROWS(evaluate_request(state, "V1", {-1, 0, 0}));
    return true;
}

// ─── Test 19: Zero request (trivially granted) ───────────────────────────────

static bool test_request_zero() {
    auto state = make_safe_state();
    auto result = evaluate_request(state, "V1", {0, 0, 0});

    ASSERT_EQ(static_cast<int>(result.status),
              static_cast<int>(RequestStatus::GRANTED));
    ASSERT_TRUE(result.safety.is_safe);

    return true;
}


// ═════════════════════════════════════════════════════════════════════════════
// DEADLOCK DETECTION TESTS
// ═════════════════════════════════════════════════════════════════════════════

// ─── Test 20: No deadlock ────────────────────────────────────────────────────
//
// Available: (2, 1, 1)
//   A: alloc(1,0,1), request(1,0,0)
//   B: alloc(1,1,0), request(0,1,1)
//
// Work=(2,1,1).  A req(1,0,0) <= (2,1,1) → finish A, work=(3,1,2).
//                B req(0,1,1) <= (3,1,2) → finish B. No deadlock.

static bool test_detection_no_deadlock() {
    DetectionState state;
    state.available = {2, 1, 1};
    state.jobs = {
        {"A", {1,0,1}, {1,0,0}},
        {"B", {1,1,0}, {0,1,1}},
    };

    auto result = detect_deadlock(state);
    ASSERT_FALSE(result.has_deadlock);
    ASSERT_TRUE(result.deadlocked_jobs.empty());

    return true;
}

// ─── Test 21: Genuine deadlock with multiple jobs and resource types ─────────
//
// Available: (0, 0, 0)
//   A: alloc(1,0,0), request(0,1,0)   — holds berth, wants crane
//   B: alloc(0,1,0), request(0,0,1)   — holds crane, wants truck
//   C: alloc(0,0,1), request(1,0,0)   — holds truck, wants berth
//
// Classic circular wait.  Work=(0,0,0).  None can proceed. All deadlocked.

static bool test_detection_genuine_deadlock() {
    DetectionState state;
    state.available = {0, 0, 0};
    state.jobs = {
        {"A", {1,0,0}, {0,1,0}},
        {"B", {0,1,0}, {0,0,1}},
        {"C", {0,0,1}, {1,0,0}},
    };

    auto result = detect_deadlock(state);
    ASSERT_TRUE(result.has_deadlock);
    ASSERT_EQ(result.deadlocked_jobs.size(), 3u);

    // All three should be listed.
    std::set<std::string> dl(result.deadlocked_jobs.begin(),
                             result.deadlocked_jobs.end());
    ASSERT_TRUE(dl.count("A") == 1);
    ASSERT_TRUE(dl.count("B") == 1);
    ASSERT_TRUE(dl.count("C") == 1);

    return true;
}

// ─── Test 22: Partial deadlock — some jobs can finish ────────────────────────
//
// Available: (1, 0, 0)
//   A: alloc(0,1,0), request(0,0,1)   — holds crane, wants truck
//   B: alloc(0,0,1), request(0,1,0)   — holds truck, wants crane
//   C: alloc(1,0,0), request(1,0,0)   — holds berth, wants berth
//
// Work=(1,0,0).
//   C req(1,0,0) <= (1,0,0) → finish C, work=(2,0,0).
//   A req(0,0,1) <= (2,0,0)? No (trucks).
//   B req(0,1,0) <= (2,0,0)? No (cranes).
// A and B are deadlocked; C is not.

static bool test_detection_partial_deadlock() {
    DetectionState state;
    state.available = {1, 0, 0};
    state.jobs = {
        {"A", {0,1,0}, {0,0,1}},
        {"B", {0,0,1}, {0,1,0}},
        {"C", {1,0,0}, {1,0,0}},
    };

    auto result = detect_deadlock(state);
    ASSERT_TRUE(result.has_deadlock);
    ASSERT_EQ(result.deadlocked_jobs.size(), 2u);

    std::set<std::string> dl(result.deadlocked_jobs.begin(),
                             result.deadlocked_jobs.end());
    ASSERT_TRUE(dl.count("A") == 1);
    ASSERT_TRUE(dl.count("B") == 1);
    ASSERT_TRUE(dl.count("C") == 0);

    return true;
}

// ─── Test 23: Job with no allocation ─────────────────────────────────────────
// A job holding zero resources is treated as able to finish (standard algo).

static bool test_detection_no_allocation_job() {
    DetectionState state;
    state.available = {0, 0, 0};
    state.jobs = {
        {"A", {0,0,0}, {1,0,0}},   // holds nothing, wants 1 berth
        {"B", {1,0,0}, {0,0,0}},   // holds 1 berth, wants nothing
    };

    // Work=(0,0,0).
    // A has alloc=(0,0,0) → finish[A]=true (initialization rule).
    // B req(0,0,0) <= (0,0,0) → finish B, work=(1,0,0).
    // No deadlock.
    auto result = detect_deadlock(state);
    ASSERT_FALSE(result.has_deadlock);

    return true;
}

// ─── Test 24: Job whose request can be satisfied after another finishes ──────
//
// Available: (0, 1, 0)
//   A: alloc(1,0,0), request(0,1,0)   — wants crane
//   B: alloc(0,1,0), request(1,0,0)   — wants berth
//
// Work=(0,1,0).
//   A req(0,1,0) <= (0,1,0) → finish A, work=(1,1,0).
//   B req(1,0,0) <= (1,1,0) → finish B. No deadlock.

static bool test_detection_chain_resolution() {
    DetectionState state;
    state.available = {0, 1, 0};
    state.jobs = {
        {"A", {1,0,0}, {0,1,0}},
        {"B", {0,1,0}, {1,0,0}},
    };

    auto result = detect_deadlock(state);
    ASSERT_FALSE(result.has_deadlock);

    return true;
}

// ─── Test 25: Empty job list ─────────────────────────────────────────────────

static bool test_detection_empty() {
    DetectionState state;
    state.available = {5, 5, 5};
    state.jobs = {};

    auto result = detect_deadlock(state);
    ASSERT_FALSE(result.has_deadlock);
    ASSERT_TRUE(result.deadlocked_jobs.empty());

    return true;
}

// ─── Test 26: Detection validation — duplicate IDs ───────────────────────────

static bool test_detection_duplicate_ids() {
    DetectionState state;
    state.available = {1, 1, 1};
    state.jobs = {
        {"A", {0,0,0}, {0,0,0}},
        {"A", {0,0,0}, {0,0,0}},
    };

    ASSERT_THROWS(detect_deadlock(state));
    return true;
}

// ─── Test 27: Detection validation — empty job ID ────────────────────────────

static bool test_detection_empty_job_id() {
    DetectionState state;
    state.available = {1, 1, 1};
    state.jobs = {{"", {0,0,0}, {0,0,0}}};

    ASSERT_THROWS(detect_deadlock(state));
    return true;
}

// ─── Test 28: Detection validation — negative quantities ─────────────────────

static bool test_detection_negative_quantities() {
    {
        DetectionState state;
        state.available = {-1, 0, 0};
        state.jobs = {};
        ASSERT_THROWS(detect_deadlock(state));
    }
    {
        DetectionState state;
        state.available = {1, 1, 1};
        state.jobs = {{"A", {-1,0,0}, {0,0,0}}};
        ASSERT_THROWS(detect_deadlock(state));
    }
    {
        DetectionState state;
        state.available = {1, 1, 1};
        state.jobs = {{"A", {0,0,0}, {-1,0,0}}};
        ASSERT_THROWS(detect_deadlock(state));
    }

    return true;
}

// ─── Test 29: Safety with multiple resource types stress ─────────────────────
//
// 4 jobs, asymmetric resource needs across berths/cranes/trucks.
//
// Total: (6, 5, 4), Available: (1, 1, 1)
//   J1: alloc(2,0,1), max(3,2,2), need(1,2,1)
//   J2: alloc(1,2,0), max(2,3,1), need(1,1,1)
//   J3: alloc(1,1,1), max(3,3,2), need(2,2,1)
//   J4: alloc(1,1,1), max(2,2,2), need(1,1,1)
// sum(alloc)=(5,4,3), avail=(1,1,1), total=(6,5,4). ✓
//
// Safety:
//   J2 need(1,1,1) <= (1,1,1) → work=(2,3,1).
//   J1 need(1,2,1) <= (2,3,1) → work=(4,3,2).
//   J4 need(1,1,1) <= (4,3,2) → work=(5,4,3).
//   J3 need(2,2,1) <= (5,4,3) → work=(6,5,4). Safe!

static bool test_safety_multi_resource_stress() {
    BankerState state;
    state.total     = {6, 5, 4};
    state.available = {1, 1, 1};
    state.jobs = {
        {"J1", {2,0,1}, {3,2,2}},
        {"J2", {1,2,0}, {2,3,1}},
        {"J3", {1,1,1}, {3,3,2}},
        {"J4", {1,1,1}, {2,2,2}},
    };

    auto result = check_safety(state);
    ASSERT_TRUE(result.is_safe);
    ASSERT_EQ(result.safe_sequence.size(), 4u);
    ASSERT_TRUE(validate_safe_sequence(state, result.safe_sequence));

    return true;
}

// ─── Test 30: Deadlock detection — all jobs can finish with zero request ─────

static bool test_detection_all_zero_requests() {
    DetectionState state;
    state.available = {0, 0, 0};
    state.jobs = {
        {"A", {1,1,1}, {0,0,0}},
        {"B", {2,2,2}, {0,0,0}},
    };

    // Both have zero outstanding requests → both can proceed.
    auto result = detect_deadlock(state);
    ASSERT_FALSE(result.has_deadlock);

    return true;
}

// ─── Test 31: Safety validation — inconsistent state (available too high) ────

static bool test_safety_inconsistent_available() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {5, 5, 5};  // but jobs also hold resources
    state.jobs = {{"V1", {1,0,0}, {2,1,1}}};
    // sum(alloc)=(1,0,0) + avail=(5,5,5) = (6,5,5) != total(5,5,5)

    ASSERT_THROWS(check_safety(state));
    return true;
}

// ─── Test 32: Request on inconsistent base state throws ──────────────────────

static bool test_request_inconsistent_base() {
    BankerState state;
    state.total     = {5, 5, 5};
    state.available = {5, 5, 5};
    state.jobs = {{"V1", {1,0,0}, {2,1,1}}};
    // Inconsistent: sum(alloc)+avail != total

    ASSERT_THROWS(evaluate_request(state, "V1", {1, 0, 0}));
    return true;
}


// ═════════════════════════════════════════════════════════════════════════════
// main
// ═════════════════════════════════════════════════════════════════════════════

int main() {
    std::cout << "\n=== Deadlock Manager Test Suite ===\n\n";

    // Banker's safety algorithm tests.
    run_test("Safety: known safe state",            test_safety_known_safe);
    run_test("Safety: unsafe state",                test_safety_unsafe);
    run_test("Safety: empty job list",              test_safety_empty_jobs);
    run_test("Safety: single job safe",             test_safety_single_job_safe);
    run_test("Safety: zero resources",              test_safety_zero_resources);
    run_test("Safety: duplicate job IDs",           test_safety_duplicate_ids);
    run_test("Safety: empty job ID",                test_safety_empty_job_id);
    run_test("Safety: negative quantities",         test_safety_negative_quantities);
    run_test("Safety: alloc exceeds max",           test_safety_alloc_exceeds_max);
    run_test("Safety: alloc exceeds total",         test_safety_alloc_exceeds_total);

    // Request evaluation tests.
    run_test("Request: safely granted",             test_request_granted);
    run_test("Request: exceeds available",          test_request_exceeds_available);
    run_test("Request: exceeds need",               test_request_exceeds_need);
    run_test("Request: would make unsafe",          test_request_unsafe);
    run_test("Request: no side effects",            test_request_no_side_effects);
    run_test("Request: unknown job",                test_request_unknown_job);
    run_test("Request: empty job ID",               test_request_empty_job_id);
    run_test("Request: negative quantities",        test_request_negative);
    run_test("Request: zero request",               test_request_zero);

    // Deadlock detection tests.
    run_test("Detection: no deadlock",              test_detection_no_deadlock);
    run_test("Detection: genuine deadlock",         test_detection_genuine_deadlock);
    run_test("Detection: partial deadlock",         test_detection_partial_deadlock);
    run_test("Detection: job with no allocation",   test_detection_no_allocation_job);
    run_test("Detection: chain resolution",         test_detection_chain_resolution);
    run_test("Detection: empty job list",           test_detection_empty);
    run_test("Detection: duplicate IDs",            test_detection_duplicate_ids);
    run_test("Detection: empty job ID",             test_detection_empty_job_id);
    run_test("Detection: negative quantities",      test_detection_negative_quantities);

    // Cross-cutting tests.
    run_test("Safety: multi-resource stress",       test_safety_multi_resource_stress);
    run_test("Detection: all zero requests",        test_detection_all_zero_requests);
    run_test("Safety: inconsistent available",      test_safety_inconsistent_available);
    run_test("Request: inconsistent base state",    test_request_inconsistent_base);

    std::cout << "\n─── Summary ────────────────────────\n";
    std::cout << "  Total:  " << g_total  << "\n";
    std::cout << "  Passed: " << g_passed << "\n";
    std::cout << "  Failed: " << g_failed << "\n";
    std::cout << "────────────────────────────────────\n\n";

    return g_failed == 0 ? 0 : 1;
}
