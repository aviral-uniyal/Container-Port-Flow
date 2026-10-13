// ─── Crane Registry Test Suite ────────────────────────────────────────────────
// Self-contained test harness — no external frameworks required.
// Compile:  g++ -std=c++14 -I../include crane_registry_tests.cpp ../src/crane_registry.cpp -o crane_registry_tests
// Run:      ./crane_registry_tests

#include "crane_registry.h"
#include "resource_manager.h"

#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

// ─── Minimal test infrastructure ─────────────────────────────────────────────
// Mirrors the conventions used by resource_manager_tests.cpp and
// deadlock_manager_tests.cpp.

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

static bool contains(const std::vector<std::string>& v, const std::string& s) {
    return std::find(v.begin(), v.end(), s) != v.end();
}

// =============================================================================
// Registration tests
// =============================================================================

// ─── Test 1: Register a single crane — initial state ─────────────────────────

static bool test_register_single() {
    CraneRegistry reg;
    reg.register_crane("C1");

    ASSERT_TRUE(reg.has_crane("C1"));
    ASSERT_EQ(reg.total_cranes(), 1);

    CraneState cs = reg.get_crane("C1");
    ASSERT_EQ(cs.crane_id, std::string("C1"));
    ASSERT_TRUE(cs.status == CraneStatus::AVAILABLE);
    ASSERT_TRUE(cs.assigned_job.empty());

    return true;
}

// ─── Test 2: Register multiple cranes ────────────────────────────────────────

static bool test_register_multiple() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");

    ASSERT_EQ(reg.total_cranes(), 3);
    ASSERT_TRUE(reg.has_crane("C1"));
    ASSERT_TRUE(reg.has_crane("C2"));
    ASSERT_TRUE(reg.has_crane("C3"));
    ASSERT_FALSE(reg.has_crane("C4"));

    return true;
}

// ─── Test 3: Duplicate crane ID rejected ─────────────────────────────────────

static bool test_register_duplicate() {
    CraneRegistry reg;
    reg.register_crane("C1");
    ASSERT_THROWS(reg.register_crane("C1"));

    // Registry unchanged after the rejected call.
    ASSERT_EQ(reg.total_cranes(), 1);
    return true;
}

// ─── Test 4: Empty crane ID rejected ─────────────────────────────────────────

static bool test_register_empty_id() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.register_crane(""));
    ASSERT_EQ(reg.total_cranes(), 0);
    return true;
}

// ─── Test 5: get_crane for unregistered crane throws ─────────────────────────

static bool test_get_unregistered() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.get_crane("GHOST"));
    return true;
}

// =============================================================================
// Assignment tests
// =============================================================================

// ─── Test 6: Successful assignment ───────────────────────────────────────────

static bool test_assign_success() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::ASSIGNED);
    ASSERT_EQ(cs.assigned_job, std::string("SHIP-001"));

    // No longer available.
    auto avail = reg.available_cranes();
    ASSERT_FALSE(contains(avail, "C1"));
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 0);
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED), 1);

    return true;
}

// ─── Test 7: Assign with empty crane ID throws ────────────────────────────────

static bool test_assign_empty_crane_id() {
    CraneRegistry reg;
    reg.register_crane("C1");
    ASSERT_THROWS(reg.assign_crane("", "SHIP-001"));
    // C1 still available.
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 8: Assign with empty job ID throws ──────────────────────────────────

static bool test_assign_empty_job_id() {
    CraneRegistry reg;
    reg.register_crane("C1");
    ASSERT_THROWS(reg.assign_crane("C1", ""));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 9: Assign unregistered crane throws ─────────────────────────────────

static bool test_assign_unregistered() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.assign_crane("GHOST", "SHIP-001"));
    return true;
}

// ─── Test 10: Assign already-assigned crane throws ────────────────────────────

static bool test_assign_already_assigned() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    ASSERT_THROWS(reg.assign_crane("C1", "SHIP-002"));

    // State unchanged: still assigned to SHIP-001.
    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::ASSIGNED);
    ASSERT_EQ(cs.assigned_job, std::string("SHIP-001"));
    return true;
}

// ─── Test 11: Assign FAILED crane throws ─────────────────────────────────────

static bool test_assign_failed_crane() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.mark_failed("C1");
    ASSERT_THROWS(reg.assign_crane("C1", "SHIP-001"));

    // Crane remains FAILED.
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);
    return true;
}

// =============================================================================
// Release tests
// =============================================================================

// ─── Test 12: Successful release ─────────────────────────────────────────────

