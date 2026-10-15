// ─── Port Dispatcher Integration Test Suite ───────────────────────────────────
// Tests for PortDispatcher: the integration layer connecting DisruptionManager,
// CraneRegistry, and ResourceManager into a live job-dispatch loop.
//
// Compile (from project root):
//   g++ -std=c++14 -I include \
//       tests/port_dispatcher_tests.cpp \
//       src/port_dispatcher.cpp \
//       src/disruption_manager.cpp \
//       src/crane_registry.cpp \
//       src/resource_manager.cpp \
//       -o port_dispatcher_tests
// Run:
//   ./port_dispatcher_tests

#include "port_dispatcher.h"
#include "resource_manager.h"
#include "crane_registry.h"
#include "disruption_manager.h"

#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

// ─── Minimal test infrastructure ─────────────────────────────────────────────
// Same conventions as all other test suites in this project.

static int g_total  = 0;
static int g_passed = 0;
static int g_failed = 0;

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

// ─── Helper: build a PortJob quickly ─────────────────────────────────────────
static PortJob make_job(const std::string& id,
                        int arrival = 0,
                        int work    = 100,
                        int pri     = 5,
                        int berths  = 1,
                        int cranes  = 1,
                        int trucks  = 1)
{
    PortJob j;
    j.job_id       = id;
    j.arrival_time = arrival;
    j.work_total   = work;
    j.priority     = pri;
    j.berths       = berths;
    j.cranes       = cranes;
    j.trucks       = trucks;
    return j;
}

// =============================================================================
// Section 1 — Basic dispatch and lifecycle
// =============================================================================

// ─── Test 1: Submit a job — PENDING then dispatched immediately ───────────────
static bool test_submit_dispatches_when_resources_available() {
    PortDispatcher pd(2, 3, 4, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1"));

    // With sufficient resources and a registered crane, the job should be ACTIVE.
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.pending_count(), 0);
    ASSERT_EQ(pd.active_count(), 1);
    return true;
}

// ─── Test 2: Submit job — stays PENDING when no cranes available ──────────────
static bool test_submit_stays_pending_no_crane() {
    PortDispatcher pd(2, 3, 4, DispatchPolicy::FCFS);
    // No cranes registered → job cannot be dispatched.
    pd.submit_job(make_job("J1"));

    ASSERT_TRUE(pd.job_state("J1") == JobState::PENDING);
    ASSERT_EQ(pd.pending_count(), 1);
    ASSERT_EQ(pd.active_count(), 0);
    return true;
}

// ─── Test 3: Submit job — stays PENDING when RM has insufficient cranes ───────
static bool test_submit_stays_pending_insufficient_rm() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    // Dispatch first job (uses the 1 RM crane and 1 physical crane).
    pd.submit_job(make_job("J1", 0, 50, 5, 1, 1, 1));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // Second job cannot be dispatched.
    pd.register_crane("C2");  // physical crane exists, but RM crane count is 0
    pd.submit_job(make_job("J2", 1, 50, 5, 1, 1, 1));
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);
    return true;
}

// ─── Test 4: complete_job releases resources and dispatches next ───────────────
static bool test_complete_job_frees_and_dispatches() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1"));  // uses C1
    pd.submit_job(make_job("J2"));  // waits (RM cranes exhausted)

    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);

    pd.complete_job("J1");

    ASSERT_TRUE(pd.job_state("J1") == JobState::COMPLETED);
    ASSERT_EQ(pd.job_record("J1").work_completed, 100);
    ASSERT_EQ(pd.job_record("J1").work_remaining, 0);
    ASSERT_TRUE(pd.job_state("J2") == JobState::ACTIVE);  // dispatched on J1 completion
    return true;
}

// ─── Test 5: cancel_job on PENDING — no RM allocated, job cancelled ───────────
static bool test_cancel_pending_job() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    // No cranes registered → J1 stays PENDING.
    pd.submit_job(make_job("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::PENDING);

    pd.cancel_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::CANCELLED);
    ASSERT_EQ(pd.pending_count(), 0);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);  // nothing to release
    return true;
}

// ─── Test 6: cancel_job on ACTIVE — releases resources ───────────────────────
static bool test_cancel_active_job() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 1);

    pd.cancel_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::CANCELLED);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    ASSERT_EQ(pd.crane_registry().count_by_status(CraneStatus::AVAILABLE), 1);
    return true;
}

// =============================================================================
// Section 2 — Crane failure during active work
// =============================================================================

// ─── Test 7: Single crane fails — job becomes INTERRUPTED ─────────────────────
static bool test_single_crane_fail_interrupts_job() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    FailureResult fr = pd.on_crane_failed("C1", 30, 70);

    ASSERT_EQ(fr.crane_id,        std::string("C1"));
    ASSERT_EQ(fr.affected_job_id, std::string("J1"));
    ASSERT_TRUE(fr.job_interrupted);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    return true;
}

// ─── Test 8: Work progress preserved after crane failure ──────────────────────
static bool test_work_progress_preserved_on_failure() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 40, 60);

    const JobRecord& rec = pd.job_record("J1");
    ASSERT_EQ(rec.work_completed, 40);
    ASSERT_EQ(rec.work_remaining, 60);
    return true;
}

