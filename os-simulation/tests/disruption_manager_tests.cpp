// ─── Disruption Manager Test Suite ───────────────────────────────────────────
// Self-contained test harness — no external frameworks required.
// Compile:
//   g++ -std=c++14 -I../include disruption_manager_tests.cpp \
//       ../src/disruption_manager.cpp ../src/crane_registry.cpp \
//       ../src/resource_manager.cpp -o disruption_manager_tests
// Run:
//   ./disruption_manager_tests

#include "disruption_manager.h"
#include "crane_registry.h"
#include "resource_manager.h"

#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

// ─── Minimal test infrastructure ─────────────────────────────────────────────
// Mirrors conventions from resource_manager_tests.cpp,
// deadlock_manager_tests.cpp, and crane_registry_tests.cpp.

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

// ─── Helper ───────────────────────────────────────────────────────────────────

static bool has_job(const std::vector<JobProgress>& v, const std::string& job_id) {
    for (const auto& jp : v)
        if (jp.job_id == job_id) return true;
    return false;
}

// =============================================================================
// Section 1 — Idle-crane failure
// =============================================================================

// ─── Test 1: Fail an AVAILABLE crane — result structure ───────────────────────
static bool test_fail_idle_result() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    DisruptionResult r = dm.fail_idle_crane("C1");

    ASSERT_TRUE(r.event == DisruptionEvent::CRANE_FAILED);
    ASSERT_EQ(r.crane_id, std::string("C1"));
    ASSERT_TRUE(r.affected_job.job_id.empty());   // no job affected

    return true;
}

// ─── Test 2: Registry state after idle-crane failure ──────────────────────────
static bool test_fail_idle_registry_state() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");

    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 0);
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),    1);

    return true;
}

// ─── Test 3: Failed idle crane not available ──────────────────────────────────
static bool test_fail_idle_not_available() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");

    auto avail = reg.available_cranes();
    for (const auto& id : avail) {
        ASSERT_FALSE(id == "C1");
    }
    ASSERT_EQ(static_cast<int>(avail.size()), 1);   // only C2
    return true;
}

// ─── Test 4: No job-progress record for idle failure ─────────────────────────
static bool test_fail_idle_no_job_progress() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");

    ASSERT_FALSE(dm.has_job_progress("C1"));
    ASSERT_EQ(static_cast<int>(dm.affected_jobs().size()), 0);
    return true;
}

// ─── Test 5: fail_idle_crane — empty crane ID throws ─────────────────────────
static bool test_fail_idle_empty_id() {
    CraneRegistry reg;
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.fail_idle_crane(""));
    return true;
}

// ─── Test 6: fail_idle_crane — unregistered crane throws ─────────────────────
static bool test_fail_idle_unregistered() {
    CraneRegistry reg;
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.fail_idle_crane("GHOST"));
    return true;
}

// ─── Test 7: fail_idle_crane on ASSIGNED crane throws ────────────────────────
static bool test_fail_idle_on_assigned_throws() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    ASSERT_THROWS(dm.fail_idle_crane("C1"));
    // Crane still ASSIGNED after failed call.
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    return true;
}

// ─── Test 8: fail_idle_crane on already-FAILED crane throws ──────────────────
static bool test_fail_idle_already_failed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");
    ASSERT_THROWS(dm.fail_idle_crane("C1"));   // repeated — must throw
    return true;
}

// =============================================================================
// Section 2 — Assigned-crane failure and job-progress preservation
// =============================================================================

// ─── Test 9: Fail ASSIGNED crane — result structure ───────────────────────────
static bool test_fail_assigned_result() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-007");
    DisruptionManager dm(reg);

    DisruptionResult r = dm.fail_assigned_crane("C1", 30, 70);

    ASSERT_TRUE(r.event == DisruptionEvent::CRANE_FAILED);
    ASSERT_EQ(r.crane_id, std::string("C1"));
    ASSERT_EQ(r.affected_job.job_id,         std::string("SHIP-007"));
    ASSERT_EQ(r.affected_job.crane_id,       std::string("C1"));
    ASSERT_EQ(r.affected_job.work_completed, 30);
    ASSERT_EQ(r.affected_job.work_remaining, 70);

    return true;
}