static bool test_release_success() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    reg.release_crane("C1");

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::AVAILABLE);
    ASSERT_TRUE(cs.assigned_job.empty());

    // Now shows up as available again.
    auto avail = reg.available_cranes();
    ASSERT_TRUE(contains(avail, "C1"));
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED), 0);
    return true;
}

// ─── Test 13: Release with empty crane ID throws ──────────────────────────────

static bool test_release_empty_id() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.release_crane(""));
    return true;
}

// ─── Test 14: Release already-available crane throws ─────────────────────────

static bool test_release_available_crane() {
    CraneRegistry reg;
    reg.register_crane("C1");
    ASSERT_THROWS(reg.release_crane("C1"));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 15: Release FAILED crane throws ────────────────────────────────────

static bool test_release_failed_crane() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.mark_failed("C1");
    ASSERT_THROWS(reg.release_crane("C1"));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);
    return true;
}

// ─── Test 16: Release unregistered crane throws ───────────────────────────────

static bool test_release_unregistered() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.release_crane("GHOST"));
    return true;
}

// =============================================================================
// Failure tests
// =============================================================================

// ─── Test 17: Fail an AVAILABLE crane ────────────────────────────────────────

static bool test_fail_available() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.mark_failed("C1");

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::FAILED);
    ASSERT_TRUE(cs.assigned_job.empty());

    // No longer in available list.
    auto avail = reg.available_cranes();
    ASSERT_FALSE(contains(avail, "C1"));
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 0);
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED), 1);
    return true;
}

// ─── Test 18: Fail an ASSIGNED crane (mid-job failure) ───────────────────────

static bool test_fail_assigned() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    reg.mark_failed("C1");

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::FAILED);
    // assigned_job is cleared when the crane fails.
    ASSERT_TRUE(cs.assigned_job.empty());

    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED), 1);
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED), 0);
    return true;
}

// ─── Test 19: Fail an already-FAILED crane throws ────────────────────────────

static bool test_fail_already_failed() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.mark_failed("C1");
    ASSERT_THROWS(reg.mark_failed("C1"));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);
    return true;
}

// ─── Test 20: Fail with empty crane ID throws ─────────────────────────────────

static bool test_fail_empty_id() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.mark_failed(""));
    return true;
}

// ─── Test 21: Fail unregistered crane throws ─────────────────────────────────

static bool test_fail_unregistered() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.mark_failed("GHOST"));
    return true;
}

// =============================================================================
// Recovery tests
// =============================================================================

// ─── Test 22: Successful recovery ────────────────────────────────────────────

static bool test_recover_success() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.mark_failed("C1");
    reg.recover_crane("C1");

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::AVAILABLE);
    ASSERT_TRUE(cs.assigned_job.empty());

    auto avail = reg.available_cranes();
    ASSERT_TRUE(contains(avail, "C1"));
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED), 0);
    return true;
}

// ─── Test 23: Recover then re-assign ─────────────────────────────────────────

static bool test_recover_then_assign() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.mark_failed("C1");
    reg.recover_crane("C1");
    reg.assign_crane("C1", "SHIP-002");

    CraneState cs = reg.get_crane("C1");
    ASSERT_TRUE(cs.status == CraneStatus::ASSIGNED);
    ASSERT_EQ(cs.assigned_job, std::string("SHIP-002"));
    return true;
}

// ─── Test 24: Recover an AVAILABLE crane throws ───────────────────────────────

static bool test_recover_available() {
    CraneRegistry reg;
    reg.register_crane("C1");
    ASSERT_THROWS(reg.recover_crane("C1"));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    return true;
}

// ─── Test 25: Recover an ASSIGNED crane throws ───────────────────────────────

static bool test_recover_assigned() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.assign_crane("C1", "SHIP-001");
    ASSERT_THROWS(reg.recover_crane("C1"));
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    return true;
}

// ─── Test 26: Recover with empty crane ID throws ──────────────────────────────

static bool test_recover_empty_id() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.recover_crane(""));
    return true;
}

// ─── Test 27: Recover unregistered crane throws ───────────────────────────────

static bool test_recover_unregistered() {
    CraneRegistry reg;
    ASSERT_THROWS(reg.recover_crane("GHOST"));
    return true;
}

// =============================================================================
// State-transition sequence tests
// =============================================================================

// ─── Test 28: Full lifecycle: register -> assign -> release -> assign again ───

static bool test_full_lifecycle() {
    CraneRegistry reg;
    reg.register_crane("C1");

    // Assign.
    reg.assign_crane("C1", "SHIP-001");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);

    // Release.
    reg.release_crane("C1");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);

    // Re-assign.
    reg.assign_crane("C1", "SHIP-002");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);
    ASSERT_EQ(reg.get_crane("C1").assigned_job, std::string("SHIP-002"));

    return true;
}