// ─── Test 9: Failed crane is not available ────────────────────────────────────
static bool test_failed_crane_not_available() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 20, 80);

    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_EQ(pd.crane_registry().count_by_status(CraneStatus::AVAILABLE), 0);
    return true;
}

// ─── Test 10: RM allocation unchanged after single-crane failure ───────────────
static bool test_rm_unchanged_after_crane_failure() {
    PortDispatcher pd(2, 3, 4, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    // Job requests 2 cranes.
    pd.submit_job(make_job("J1", 0, 100, 5, 1, 2, 2));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);

    // One crane fails.
    pd.on_crane_failed("C1", 15, 85);

    // Job still ACTIVE (C2 remains); RM unchanged.
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);
    return true;
}

// ─── Test 11: Berths and trucks remain intact while job active/interrupted ─────
static bool test_berths_trucks_intact_during_interruption() {
    PortDispatcher pd(3, 3, 5, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100, 5, 2, 1, 3));  // 2 berths, 1 crane, 3 trucks
    ASSERT_EQ(pd.resource_manager().allocated().berths, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 3);

    pd.on_crane_failed("C1", 10, 90);  // job interrupted

    // Berths and trucks still allocated.
    ASSERT_EQ(pd.resource_manager().allocated().berths, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 3);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    return true;
}

// ─── Test 12: Job with two cranes — one fails, job stays ACTIVE ───────────────
static bool test_job_stays_active_when_one_of_two_cranes_fails() {
    PortDispatcher pd(2, 3, 3, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1", 0, 100, 5, 1, 2, 1));  // needs 2 cranes
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    FailureResult fr = pd.on_crane_failed("C1", 25, 75);

    ASSERT_EQ(fr.affected_job_id, std::string("J1"));
    ASSERT_FALSE(fr.job_interrupted);          // still has C2
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // C1 is FAILED, C2 is still ASSIGNED.
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::ASSIGNED);
    return true;
}

// ─── Test 13: Idle crane failure — no job affected ────────────────────────────
static bool test_idle_crane_failure_no_job_affected() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    // C1 is idle (no job submitted); fail it.
    FailureResult fr = pd.on_idle_crane_failed("C1");

    ASSERT_EQ(fr.crane_id, std::string("C1"));
    ASSERT_TRUE(fr.affected_job_id.empty());
    ASSERT_FALSE(fr.job_interrupted);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_EQ(pd.pending_count(), 0);
    return true;
}

// =============================================================================
// Section 3 — Rescheduling and recovery
// =============================================================================

// ─── Test 14: Recovery dispatches interrupted job ─────────────────────────────
static bool test_recovery_dispatches_interrupted_job() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 30, 70);

    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);

    RecoveryResult rr = pd.on_crane_recovered("C1");

    ASSERT_TRUE(rr.job_dispatched);
    ASSERT_EQ(rr.dispatched_job_id, std::string("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.pending_count(), 0);
    return true;
}

// ─── Test 15: Recovered crane resumes interrupted job — work_remaining kept ───
static bool test_interrupted_job_resumes_with_correct_remaining_work() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 35, 65);

    pd.on_crane_recovered("C1");

    const JobRecord& rec = pd.job_record("J1");
    ASSERT_EQ(rec.work_remaining, 65);
    ASSERT_EQ(rec.work_completed, 35);
    ASSERT_TRUE(rec.state == JobState::ACTIVE);
    return true;
}

// ─── Test 16: Recovery does not duplicate capacity ───────────────────────────
// Recovering C1 and then completing J1 must leave RM back at full capacity —
// not above it.
static bool test_recovery_does_not_duplicate_capacity() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 50, 50);
    pd.on_crane_recovered("C1");

    // J1 is ACTIVE again.  Complete it.
    pd.complete_job("J1");

    // Full capacity restored, no extra.
    ASSERT_EQ(pd.resource_manager().available().cranes, 1);
    ASSERT_EQ(pd.resource_manager().available().berths, 1);
    ASSERT_EQ(pd.resource_manager().available().trucks, 1);
    ASSERT_EQ(pd.crane_registry().count_by_status(CraneStatus::AVAILABLE), 1);
    return true;
}

// ─── Test 17: Recovered crane can only be used after recovery ──────────────────
static bool test_failed_crane_not_assignable_before_recovery() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 20, 80);

    // J1 is INTERRUPTED and C1 is FAILED.
    // Submitting J2 must NOT assign C1 (it's FAILED).
    // Interrupted J1 has priority and resumes on C2.
    // J2 stays PENDING because failed C1 cannot be allocated before recovery.
    pd.submit_job(make_job("J2", 1, 50));

    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::ASSIGNED);
    ASSERT_EQ(pd.crane_registry().get_crane("C2").assigned_job, std::string("J1"));

    // Once C1 is recovered, J2 can finally be dispatched onto C1.
    pd.on_crane_recovered("C1");
    ASSERT_TRUE(pd.job_state("J2") == JobState::ACTIVE);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_EQ(pd.crane_registry().get_crane("C1").assigned_job, std::string("J2"));
    return true;
}

// ─── Test 18: Recovery restores eligibility exactly once ──────────────────────
static bool test_recovery_eligibility_exactly_once() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 10, 90);
    pd.on_crane_recovered("C1");

    // Second recovery must throw (crane is now AVAILABLE/ASSIGNED, not FAILED).
    ASSERT_THROWS(pd.on_crane_recovered("C1"));
    return true;
}