// ─── Test 10: Registry state after assigned-crane failure ─────────────────────
static bool test_fail_assigned_registry_state() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-007");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 10, 90);

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::FAILED);
    ASSERT_TRUE(cs.assigned_job.empty());   // registry cleared it
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED), 0);
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),   1);

    return true;
}

// ─── Test 11: Job-progress captured before registry clears it ────────────────
// This is the critical ordering test.  After fail_assigned_crane(), the
// registry has cleared assigned_job but the disruption manager must still have
// the correct job_id stored.
static bool test_fail_assigned_progress_preserved() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-999");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 40, 60);

    // Registry has cleared the assignment.
    ASSERT_TRUE(reg.get_crane("C1").assigned_job.empty());

    // But DisruptionManager still knows about it.
    ASSERT_TRUE(dm.has_job_progress("C1"));
    JobProgress jp = dm.get_job_progress("C1");
    ASSERT_EQ(jp.job_id,         std::string("SHIP-999"));
    ASSERT_EQ(jp.crane_id,       std::string("C1"));
    ASSERT_EQ(jp.work_completed, 40);
    ASSERT_EQ(jp.work_remaining, 60);

    return true;
}

// ─── Test 12: fail_assigned_crane — empty crane ID throws ────────────────────
static bool test_fail_assigned_empty_id() {
    CraneRegistry reg;
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.fail_assigned_crane("", 0, 10));
    return true;
}

// ─── Test 13: fail_assigned_crane — unregistered crane throws ────────────────
static bool test_fail_assigned_unregistered() {
    CraneRegistry reg;
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.fail_assigned_crane("GHOST", 0, 10));
    return true;
}

// ─── Test 14: fail_assigned_crane on AVAILABLE crane throws ──────────────────
static bool test_fail_assigned_on_idle_throws() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    ASSERT_THROWS(dm.fail_assigned_crane("C1", 0, 10));
    // Crane still AVAILABLE after failed call.
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 15: fail_assigned_crane on already-FAILED crane throws ─────────────
static bool test_fail_assigned_already_failed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");
    ASSERT_THROWS(dm.fail_assigned_crane("C1", 0, 10));
    return true;
}

// ─── Test 16: fail_assigned_crane — negative work_completed throws ────────────
static bool test_fail_assigned_negative_work_completed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    ASSERT_THROWS(dm.fail_assigned_crane("C1", -1, 10));
    // Crane still ASSIGNED (no side effects on validation failure).
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    return true;
}

// ─── Test 17: fail_assigned_crane — zero work_remaining throws ────────────────
static bool test_fail_assigned_zero_work_remaining() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    ASSERT_THROWS(dm.fail_assigned_crane("C1", 10, 0));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    return true;
}

// ─── Test 18: fail_assigned_crane — negative work_remaining throws ────────────
static bool test_fail_assigned_negative_work_remaining() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    ASSERT_THROWS(dm.fail_assigned_crane("C1", 5, -5));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    return true;
}

// ─── Test 19: zero work_completed is valid ────────────────────────────────────
static bool test_fail_assigned_zero_work_completed_valid() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    // Crane fails at the very start — 0 work done.
    DisruptionResult r = dm.fail_assigned_crane("C1", 0, 100);
    ASSERT_EQ(r.affected_job.work_completed, 0);
    ASSERT_EQ(r.affected_job.work_remaining, 100);
    return true;
}

// =============================================================================
// Section 3 — Recovery
// =============================================================================

// ─── Test 20: Recover idle-failed crane ───────────────────────────────────────
static bool test_recover_idle_failed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");
    DisruptionResult r = dm.recover_crane("C1");

    ASSERT_TRUE(r.event == DisruptionEvent::CRANE_RECOVERED);
    ASSERT_EQ(r.crane_id, std::string("C1"));
    ASSERT_TRUE(r.affected_job.job_id.empty());

    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 21: Recover assigned-failed crane ───────────────────────────────────