// ─── Test 29: Full failure-recovery lifecycle ─────────────────────────────────

static bool test_fail_recover_lifecycle() {
    CraneRegistry reg;
    reg.register_crane("C1");

    reg.assign_crane("C1", "SHIP-001");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);

    // Crane fails mid-job.
    reg.mark_failed("C1");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::FAILED);
    ASSERT_TRUE(reg.get_crane("C1").assigned_job.empty());

    // Crane recovered.
    reg.recover_crane("C1");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);

    // Back to work.
    reg.assign_crane("C1", "SHIP-003");
    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::ASSIGNED);

    return true;
}

// =============================================================================
// Multi-crane tests
// =============================================================================

// ─── Test 30: Multiple cranes with different statuses ────────────────────────

static bool test_multiple_cranes_mixed_status() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");
    reg.register_crane("C4");
    reg.register_crane("C5");

    // C2 assigned, C3 failed, C4 assigned then failed, rest available.
    reg.assign_crane("C2", "SHIP-A");
    reg.mark_failed("C3");
    reg.assign_crane("C4", "SHIP-B");
    reg.mark_failed("C4");

    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 2); // C1, C5
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED),  1); // C2
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),    2); // C3, C4

    auto avail = reg.available_cranes();
    ASSERT_EQ(static_cast<int>(avail.size()), 2);
    ASSERT_TRUE(contains(avail, "C1"));
    ASSERT_TRUE(contains(avail, "C5"));
    ASSERT_FALSE(contains(avail, "C2"));
    ASSERT_FALSE(contains(avail, "C3"));
    ASSERT_FALSE(contains(avail, "C4"));

    return true;
}

// ─── Test 31: all_cranes returns every registered crane ───────────────────────

static bool test_all_cranes() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");

    auto all = reg.all_cranes();
    ASSERT_EQ(static_cast<int>(all.size()), 3);

    // Each crane appears exactly once.
    int c1_count = 0, c2_count = 0, c3_count = 0;
    for (const auto& cs : all) {
        if (cs.crane_id == "C1") ++c1_count;
        if (cs.crane_id == "C2") ++c2_count;
        if (cs.crane_id == "C3") ++c3_count;
    }
    ASSERT_EQ(c1_count, 1);
    ASSERT_EQ(c2_count, 1);
    ASSERT_EQ(c3_count, 1);

    return true;
}

// ─── Test 32: available_cranes on empty registry ──────────────────────────────

static bool test_available_cranes_empty_registry() {
    CraneRegistry reg;
    auto avail = reg.available_cranes();
    ASSERT_EQ(static_cast<int>(avail.size()), 0);
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 0);
    return true;
}

// ─── Test 33: count_by_status totals equal total_cranes ───────────────────────

static bool test_status_counts_sum_to_total() {
    CraneRegistry reg;
    for (int i = 0; i < 6; ++i) {
        reg.register_crane("C" + std::to_string(i));
    }

    // Assign two, fail two, leave two available.
    reg.assign_crane("C0", "J0");
    reg.assign_crane("C1", "J1");
    reg.mark_failed("C2");
    reg.mark_failed("C3");

    int n_avail    = reg.count_by_status(CraneStatus::AVAILABLE);
    int n_assigned = reg.count_by_status(CraneStatus::ASSIGNED);
    int n_failed   = reg.count_by_status(CraneStatus::FAILED);

    ASSERT_EQ(n_avail,    2);
    ASSERT_EQ(n_assigned, 2);
    ASSERT_EQ(n_failed,   2);
    ASSERT_EQ(n_avail + n_assigned + n_failed, reg.total_cranes());

    return true;
}

// ─── Test 34: Assign multiple cranes to different jobs independently ──────────

static bool test_assign_multiple_independent() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");

    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");
    // C3 stays available.

    ASSERT_EQ(reg.get_crane("C1").assigned_job, std::string("SHIP-A"));
    ASSERT_EQ(reg.get_crane("C2").assigned_job, std::string("SHIP-B"));
    ASSERT_TRUE(reg.get_crane("C3").assigned_job.empty());
    ASSERT_TRUE(reg.get_crane("C3").status == CraneStatus::AVAILABLE);

    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED), 2);
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 1);

    return true;
}

// ─── Test 35: Release one of several assigned cranes ─────────────────────────