// ─── Test 19: Recovery of idle-failed crane dispatches pending job ─────────────
static bool test_recovery_of_idle_failed_crane_dispatches_pending() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    // Fail C1 while idle (before any job arrives).
    pd.on_idle_crane_failed("C1");

    // Submit a job — stays PENDING because C1 is FAILED.
    pd.submit_job(make_job("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::PENDING);

    // Recover C1 — J1 should be dispatched.
    RecoveryResult rr = pd.on_crane_recovered("C1");
    ASSERT_TRUE(rr.job_dispatched);
    ASSERT_EQ(rr.dispatched_job_id, std::string("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    return true;
}

// =============================================================================
// Section 4 — Policy-consistent ordering
// =============================================================================

// ─── Test 20: FCFS — interrupted job is re-queued ahead of newly pending ──────
static bool test_fcfs_interrupted_before_newly_pending() {
    PortDispatcher pd(1, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    // J1 submitted first (seq 0), J2 second (seq 1), but they share one RM crane.
    pd.submit_job(make_job("J1", 0, 100));  // dispatched immediately
    pd.submit_job(make_job("J2", 1, 100));  // stays PENDING (RM cranes exhausted)

    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);

    // C1 fails → J1 interrupted and re-queued.
    pd.on_crane_failed("C1", 20, 80);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);

    // Recover C2 (a fresh crane was registered but not yet used).
    // After recovery, J1 (INTERRUPTED) must be dispatched before J2 (PENDING).
    RecoveryResult rr = pd.on_crane_recovered("C1");
    ASSERT_TRUE(rr.job_dispatched);
    ASSERT_EQ(rr.dispatched_job_id, std::string("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);  // still waiting
    return true;
}

// ─── Test 21: SJF — shorter job dispatched before longer ──────────────────────
static bool test_sjf_shorter_job_first() {
    PortDispatcher pd(1, 2, 2, DispatchPolicy::SJF);
    pd.register_crane("C1");
    pd.register_crane("C2");

    // J1 (work=200, submitted first) is dispatched immediately.
    pd.submit_job(make_job("J1", 0, 200));
    // J2 (work=50) submitted second — stays pending (RM crane taken by J1).
    pd.submit_job(make_job("J2", 0, 50));
    // J3 (work=80) submitted third.
    pd.submit_job(make_job("J3", 0, 80));

    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);
    ASSERT_TRUE(pd.job_state("J3") == JobState::PENDING);

    // Complete J1 — SJF should dispatch J2 (shorter) before J3.
    pd.complete_job("J1");
    ASSERT_TRUE(pd.job_state("J2") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J3") == JobState::PENDING);
    return true;
}

// ─── Test 22: Priority policy — lower number dispatched first ─────────────────
static bool test_priority_lower_number_first() {
    PortDispatcher pd(1, 2, 2, DispatchPolicy::PRIORITY);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1", 0, 100, 5));  // priority 5, dispatched immediately
    pd.submit_job(make_job("J2", 0, 100, 3));  // priority 3, pending
    pd.submit_job(make_job("J3", 0, 100, 1));  // priority 1 (highest), pending

    pd.complete_job("J1");
    // J3 (priority 1) should be dispatched before J2 (priority 3).
    ASSERT_TRUE(pd.job_state("J3") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);
    return true;
}

// =============================================================================
// Section 5 — Multiple failures and recoveries
// =============================================================================

// ─── Test 23: Multiple sequential failures and recoveries ─────────────────────
static bool test_multiple_fail_recover_cycles() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));

    // Cycle 1.
    pd.on_crane_failed("C1", 20, 80);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    pd.on_crane_recovered("C1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // Cycle 2 (crane fails again).
    pd.on_crane_failed("C1", 30, 50);
    ASSERT_EQ(pd.job_record("J1").work_completed, 50);  // 20 + 30
    ASSERT_EQ(pd.job_record("J1").work_remaining, 50);

    pd.on_crane_recovered("C1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // Complete.
    pd.complete_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::COMPLETED);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    return true;
}

// ─── Test 24: Repeated failure events — invalid after already FAILED ──────────
static bool test_repeated_failure_throws() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 10, 90);

    // C1 is already FAILED — another fail call must throw.
    ASSERT_THROWS(pd.on_crane_failed("C1", 5, 85));
    ASSERT_THROWS(pd.on_idle_crane_failed("C1"));
    return true;
}

// ─── Test 25: Repeated recovery events — invalid after already AVAILABLE ──────
static bool test_repeated_recovery_throws() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1"));
    pd.on_crane_failed("C1", 10, 90);
    pd.on_crane_recovered("C1");

    // C1 is now ASSIGNED again (to J1) — another recover must throw.
    ASSERT_THROWS(pd.on_crane_recovered("C1"));
    return true;
}

// ─── Test 26: Multiple cranes, multiple failures, one job ─────────────────────
static bool test_multi_crane_job_multiple_failures() {
    // Job needs 3 cranes; they fail one by one.
    PortDispatcher pd(1, 3, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");
    pd.register_crane("C3");

    pd.submit_job(make_job("J1", 0, 300, 5, 1, 3, 2));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 3);

    // First crane fails — job still ACTIVE (2 remain).
    pd.on_crane_failed("C1", 50, 250);
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 3);

    // Second crane fails — job still ACTIVE (1 remains).
    pd.on_crane_failed("C2", 60, 190);
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // Third crane fails — job INTERRUPTED (no cranes left).
    pd.on_crane_failed("C3", 70, 120);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 3);  // still held

    // Berths and trucks still allocated.
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);
    return true;
}