static bool test_recover_assigned_failed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 20, 80);
    DisruptionResult r = dm.recover_crane("C1");

    ASSERT_TRUE(r.event == DisruptionEvent::CRANE_RECOVERED);
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 22: Job-progress record persists after recovery ────────────────────
// Recovery makes the crane available again but does NOT erase the job record.
// The caller must decide what to do with the interrupted work.
static bool test_progress_persists_after_recovery() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-002");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 15, 85);
    dm.recover_crane("C1");

    // Registry: crane is AVAILABLE.
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);

    // DisruptionManager: progress record is STILL there.
    ASSERT_TRUE(dm.has_job_progress("C1"));
    ASSERT_EQ(dm.get_job_progress("C1").job_id, std::string("SHIP-002"));
    return true;
}

// ─── Test 23: Recovered crane can be re-assigned ─────────────────────────────
static bool test_recovered_crane_reassignable() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 50, 50);
    dm.recover_crane("C1");

    // No exception — crane is AVAILABLE again.
    reg.assign_crane("C1", "SHIP-002");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_EQ(reg.get_crane("C1").assigned_job, std::string("SHIP-002"));
    return true;
}

// ─── Test 24: recover_crane — empty crane ID throws ──────────────────────────
static bool test_recover_empty_id() {
    CraneRegistry reg;
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.recover_crane(""));
    return true;
}

// ─── Test 25: recover_crane — unregistered crane throws ──────────────────────
static bool test_recover_unregistered() {
    CraneRegistry reg;
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.recover_crane("GHOST"));
    return true;
}

// ─── Test 26: recover_crane on AVAILABLE crane throws ────────────────────────
static bool test_recover_available_throws() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.recover_crane("C1"));
    return true;
}

// ─── Test 27: recover_crane on ASSIGNED crane throws ─────────────────────────
static bool test_recover_assigned_throws() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.recover_crane("C1"));
    return true;
}

// ─── Test 28: Repeated recovery throws ───────────────────────────────────────
static bool test_recover_repeated_throws() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");
    dm.recover_crane("C1");

    // Crane is AVAILABLE now — second recover must throw.
    ASSERT_THROWS(dm.recover_crane("C1"));
    return true;
}

// =============================================================================
// Section 4 — Job-progress record management
// =============================================================================

// ─── Test 29: get_job_progress for crane with no record throws ────────────────
static bool test_get_progress_no_record() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.get_job_progress("C1"));
    return true;
}

// ─── Test 30: clear_job_progress removes a specific record ────────────────────
static bool test_clear_specific_progress() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 5, 95);
    dm.fail_assigned_crane("C2", 10, 90);

    dm.clear_job_progress("C1");
    ASSERT_FALSE(dm.has_job_progress("C1"));
    ASSERT_TRUE(dm.has_job_progress("C2"));
    ASSERT_EQ(static_cast<int>(dm.affected_jobs().size()), 1);
    return true;
}

// ─── Test 31: clear_job_progress on non-existent record throws ────────────────
static bool test_clear_progress_no_record() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);
    ASSERT_THROWS(dm.clear_job_progress("C1"));
    return true;
}

// ─── Test 32: clear_all_job_progress removes all records ─────────────────────
static bool test_clear_all_progress() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 5, 95);
    dm.fail_assigned_crane("C2", 10, 90);

    dm.clear_all_job_progress();
    ASSERT_EQ(static_cast<int>(dm.affected_jobs().size()), 0);
    ASSERT_FALSE(dm.has_job_progress("C1"));
    ASSERT_FALSE(dm.has_job_progress("C2"));
    return true;
}

// ─── Test 33: affected_jobs lists all interrupted jobs ────────────────────────
static bool test_affected_jobs_all_listed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");
    // C3 stays idle (not assigned).
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 10, 90);
    dm.fail_assigned_crane("C2", 20, 80);
    dm.fail_idle_crane("C3");

    auto jobs = dm.affected_jobs();
    ASSERT_EQ(static_cast<int>(jobs.size()), 2);   // C3 (idle) not counted
    ASSERT_TRUE(has_job(jobs, "SHIP-A"));
    ASSERT_TRUE(has_job(jobs, "SHIP-B"));
    return true;
}

// =============================================================================
// Section 5 — Multiple cranes / complex scenarios
// =============================================================================

// ─── Test 34: Mixed fleet — some idle, some assigned, one fails each ─────────
static bool test_mixed_fleet_failures() {
    CraneRegistry reg;
    for (int i = 1; i <= 5; ++i)
        reg.register_crane("C" + std::to_string(i));

    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");

    DisruptionManager dm(reg);
    dm.fail_idle_crane("C3");
    dm.fail_assigned_crane("C1", 25, 75);

    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 2);  // C4, C5
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED),  1);  // C2
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),    2);  // C1, C3

    ASSERT_TRUE(dm.has_job_progress("C1"));
    ASSERT_FALSE(dm.has_job_progress("C3"));
    return true;
}