static bool test_release_one_of_several() {
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-B");

    reg.release_crane("C1");

    ASSERT_TRUE(reg.get_crane("C1").status == CraneStatus::AVAILABLE);
    ASSERT_TRUE(reg.get_crane("C2").status == CraneStatus::ASSIGNED);
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 1);
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED), 1);

    return true;
}

// =============================================================================
// ResourceManager integration / capacity-consistency tests
// =============================================================================
//
// These tests demonstrate the caller-mediated coupling pattern documented in
// crane_registry.h.  They do NOT claim automated synchronisation; they verify
// that both components can be used correctly together by a diligent caller.

// ─── Test 36: Allocating via RM and assigning cranes are consistent ───────────
// Scenario: 4 cranes in both systems; allocate 2 for a job, verify counts.

static bool test_rm_and_registry_consistent_allocation() {
    // ResourceManager holds aggregate counts.
    ResourceManager rm(0, 4, 0);   // 0 berths, 4 cranes, 0 trucks

    // CraneRegistry holds individual identities.
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");
    reg.register_crane("C4");

    // Caller allocates 2 cranes via RM.
    bool rm_ok = rm.allocate({"SHIP-A", 0, 2, 0});
    ASSERT_TRUE(rm_ok);

    // Caller assigns the specific cranes via registry.
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-A");

    // Verify RM view.
    ASSERT_EQ(rm.available().cranes, 2);
    ASSERT_EQ(rm.allocated().cranes, 2);

    // Verify registry view.
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 2);
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED),  2);

    return true;
}

// ─── Test 37: Release via RM and release_crane maintain consistency ───────────

static bool test_rm_and_registry_consistent_release() {
    ResourceManager rm(0, 4, 0);
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");
    reg.register_crane("C3");
    reg.register_crane("C4");

    // Allocate.
    rm.allocate({"SHIP-A", 0, 2, 0});
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-A");

    // Release.
    rm.release("SHIP-A");
    reg.release_crane("C1");
    reg.release_crane("C2");

    // Both views show fully-available state.
    ASSERT_EQ(rm.available().cranes, 4);
    ASSERT_EQ(reg.count_by_status(CraneStatus::AVAILABLE), 4);
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED),  0);

    return true;
}

// ─── Test 38: Failed crane — RM capacity diverges (known limitation) ──────────
// When a crane fails, CraneRegistry updates its per-crane view, but the
// aggregate capacity in ResourceManager does NOT automatically decrease.
// Callers must decide whether to reflect the failure in ResourceManager.
// This test documents the limitation explicitly rather than hiding it.

static bool test_rm_registry_divergence_on_failure() {
    ResourceManager rm(0, 2, 0);
    CraneRegistry reg;
    reg.register_crane("C1");
    reg.register_crane("C2");

    // Allocate both cranes.
    rm.allocate({"SHIP-A", 0, 2, 0});
    reg.assign_crane("C1", "SHIP-A");
    reg.assign_crane("C2", "SHIP-A");

    // C1 fails mid-job.
    reg.mark_failed("C1");

    // CraneRegistry: 1 FAILED, 1 ASSIGNED.
    ASSERT_EQ(reg.count_by_status(CraneStatus::FAILED),   1);
    ASSERT_EQ(reg.count_by_status(CraneStatus::ASSIGNED),  1);

    // ResourceManager: still shows 2 cranes allocated (it has no knowledge of
    // the individual crane failure).  This is the documented limitation.
    ASSERT_EQ(rm.allocated().cranes, 2);

    // The caller must decide: perhaps release the job's allocation partially,
    // or update the total capacity manually.  Neither is done automatically.

    return true;
}

// ─── Test 39: Double release via RM is rejected; registry double-release too ──

static bool test_double_release_rejected() {
    ResourceManager rm(0, 2, 0);
    CraneRegistry reg;
    reg.register_crane("C1");

    rm.allocate({"SHIP-A", 0, 1, 0});
    reg.assign_crane("C1", "SHIP-A");

    rm.release("SHIP-A");
    reg.release_crane("C1");

    // Second release is rejected by both components.
    ASSERT_THROWS(rm.release("SHIP-A"));
    ASSERT_THROWS(reg.release_crane("C1"));

    return true;
}

// ─── Test 40: Fabricated capacity — RM total vs registry total ────────────────
// The RM total capacity is fixed at construction; registering more cranes in
// CraneRegistry than the RM capacity does NOT increase RM's total capacity.
// This test shows that the two totals are independent (no fabricated capacity).