// =============================================================================
// Section 6 — Resource accounting correctness
// =============================================================================

// ─── Test 27: No RM release until job is explicitly completed/cancelled ────────
static bool test_rm_not_released_until_complete() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 40, 60);      // INTERRUPTED
    pd.on_crane_recovered("C1");            // ACTIVE again

    // RM allocation is still held (not released by failure/recovery).
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 1);

    pd.complete_job("J1");
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    return true;
}

// ─── Test 28: RM release on cancel — ACTIVE job ───────────────────────────────
static bool test_rm_released_on_cancel_active() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100, 5, 1, 1, 1));
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 1);

    pd.cancel_job("J1");
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    ASSERT_EQ(pd.resource_manager().available().cranes, 2);
    return true;
}

// ─── Test 29: RM release on cancel — INTERRUPTED job ─────────────────────────
static bool test_rm_released_on_cancel_interrupted() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100, 5, 1, 1, 1));
    pd.on_crane_failed("C1", 30, 70);      // INTERRUPTED; RM still held

    ASSERT_EQ(pd.resource_manager().allocated().cranes, 1);
    pd.cancel_job("J1");

    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    ASSERT_EQ(pd.resource_manager().available().cranes, 1);
    return true;
}

// ─── Test 30: Cancel INTERRUPTED job — triggers dispatch of next pending ───────
static bool test_cancel_interrupted_dispatches_next() {
    PortDispatcher pd(1, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1"));  // uses C1 + 1 RM crane
    pd.submit_job(make_job("J2"));  // pending (RM crane exhausted)

    pd.on_crane_failed("C1", 10, 90);  // J1 interrupted
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);

    // Cancel J1 — frees the RM crane.  J2 should be dispatched with C2.
    pd.cancel_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::CANCELLED);
    ASSERT_TRUE(pd.job_state("J2") == JobState::ACTIVE);
    return true;
}

// ─── Test 31: No double release — complete after recovery ─────────────────────
static bool test_no_double_release_on_complete() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 50, 50);
    pd.on_crane_recovered("C1");  // C1 back to ACTIVE serving J1

    // Complete should succeed exactly once.
    pd.complete_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::COMPLETED);

    // A second complete must throw.
    ASSERT_THROWS(pd.complete_job("J1"));
    return true;
}

// ─── Test 32: Failed crane in multi-crane job — RM holds full allocation ───────
static bool test_multi_crane_single_fail_rm_holds_full_allocation() {
    PortDispatcher pd(2, 4, 4, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");
    pd.register_crane("C3");
    pd.register_crane("C4");

    // Job J1 requests 4 cranes.
    pd.submit_job(make_job("J1", 0, 400, 5, 2, 4, 4));
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 4);
    ASSERT_EQ(pd.resource_manager().allocated().berths, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 4);

    // C3 fails.
    pd.on_crane_failed("C3", 100, 300);
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);   // 3 cranes left

    // RM allocation unchanged.
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 4);
    ASSERT_EQ(pd.resource_manager().allocated().berths, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 4);
    return true;
}

// =============================================================================
// Section 7 — Validation and error paths
// =============================================================================

// ─── Test 33: submit_job — duplicate job_id throws ───────────────────────────
static bool test_submit_duplicate_job_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.submit_job(make_job("J1"));
    ASSERT_THROWS(pd.submit_job(make_job("J1")));
    return true;
}

// ─── Test 34: submit_job — empty job_id throws ───────────────────────────────
static bool test_submit_empty_id_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    ASSERT_THROWS(pd.submit_job(make_job("")));
    return true;
}

// ─── Test 35: submit_job — zero work_total throws ────────────────────────────
static bool test_submit_zero_work_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    ASSERT_THROWS(pd.submit_job(make_job("J1", 0, 0)));
    return true;
}

// ─── Test 36: submit_job — zero cranes throws ────────────────────────────────
static bool test_submit_zero_cranes_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    ASSERT_THROWS(pd.submit_job(make_job("J1", 0, 100, 5, 1, 0)));
    return true;
}

// ─── Test 37: complete_job on non-ACTIVE job throws ──────────────────────────
static bool test_complete_non_active_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1"));
    pd.on_crane_failed("C1", 10, 90);  // INTERRUPTED

    ASSERT_THROWS(pd.complete_job("J1"));  // must throw (not ACTIVE)
    return true;
}

// ─── Test 38: cancel_job on COMPLETED throws ──────────────────────────────────
static bool test_cancel_completed_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1"));
    pd.complete_job("J1");

    ASSERT_THROWS(pd.cancel_job("J1"));
    return true;
}

// ─── Test 39: cancel_job twice throws on second call ─────────────────────────
static bool test_cancel_twice_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.submit_job(make_job("J1"));  // stays PENDING (no cranes)

    pd.cancel_job("J1");
    ASSERT_THROWS(pd.cancel_job("J1"));
    return true;
}