// ─── Test 35: Fail, recover, fail again — progress overwritten ────────────────
// Crane fails once (job-progress stored), is recovered, then reassigned and
// fails again.  The new progress record should overwrite the old one.
static bool test_fail_recover_fail_again() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    // First failure.
    dm.fail_assigned_crane("C1", 30, 70);
    ASSERT_EQ(dm.get_job_progress("C1").job_id, std::string("SHIP-001"));

    // Recovery.
    dm.recover_crane("C1");

    // Re-assign to a new job.
    reg.assign_crane("C1", "SHIP-002");

    // Second failure.
    dm.fail_assigned_crane("C1", 5, 95);

    // New record overwrites old.
    ASSERT_EQ(dm.get_job_progress("C1").job_id,         std::string("SHIP-002"));
    ASSERT_EQ(dm.get_job_progress("C1").work_completed, 5);
    ASSERT_EQ(dm.get_job_progress("C1").work_remaining, 95);
    return true;
}

// ─── Test 36: Multiple failures then multiple recoveries ─────────────────────
static bool test_multiple_fail_then_recover() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 10, 90);
    dm.fail_assigned_crane("C2", 20, 80);

    dm.recover_crane("C1");
    dm.recover_crane("C2");

    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 2);
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),    0);

    // Both progress records still present after recovery.
    ASSERT_TRUE(dm.has_job_progress("C1"));
    ASSERT_TRUE(dm.has_job_progress("C2"));
    return true;
}

// =============================================================================
// Section 6 — Resource-accounting boundary with ResourceManager
// =============================================================================
// These tests verify the caller-mediated coupling documented in the header.
// DisruptionManager does NOT call ResourceManager::release() on failure.

// ─── Test 37: RM allocation unchanged after crane failure ─────────────────────
// Failing one crane must not touch ResourceManager state.
static bool test_rm_unchanged_on_crane_failure() {
    ResourceManager rm(0, 3, 0);
    CraneRegistry   reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");

    // Allocate 2 cranes for SHIP-A.
    rm.allocate({"SHIP-A", 0, 2, 0});
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-A");

    DisruptionManager dm(reg);

    // C1 fails mid-job.
    dm.fail_assigned_crane("C1", 20, 80);

    // ResourceManager still shows 2 cranes allocated for SHIP-A.
    ASSERT_EQ(rm.allocated().cranes, 2);
    ASSERT_EQ(rm.available().cranes, 1);

    // CraneRegistry reflects the failure.
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(reg.get_crane("C2").status == CraneStatus::ASSIGNED);

    return true;
}

// ─── Test 38: RM release only when caller decides job is done ─────────────────
// After all cranes are recovered and the job completes, the caller explicitly
// releases the RM allocation.
static bool test_rm_release_by_caller() {
    ResourceManager rm(0, 2, 0);
    CraneRegistry   reg;
    reg.register_crane("C1");
    reg.register_crane("C2");

    rm.allocate({"SHIP-A", 0, 2, 0});
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-A");

    DisruptionManager dm(reg);

    // C1 fails; RM untouched.
    dm.fail_assigned_crane("C1", 10, 90);
    ASSERT_EQ(rm.allocated().cranes, 2);  // job still alive

    // Caller decides to cancel the job entirely.
    rm.release("SHIP-A");   // ← caller owns this decision
    reg.release_crane("C2");

    ASSERT_EQ(rm.allocated().cranes, 0);
    ASSERT_EQ(rm.available().cranes, 2);
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 1);  // C2
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),    1);  // C1

    return true;
}

// ─── Test 39: No double-release via RM and disruption manager ────────────────
// Verifies that the disruption path does not accidentally free RM resources
// that haven't been allocated.
static bool test_rm_no_double_release() {
    ResourceManager rm(0, 1, 0);
    CraneRegistry   reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    // Fail an idle crane — no RM allocation involved at all.
    dm.fail_idle_crane("C1");

    // RM is untouched; any release attempt would throw.
    ASSERT_THROWS(rm.release("SHIP-X"));
    ASSERT_EQ(rm.available().cranes, 1);  // RM capacity intact
    return true;
}