static bool test_no_fabricated_capacity() {
    // RM declares 2 crane slots.
    ResourceManager rm(0, 2, 0);

    // Registry has 5 cranes registered (more than RM knows about).
    CraneRegistry reg;
    for (int i = 1; i <= 5; ++i) {
        reg.register_crane("C" + std::to_string(i));
    }

    ASSERT_EQ(reg.total_cranes(), 5);
    ASSERT_EQ(rm.total_capacity().cranes, 2);   // unchanged

    // RM still only allows up to 2 concurrent crane slots.
    ASSERT_TRUE(rm.allocate({"SHIP-A", 0, 2, 0}));
    ASSERT_FALSE(rm.allocate({"SHIP-B", 0, 1, 0}));   // no capacity left

    return true;
}

// =============================================================================
// main
// =============================================================================

int main() {
    std::cout << "\n=== Crane Registry Test Suite ===\n\n";

    // ── Registration ──────────────────────────────────────────────────────────
    run_test("Register single crane",              test_register_single);
    run_test("Register multiple cranes",           test_register_multiple);
    run_test("Duplicate crane ID rejected",        test_register_duplicate);
    run_test("Empty crane ID rejected",            test_register_empty_id);
    run_test("get_crane for unregistered",         test_get_unregistered);

    // ── Assignment ────────────────────────────────────────────────────────────
    run_test("Successful assignment",              test_assign_success);
    run_test("Assign — empty crane ID throws",     test_assign_empty_crane_id);
    run_test("Assign — empty job ID throws",       test_assign_empty_job_id);
    run_test("Assign — unregistered crane throws", test_assign_unregistered);
    run_test("Assign — already-assigned throws",   test_assign_already_assigned);
    run_test("Assign — FAILED crane throws",       test_assign_failed_crane);

    // ── Release ───────────────────────────────────────────────────────────────
    run_test("Successful release",                 test_release_success);
    run_test("Release — empty ID throws",          test_release_empty_id);
    run_test("Release — AVAILABLE crane throws",   test_release_available_crane);
    run_test("Release — FAILED crane throws",      test_release_failed_crane);
    run_test("Release — unregistered throws",      test_release_unregistered);

    // ── Failure ───────────────────────────────────────────────────────────────
    run_test("Fail AVAILABLE crane",               test_fail_available);
    run_test("Fail ASSIGNED crane (mid-job)",      test_fail_assigned);
    run_test("Fail already-FAILED crane throws",   test_fail_already_failed);
    run_test("Fail — empty ID throws",             test_fail_empty_id);
    run_test("Fail — unregistered throws",         test_fail_unregistered);

    // ── Recovery ──────────────────────────────────────────────────────────────
    run_test("Successful recovery",                test_recover_success);
    run_test("Recover then re-assign",             test_recover_then_assign);
    run_test("Recover AVAILABLE crane throws",     test_recover_available);
    run_test("Recover ASSIGNED crane throws",      test_recover_assigned);
    run_test("Recover — empty ID throws",          test_recover_empty_id);
    run_test("Recover — unregistered throws",      test_recover_unregistered);

    // ── State-transition sequences ────────────────────────────────────────────
    run_test("Full lifecycle (assign/release/reassign)",  test_full_lifecycle);
    run_test("Fail-recovery lifecycle",                   test_fail_recover_lifecycle);

    // ── Multi-crane ───────────────────────────────────────────────────────────
    run_test("Multiple cranes mixed statuses",            test_multiple_cranes_mixed_status);
    run_test("all_cranes returns every crane",            test_all_cranes);
    run_test("available_cranes on empty registry",        test_available_cranes_empty_registry);
    run_test("Status counts sum to total_cranes",         test_status_counts_sum_to_total);
    run_test("Assign multiple cranes independently",      test_assign_multiple_independent);
    run_test("Release one of several assigned",           test_release_one_of_several);

    // ── ResourceManager integration ───────────────────────────────────────────
    run_test("RM+registry consistent allocation",         test_rm_and_registry_consistent_allocation);
    run_test("RM+registry consistent release",            test_rm_and_registry_consistent_release);
    run_test("RM/registry diverge on failure (documented)", test_rm_registry_divergence_on_failure);
    run_test("Double release rejected by both",           test_double_release_rejected);
    run_test("No fabricated capacity",                    test_no_fabricated_capacity);

    std::cout << "\n─── Summary ────────────────────────\n";
    std::cout << "  Total:  " << g_total  << "\n";
    std::cout << "  Passed: " << g_passed << "\n";
    std::cout << "  Failed: " << g_failed << "\n";
    std::cout << "────────────────────────────────────\n\n";

    return g_failed == 0 ? 0 : 1;
}