// ─── Test 40: on_crane_failed — empty crane_id throws ────────────────────────
static bool test_fail_empty_crane_id_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    ASSERT_THROWS(pd.on_crane_failed("", 0, 10));
    return true;
}

// ─── Test 41: on_crane_failed — unregistered crane throws ────────────────────
static bool test_fail_unregistered_crane_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    ASSERT_THROWS(pd.on_crane_failed("GHOST", 0, 10));
    return true;
}

// ─── Test 42: on_crane_failed — AVAILABLE crane throws ───────────────────────
static bool test_fail_available_crane_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    // C1 is AVAILABLE (no job assigned) — fail_assigned_crane must throw.
    ASSERT_THROWS(pd.on_crane_failed("C1", 0, 10));
    return true;
}

// ─── Test 43: on_idle_crane_failed — already FAILED crane throws ──────────────
static bool test_fail_idle_already_failed_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.on_idle_crane_failed("C1");
    ASSERT_THROWS(pd.on_idle_crane_failed("C1"));
    return true;
}

// ─── Test 44: on_crane_recovered — not-FAILED crane throws ───────────────────
static bool test_recover_not_failed_throws() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    // C1 is AVAILABLE — recovery must throw.
    ASSERT_THROWS(pd.on_crane_recovered("C1"));
    return true;
}

// ─── Test 45: job_state / job_record — unknown job throws ─────────────────────
static bool test_unknown_job_queries_throw() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    ASSERT_THROWS(pd.job_state("GHOST"));
    ASSERT_THROWS(pd.job_record("GHOST"));
    return true;
}

// =============================================================================
// Section 8 — Integration with Scheduler batch policy (boundary documentation)
// =============================================================================
// These tests explicitly document the boundary between the one-shot Scheduler
// and the PortDispatcher's live dispatch loop.

// ─── Test 46: FCFS ordering matches batch FCFS arrival-time ordering ──────────
// Jobs submitted in order J1 (seq 0), J2 (seq 1), J3 (seq 2).
// After J1 completes, J2 (not J3) should be dispatched under FCFS.
static bool test_fcfs_ordering_consistent_with_batch_fcfs() {
    PortDispatcher pd(1, 3, 3, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");
    pd.register_crane("C3");

    pd.submit_job(make_job("J1", 0, 50));   // seq 0, dispatched immediately
    pd.submit_job(make_job("J2", 1, 100));  // seq 1, pending
    pd.submit_job(make_job("J3", 2, 75));   // seq 2, pending

    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    pd.complete_job("J1");
    ASSERT_TRUE(pd.job_state("J2") == JobState::ACTIVE);  // FCFS picks seq-1 first
    ASSERT_TRUE(pd.job_state("J3") == JobState::PENDING);
    return true;
}

// ─── Test 47: SJF interrupted job skips ahead of longer pending job ────────────
// Demonstrates consistent SJF behavior: an interrupted job with work_remaining=40
// is dispatched before a fresh pending job with work_total=100.
static bool test_sjf_interrupted_skips_ahead_longer_pending() {
    PortDispatcher pd(1, 2, 2, DispatchPolicy::SJF);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1", 0, 100));  // dispatched immediately
    pd.submit_job(make_job("J2", 0, 100));  // pending

    // J1 fails with 40 remaining.
    pd.on_crane_failed("C1", 60, 40);
    // Queue: J1 (INTERRUPTED, remaining=40), J2 (PENDING, total=100).

    // Recover C1 — SJF should pick J1 (remaining=40 < J2's 100).
    RecoveryResult rr = pd.on_crane_recovered("C1");
    ASSERT_EQ(rr.dispatched_job_id, std::string("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    return true;
}

// ─── Test 48: Priority ordering with interrupted job in the mix ───────────────
static bool test_priority_ordering_with_interrupted_job() {
    PortDispatcher pd(1, 2, 2, DispatchPolicy::PRIORITY);
    pd.register_crane("C1");
    pd.register_crane("C2");

    // J1 (priority 3) dispatched; J2 (priority 1, higher) is pending.
    pd.submit_job(make_job("J1", 0, 100, 3));
    pd.submit_job(make_job("J2", 0, 100, 1));

    pd.on_crane_failed("C1", 20, 80);
    // J1 is INTERRUPTED (priority 3), J2 is PENDING (priority 1).
    // Under Priority policy, INTERRUPTED jobs take precedence over PENDING
    // at the same or lower priority.  J1 (INTERRUPTED) comes before J2 (PENDING).

    RecoveryResult rr = pd.on_crane_recovered("C1");
    ASSERT_EQ(rr.dispatched_job_id, std::string("J1"));  // interrupted first
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::PENDING);
    return true;
}

// =============================================================================
// Section 9 — DisruptionManager state visible through dispatcher
// =============================================================================

// ─── Test 49: Disruption manager captures job progress through dispatcher ──────
static bool test_disruption_manager_progress_visible() {
    PortDispatcher pd(1, 1, 1, DispatchPolicy::FCFS);
    pd.register_crane("C1");

    pd.submit_job(make_job("J1", 0, 100));
    pd.on_crane_failed("C1", 45, 55);

    const DisruptionManager& dm = pd.disruption_manager();
    ASSERT_TRUE(dm.has_job_progress("C1"));
    JobProgress jp = dm.get_job_progress("C1");
    ASSERT_EQ(jp.job_id,         std::string("J1"));
    ASSERT_EQ(jp.work_completed, 45);
    ASSERT_EQ(jp.work_remaining, 55);
    return true;
}

// ─── Test 50: No RM phantom cranes after multi-cycle fail/recover/complete ────
static bool test_no_phantom_rm_cranes_after_cycles() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1", 0, 100, 5, 1, 1, 1));
    pd.submit_job(make_job("J2", 0, 100, 5, 1, 1, 1));

    // Both jobs should be dispatched (2 RM cranes, 2 physical cranes).
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_TRUE(pd.job_state("J2") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);

    // J1's crane fails.
    pd.on_crane_failed("C1", 30, 70);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);  // still held

    // Complete J2 — releases C2 and its RM crane.
    pd.complete_job("J2");
    // J1 still holds its RM slot; C1 is FAILED.
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 1);

    // Recover C1 — J1 is re-dispatched.
    pd.on_crane_recovered("C1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // Complete J1.
    pd.complete_job("J1");
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    ASSERT_EQ(pd.resource_manager().available().cranes, 2);
    ASSERT_EQ(pd.crane_registry().count_by_status(CraneStatus::AVAILABLE), 2);
    return true;
}