// ─── Test 40: affected_jobs used by caller to audit interrupted work ──────────
static bool test_affected_jobs_audit() {
    ResourceManager rm(0, 4, 0);
    CraneRegistry   reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");

    rm.allocate({"SHIP-A", 0, 2, 0});
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-A");

    rm.allocate({"SHIP-B", 0, 1, 0});
    reg.assign_crane("C3", "SHIP-B");

    DisruptionManager dm(reg);
    dm.fail_assigned_crane("C1", 40, 60);
    dm.fail_assigned_crane("C3", 10, 90);

    auto jobs = dm.affected_jobs();
    ASSERT_EQ(static_cast<int>(jobs.size()), 2);
    ASSERT_TRUE(has_job(jobs, "SHIP-A"));
    ASSERT_TRUE(has_job(jobs, "SHIP-B"));
    return true;
}

// =============================================================================
// Section 7 — Edge / boundary conditions
// =============================================================================

// ─── Test 41: Single-unit work — work_remaining == 1 is valid ────────────────
static bool test_work_remaining_one() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    DisruptionManager dm(reg);

    DisruptionResult r = dm.fail_assigned_crane("C1", 99, 1);
    ASSERT_EQ(r.affected_job.work_completed, 99);
    ASSERT_EQ(r.affected_job.work_remaining, 1);
    return true;
}

// ─── Test 42: Idle failure then immediate recovery ───────────────────────────
static bool test_idle_fail_then_recover() {
    CraneRegistry reg;
    reg.register_crane("C1");
    DisruptionManager dm(reg);

    dm.fail_idle_crane("C1");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);

    dm.recover_crane("C1");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 43: Disruption manager with empty registry ─────────────────────────
static bool test_empty_registry() {
    CraneRegistry reg;
    DisruptionManager dm(reg);

    ASSERT_THROWS(dm.fail_idle_crane("C1"));
    ASSERT_THROWS(dm.fail_assigned_crane("C1", 0, 1));
    ASSERT_THROWS(dm.recover_crane("C1"));
    ASSERT_EQ(static_cast<int>(dm.affected_jobs().size()), 0);
    return true;
}

// ─── Test 44: Multiple DMs sharing one registry — independent progress stores ─
static bool test_two_dms_same_registry() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");

    DisruptionManager dm1(reg);
    DisruptionManager dm2(reg);

    dm1.fail_assigned_crane("C1", 10, 90);
    dm2.fail_assigned_crane("C2", 20, 80);

    // Each DM only sees its own records.
    ASSERT_TRUE(dm1.has_job_progress("C1"));
    ASSERT_FALSE(dm1.has_job_progress("C2"));
    ASSERT_FALSE(dm2.has_job_progress("C1"));
    ASSERT_TRUE(dm2.has_job_progress("C2"));

    // Both cranes are FAILED in the shared registry.
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED), 2);
    return true;
}

// ─── Test 45: fail_assigned then clear_all then no records ───────────────────
static bool test_clear_all_then_empty() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-A");
    DisruptionManager dm(reg);

    dm.fail_assigned_crane("C1", 5, 95);
    ASSERT_EQ(static_cast<int>(dm.affected_jobs().size()), 1);

    dm.clear_all_job_progress();
    ASSERT_EQ(static_cast<int>(dm.affected_jobs().size()), 0);
    ASSERT_FALSE(dm.has_job_progress("C1"));
    ASSERT_THROWS(dm.get_job_progress("C1"));
    return true;
}

// =============================================================================
// main
// =============================================================================

int main() {
    std::cout << "\n=== Disruption Manager Test Suite ===\n\n";

    // ── Section 1: Idle-crane failure ─────────────────────────────────────────
    run_test("Fail idle — result structure",                    test_fail_idle_result);
    run_test("Fail idle — registry state",                     test_fail_idle_registry_state);
    run_test("Fail idle — not in available list",              test_fail_idle_not_available);
    run_test("Fail idle — no job-progress record",             test_fail_idle_no_job_progress);
    run_test("Fail idle — empty ID throws",                    test_fail_idle_empty_id);
    run_test("Fail idle — unregistered throws",                test_fail_idle_unregistered);
    run_test("Fail idle — ASSIGNED crane throws",              test_fail_idle_on_assigned_throws);
    run_test("Fail idle — already FAILED throws (repeated)",   test_fail_idle_already_failed);

    // ── Section 2: Assigned-crane failure ────────────────────────────────────
    run_test("Fail assigned — result structure",               test_fail_assigned_result);
    run_test("Fail assigned — registry state",                 test_fail_assigned_registry_state);
    run_test("Fail assigned — progress captured before clear", test_fail_assigned_progress_preserved);
    run_test("Fail assigned — empty ID throws",                test_fail_assigned_empty_id);
    run_test("Fail assigned — unregistered throws",            test_fail_assigned_unregistered);
    run_test("Fail assigned — AVAILABLE crane throws",         test_fail_assigned_on_idle_throws);
    run_test("Fail assigned — already FAILED throws",          test_fail_assigned_already_failed);
    run_test("Fail assigned — negative work_completed throws", test_fail_assigned_negative_work_completed);
    run_test("Fail assigned — zero work_remaining throws",     test_fail_assigned_zero_work_remaining);
    run_test("Fail assigned — negative work_remaining throws", test_fail_assigned_negative_work_remaining);
    run_test("Fail assigned — zero work_completed is valid",   test_fail_assigned_zero_work_completed_valid);

    // ── Section 3: Recovery ───────────────────────────────────────────────────
    run_test("Recover idle-failed crane",                      test_recover_idle_failed);
    run_test("Recover assigned-failed crane",                  test_recover_assigned_failed);
    run_test("Progress record persists after recovery",        test_progress_persists_after_recovery);
    run_test("Recovered crane can be re-assigned",             test_recovered_crane_reassignable);
    run_test("Recover — empty ID throws",                      test_recover_empty_id);
    run_test("Recover — unregistered throws",                  test_recover_unregistered);
    run_test("Recover — AVAILABLE crane throws",               test_recover_available_throws);
    run_test("Recover — ASSIGNED crane throws",                test_recover_assigned_throws);
    run_test("Recover — repeated throws",                      test_recover_repeated_throws);

    // ── Section 4: Job-progress management ───────────────────────────────────
    run_test("get_job_progress — no record throws",            test_get_progress_no_record);
    run_test("clear_job_progress — removes specific record",   test_clear_specific_progress);
    run_test("clear_job_progress — no record throws",          test_clear_progress_no_record);
    run_test("clear_all_job_progress — removes all",           test_clear_all_progress);
    run_test("affected_jobs — all interrupted jobs listed",    test_affected_jobs_all_listed);

    // ── Section 5: Multiple cranes / complex scenarios ────────────────────────
    run_test("Mixed fleet partial failures",                   test_mixed_fleet_failures);
    run_test("Fail, recover, fail again — progress overwrite", test_fail_recover_fail_again);
    run_test("Multiple failures then multiple recoveries",     test_multiple_fail_then_recover);

    // ── Section 6: ResourceManager boundary ──────────────────────────────────
    run_test("RM allocation unchanged on crane failure",       test_rm_unchanged_on_crane_failure);
    run_test("RM release is caller's responsibility",          test_rm_release_by_caller);
    run_test("No double-release via RM",                       test_rm_no_double_release);
    run_test("affected_jobs for caller audit",                 test_affected_jobs_audit);

    // ── Section 7: Edge / boundary conditions ────────────────────────────────
    run_test("work_remaining == 1 is valid",                   test_work_remaining_one);
    run_test("Idle fail then immediate recovery",               test_idle_fail_then_recover);
    run_test("Empty registry — all operations throw",          test_empty_registry);
    run_test("Two DMs sharing one registry — independent",     test_two_dms_same_registry);
    run_test("clear_all then affected_jobs is empty",          test_clear_all_then_empty);

    std::cout << "\n─── Summary ────────────────────────\n";
    std::cout << "  Total:  " << g_total  << "\n";
    std::cout << "  Passed: " << g_passed << "\n";
    std::cout << "  Failed: " << g_failed << "\n";
    std::cout << "────────────────────────────────────\n\n";

    return g_failed == 0 ? 0 : 1;
}