// ─── Test 51: Multi-crane job lifecycle trace (4-step user scenario) ───────────
static bool test_multi_crane_lifecycle_trace() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    // 1. A job holds multiple cranes (2), a berth (1), and trucks (2).
    pd.submit_job(make_job("J1", 0, 200, 5, 1, 2, 2));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::ASSIGNED);

    // Step 1: One assigned crane fails (C1).
    // The job continues with its remaining crane C2.
    FailureResult fr1 = pd.on_crane_failed("C1", 40, 160);
    ASSERT_FALSE(fr1.job_interrupted);
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    // Berths and trucks stay intact; RM allocation fully reserved.
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::ASSIGNED);

    // Step 2: All assigned cranes fail (C2 fails).
    // Job becomes INTERRUPTED.
    FailureResult fr2 = pd.on_crane_failed("C2", 40, 120);
    ASSERT_TRUE(fr2.job_interrupted);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);
    // Berths and trucks still strictly allocated; RM holds full allocation.
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::FAILED);

    // Step 3: A replacement crane becomes available (C1 recovered).
    // Interrupted job resumes (ACTIVE) with replacement crane C1.
    RecoveryResult rr = pd.on_crane_recovered("C1");
    ASSERT_TRUE(rr.job_dispatched);
    ASSERT_EQ(rr.dispatched_job_id, std::string("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    // Work remaining preserved at 120, work completed at 80.
    ASSERT_EQ(pd.job_record("J1").work_completed, 80);
    ASSERT_EQ(pd.job_record("J1").work_remaining, 120);
    // Berths and trucks still reserved.
    ASSERT_EQ(pd.resource_manager().allocated().berths, 1);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 2);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 2);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::FAILED);

    // Step 4: Attempting to complete while undersupplied throws (premature completion rejected).
    ASSERT_THROWS(pd.complete_job("J1"));
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);

    // Step 5: C2 is recovered. It tops up J1 back to its full requirement of 2 cranes.
    RecoveryResult rr2 = pd.on_crane_recovered("C2");
    ASSERT_TRUE(rr2.job_dispatched);
    ASSERT_EQ(rr2.dispatched_job_id, std::string("J1"));
    ASSERT_TRUE(pd.crane_count_fulfilled("J1"));
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::ASSIGNED);

    // Step 6: Now that all required cranes are assigned, the job completes cleanly.
    pd.complete_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::COMPLETED);
    // RM released cleanly once: 0 allocated, full capacities restored.
    ASSERT_EQ(pd.resource_manager().allocated().berths, 0);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    ASSERT_EQ(pd.resource_manager().allocated().trucks, 0);
    ASSERT_EQ(pd.resource_manager().available().cranes, 2);
    // CraneRegistry: both C1 and C2 released to AVAILABLE.
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::AVAILABLE);
    ASSERT_TRUE(pd.crane_registry().get_crane("C2").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 52: Multi-crane resume with multiple available cranes ───────────────
static bool test_multi_crane_resume_with_multiple_available_cranes() {
    PortDispatcher pd(1, 3, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");
    pd.register_crane("C3");

    // J1 requires 2 cranes (gets C1, C2).
    pd.submit_job(make_job("J1", 0, 200, 5, 1, 2, 2));
    pd.on_crane_failed("C1", 20, 180);
    pd.on_crane_failed("C2", 20, 160);
    ASSERT_TRUE(pd.job_state("J1") == JobState::INTERRUPTED);

    // When C1 recovers, both C1 and C3 are available.
    // Interrupted job J1 resumes with both available cranes (reaching its required count of 2).
    pd.on_crane_recovered("C1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::ACTIVE);
    ASSERT_EQ(static_cast<int>(pd.job_record("J1").active_cranes.size()), 2);
    ASSERT_TRUE(pd.job_record("J1").active_cranes.count("C1") > 0);
    ASSERT_TRUE(pd.job_record("J1").active_cranes.count("C3") > 0);
    return true;
}

// ─── Test 53: Confirmed live scenario regression test ─────────────────────────
// 1. JOB_NEW requires 2 cranes (C1, CRANE_DEMO_01).
// 2. Both assigned -> ACTIVE.
// 3. C1 fails -> C1 is FAILED, CRANE_DEMO_01 ASSIGNED, JOB_NEW stays ACTIVE.
// 4. Attempt premature complete -> rejected!
// 5. C1 recovered -> automatically reassigned to JOB_NEW.
// 6. Complete job -> succeeds, both cranes released to AVAILABLE.
static bool test_confirmed_live_scenario_regression() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("CRANE_DEMO_01");

    pd.submit_job(make_job("JOB_NEW", 0, 100, 5, 1, 2, 1));
    ASSERT_TRUE(pd.job_state("JOB_NEW") == JobState::ACTIVE);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_TRUE(pd.crane_registry().get_crane("CRANE_DEMO_01").status == CraneStatus::ASSIGNED);
    ASSERT_TRUE(pd.crane_count_fulfilled("JOB_NEW"));

    // C1 fails
    FailureResult fr = pd.on_crane_failed("C1", 30, 70);
    ASSERT_FALSE(fr.job_interrupted);
    ASSERT_TRUE(pd.job_state("JOB_NEW") == JobState::ACTIVE);
    ASSERT_FALSE(pd.crane_count_fulfilled("JOB_NEW"));
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(pd.crane_registry().get_crane("CRANE_DEMO_01").status == CraneStatus::ASSIGNED);

    // Premature completion attempt is rejected
    ASSERT_THROWS(pd.complete_job("JOB_NEW"));
    ASSERT_TRUE(pd.job_state("JOB_NEW") == JobState::ACTIVE);

    // C1 is recovered -> reassigned to JOB_NEW
    RecoveryResult rr = pd.on_crane_recovered("C1");
    ASSERT_TRUE(rr.job_dispatched);
    ASSERT_EQ(rr.dispatched_job_id, std::string("JOB_NEW"));
    ASSERT_TRUE(pd.crane_count_fulfilled("JOB_NEW"));
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_TRUE(pd.crane_registry().get_crane("CRANE_DEMO_01").status == CraneStatus::ASSIGNED);

    // Job completes cleanly
    pd.complete_job("JOB_NEW");
    ASSERT_TRUE(pd.job_state("JOB_NEW") == JobState::COMPLETED);
    ASSERT_EQ(pd.resource_manager().allocated().cranes, 0);
    ASSERT_EQ(pd.resource_manager().available().cranes, 2);
    ASSERT_TRUE(pd.crane_registry().get_crane("C1").status == CraneStatus::AVAILABLE);
    ASSERT_TRUE(pd.crane_registry().get_crane("CRANE_DEMO_01").status == CraneStatus::AVAILABLE);
    ASSERT_EQ(pd.job_record("JOB_NEW").work_completed, 100);
    ASSERT_EQ(pd.job_record("JOB_NEW").work_remaining, 0);

    return true;
}

// ─── Test 54: Validation and work-accounting safeguards ───────────────────────
static bool test_complete_validation_and_accounting_safeguards() {
    PortDispatcher pd(2, 2, 2, DispatchPolicy::FCFS);
    pd.register_crane("C1");
    pd.register_crane("C2");

    pd.submit_job(make_job("J1", 0, 100, 5, 1, 1, 1));
    pd.complete_job("J1");
    ASSERT_TRUE(pd.job_state("J1") == JobState::COMPLETED);

    // Duplicate complete on COMPLETED throws
    ASSERT_THROWS(pd.complete_job("J1"));

    // Complete on unknown / empty throws
    ASSERT_THROWS(pd.complete_job("NONEXISTENT"));
    ASSERT_THROWS(pd.complete_job(""));

    // Work accounting: completed + remaining == total
    pd.submit_job(make_job("J2", 0, 150, 5, 1, 1, 1));
    pd.on_crane_failed("C1", 60, 90);
    const auto& rec = pd.job_record("J2");
    ASSERT_EQ(rec.work_completed + rec.work_remaining, rec.job.work_total);

    // Complete on INTERRUPTED throws
    ASSERT_TRUE(rec.state == JobState::INTERRUPTED);
    ASSERT_THROWS(pd.complete_job("J2"));

    return true;
}

// =============================================================================
// main
// =============================================================================

int main() {
    std::cout << "\n=== Port Dispatcher Integration Test Suite ===\n\n";

    // ── Section 1: Basic dispatch and lifecycle ───────────────────────────────
    run_test("Submit job — dispatched immediately",             test_submit_dispatches_when_resources_available);
    run_test("Submit job — stays PENDING (no crane)",          test_submit_stays_pending_no_crane);
    run_test("Submit job — stays PENDING (insufficient RM)",   test_submit_stays_pending_insufficient_rm);
    run_test("complete_job — frees resources, dispatches next",test_complete_job_frees_and_dispatches);
    run_test("cancel_job — PENDING job",                       test_cancel_pending_job);
    run_test("cancel_job — ACTIVE job releases resources",     test_cancel_active_job);

    // ── Section 2: Crane failure during active work ───────────────────────────
    run_test("Single-crane fail — job INTERRUPTED",            test_single_crane_fail_interrupts_job);
    run_test("Work progress preserved after crane failure",    test_work_progress_preserved_on_failure);
    run_test("Failed crane is not AVAILABLE",                  test_failed_crane_not_available);
    run_test("RM unchanged after single-crane failure",        test_rm_unchanged_after_crane_failure);
    run_test("Berths and trucks intact during interruption",   test_berths_trucks_intact_during_interruption);
    run_test("Job stays ACTIVE when one of two cranes fails",  test_job_stays_active_when_one_of_two_cranes_fails);
    run_test("Idle-crane failure — no job affected",           test_idle_crane_failure_no_job_affected);

    // ── Section 3: Rescheduling and recovery ──────────────────────────────────
    run_test("Recovery dispatches interrupted job",            test_recovery_dispatches_interrupted_job);
    run_test("Interrupted job resumes with correct work_remaining", test_interrupted_job_resumes_with_correct_remaining_work);
    run_test("Recovery does not duplicate capacity",           test_recovery_does_not_duplicate_capacity);
    run_test("Failed crane not assignable before recovery",    test_failed_crane_not_assignable_before_recovery);
    run_test("Recovery eligibility exactly once",              test_recovery_eligibility_exactly_once);
    run_test("Recovery of idle-failed crane dispatches pending",test_recovery_of_idle_failed_crane_dispatches_pending);

    // ── Section 4: Policy-consistent ordering ─────────────────────────────────
    run_test("FCFS — interrupted before newly pending",        test_fcfs_interrupted_before_newly_pending);
    run_test("SJF — shorter job dispatched first",             test_sjf_shorter_job_first);
    run_test("Priority — lower number dispatched first",       test_priority_lower_number_first);

    // ── Section 5: Multiple failures and recoveries ───────────────────────────
    run_test("Multiple sequential fail/recover cycles",        test_multiple_fail_recover_cycles);
    run_test("Repeated failure events throw",                  test_repeated_failure_throws);
    run_test("Repeated recovery events throw",                 test_repeated_recovery_throws);
    run_test("Multi-crane job — multiple failures",            test_multi_crane_job_multiple_failures);

    // ── Section 6: Resource accounting correctness ────────────────────────────
    run_test("RM not released until explicitly complete/cancel",test_rm_not_released_until_complete);
    run_test("RM released on cancel — ACTIVE job",             test_rm_released_on_cancel_active);
    run_test("RM released on cancel — INTERRUPTED job",        test_rm_released_on_cancel_interrupted);
    run_test("Cancel interrupted job — dispatches next",       test_cancel_interrupted_dispatches_next);
    run_test("No double release on complete after recovery",   test_no_double_release_on_complete);
    run_test("Multi-crane single fail — RM holds full alloc",  test_multi_crane_single_fail_rm_holds_full_allocation);

    // ── Section 7: Validation and error paths ────────────────────────────────
    run_test("submit_job — duplicate job_id throws",           test_submit_duplicate_job_throws);
    run_test("submit_job — empty job_id throws",               test_submit_empty_id_throws);
    run_test("submit_job — zero work_total throws",            test_submit_zero_work_throws);
    run_test("submit_job — zero cranes throws",                test_submit_zero_cranes_throws);
    run_test("complete_job — non-ACTIVE throws",               test_complete_non_active_throws);
    run_test("cancel_job — COMPLETED throws",                  test_cancel_completed_throws);
    run_test("cancel_job — twice throws",                      test_cancel_twice_throws);
    run_test("on_crane_failed — empty crane_id throws",        test_fail_empty_crane_id_throws);
    run_test("on_crane_failed — unregistered crane throws",    test_fail_unregistered_crane_throws);
    run_test("on_crane_failed — AVAILABLE crane throws",       test_fail_available_crane_throws);
    run_test("on_idle_crane_failed — already FAILED throws",   test_fail_idle_already_failed_throws);
    run_test("on_crane_recovered — not-FAILED throws",         test_recover_not_failed_throws);
    run_test("job_state/job_record — unknown job throws",      test_unknown_job_queries_throw);

    // ── Section 8: Scheduler boundary documentation ───────────────────────────
    run_test("FCFS ordering consistent with batch FCFS",       test_fcfs_ordering_consistent_with_batch_fcfs);
    run_test("SJF interrupted skips ahead of longer pending",  test_sjf_interrupted_skips_ahead_longer_pending);
    run_test("Priority ordering with interrupted job",         test_priority_ordering_with_interrupted_job);

    // ── Section 9: DisruptionManager state via dispatcher ────────────────────
    run_test("DisruptionManager progress visible through dispatcher", test_disruption_manager_progress_visible);
    run_test("No phantom RM cranes after multi-cycle fail/recover/complete", test_no_phantom_rm_cranes_after_cycles);
    run_test("Multi-crane full lifecycle trace (4-step user scenario)", test_multi_crane_lifecycle_trace);
    run_test("Multi-crane resume with multiple available cranes", test_multi_crane_resume_with_multiple_available_cranes);
    run_test("Confirmed live scenario regression (undersupplied completion rejected, recovered topped up)", test_confirmed_live_scenario_regression);
    run_test("Complete validation and work-accounting safeguards", test_complete_validation_and_accounting_safeguards);

    std::cout << "\n─── Summary ────────────────────────\n";
    std::cout << "  Total:  " << g_total  << "\n";
    std::cout << "  Passed: " << g_passed << "\n";
    std::cout << "  Failed: " << g_failed << "\n";
    std::cout << "────────────────────────────────────\n\n";

    return g_failed == 0 ? 0 : 1;
}
