// ─── Resource Manager Test Suite ─────────────────────────────────────────────
// Self-contained test harness — no external frameworks required.
// Compile:  g++ -std=c++17 -I../include resource_manager_tests.cpp ../src/resource_manager.cpp -o resource_manager_tests
// Run:      ./resource_manager_tests

#include "resource_manager.h"

#include <iostream>
#include <string>

// ─── Minimal test infrastructure ─────────────────────────────────────────────
// Mirrors the conventions used by scheduler_tests.cpp.

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
// All tests use synthetic test-fixture capacities, not historical port data.
// Default fixture: 3 berths, 4 cranes, 6 trucks.
// ═════════════════════════════════════════════════════════════════════════════

// ─── Test 1: Initial capacities and availability ─────────────────────────────

static bool test_initial_capacity() {
    // Synthetic fixture: 3 berths, 4 cranes, 6 trucks
    ResourceManager rm(3, 4, 6);

    auto total = rm.total_capacity();
    ASSERT_EQ(total.berths, 3);
    ASSERT_EQ(total.cranes, 4);
    ASSERT_EQ(total.trucks, 6);

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 3);
    ASSERT_EQ(avail.cranes, 4);
    ASSERT_EQ(avail.trucks, 6);

    auto alloc = rm.allocated();
    ASSERT_EQ(alloc.berths, 0);
    ASSERT_EQ(alloc.cranes, 0);
    ASSERT_EQ(alloc.trucks, 0);

    ASSERT_EQ(rm.active_job_count(), 0);
    return true;
}

// ─── Test 2: Successful allocation ───────────────────────────────────────────

static bool test_successful_allocation() {
    ResourceManager rm(3, 4, 6);
    ResourceRequest req{"V1", 1, 2, 3};

    ASSERT_TRUE(rm.can_allocate(req));
    ASSERT_TRUE(rm.allocate(req));

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 2);
    ASSERT_EQ(avail.cranes, 2);
    ASSERT_EQ(avail.trucks, 3);

    auto alloc = rm.allocated();
    ASSERT_EQ(alloc.berths, 1);
    ASSERT_EQ(alloc.cranes, 2);
    ASSERT_EQ(alloc.trucks, 3);

    ASSERT_EQ(rm.active_job_count(), 1);
    ASSERT_TRUE(rm.has_active_job("V1"));

    auto job_alloc = rm.get_job_allocation("V1");
    ASSERT_EQ(job_alloc.berths, 1);
    ASSERT_EQ(job_alloc.cranes, 2);
    ASSERT_EQ(job_alloc.trucks, 3);

    return true;
}

// ─── Test 3: Insufficient resources ──────────────────────────────────────────

static bool test_insufficient_resources() {
    ResourceManager rm(3, 4, 6);

    // Allocate most resources first.
    ASSERT_TRUE(rm.allocate({"V1", 2, 3, 5}));

    // Remaining: 1 berth, 1 crane, 1 truck
    // This request needs 2 berths — should fail.
    ResourceRequest req{"V2", 2, 1, 1};
    ASSERT_FALSE(rm.can_allocate(req));
    ASSERT_FALSE(rm.allocate(req));

    // State unchanged after failed allocation.
    ASSERT_EQ(rm.active_job_count(), 1);
    ASSERT_FALSE(rm.has_active_job("V2"));

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 1);
    ASSERT_EQ(avail.cranes, 1);
    ASSERT_EQ(avail.trucks, 1);

    return true;
}

// ─── Test 4: Competing jobs and capacity limits ──────────────────────────────

static bool test_competing_jobs() {
    // Synthetic fixture: 2 berths, 3 cranes, 4 trucks
    ResourceManager rm(2, 3, 4);

    // V1 takes 1 berth, 2 cranes, 2 trucks
    ASSERT_TRUE(rm.allocate({"V1", 1, 2, 2}));
    // V2 takes 1 berth, 1 crane, 2 trucks (uses remaining capacity exactly)
    ASSERT_TRUE(rm.allocate({"V2", 1, 1, 2}));

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 0);
    ASSERT_EQ(avail.cranes, 0);
    ASSERT_EQ(avail.trucks, 0);

    // V3 cannot get any resources.
    ASSERT_FALSE(rm.allocate({"V3", 1, 0, 0}));
    ASSERT_FALSE(rm.allocate({"V3", 0, 1, 0}));
    ASSERT_FALSE(rm.allocate({"V3", 0, 0, 1}));

    ASSERT_EQ(rm.active_job_count(), 2);
    return true;
}

// ─── Test 5: Release and subsequent allocation ──────────────────────────────

static bool test_release_and_reallocate() {
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 2, 3, 5}));
    ASSERT_FALSE(rm.allocate({"V2", 2, 2, 2}));  // insufficient berths

    // Release V1.
    rm.release("V1");
    ASSERT_FALSE(rm.has_active_job("V1"));
    ASSERT_EQ(rm.active_job_count(), 0);

    // Resources fully restored.
    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 3);
    ASSERT_EQ(avail.cranes, 4);
    ASSERT_EQ(avail.trucks, 6);

    // Now V2 can be allocated.
    ASSERT_TRUE(rm.allocate({"V2", 2, 2, 2}));
    ASSERT_TRUE(rm.has_active_job("V2"));

    return true;
}

// ─── Test 6: Duplicate job allocation ────────────────────────────────────────

static bool test_duplicate_allocation() {
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 1, 1, 1}));

    // Trying to allocate V1 again should throw.
    ASSERT_THROWS(rm.allocate({"V1", 1, 1, 1}));

    // State unchanged: V1 still active with original allocation.
    ASSERT_EQ(rm.active_job_count(), 1);
    auto job_alloc = rm.get_job_allocation("V1");
    ASSERT_EQ(job_alloc.berths, 1);
    ASSERT_EQ(job_alloc.cranes, 1);
    ASSERT_EQ(job_alloc.trucks, 1);

    return true;
}

// ─── Test 7: Invalid / negative resource requests ────────────────────────────

static bool test_negative_request() {
    ResourceManager rm(3, 4, 6);

    ASSERT_THROWS(rm.allocate({"V1", -1, 1, 1}));
    ASSERT_THROWS(rm.allocate({"V1", 1, -1, 1}));
    ASSERT_THROWS(rm.allocate({"V1", 1, 1, -1}));

    // State unchanged.
    ASSERT_EQ(rm.active_job_count(), 0);

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 3);
    ASSERT_EQ(avail.cranes, 4);
    ASSERT_EQ(avail.trucks, 6);

    return true;
}

static bool test_empty_job_id_allocate() {
    ResourceManager rm(3, 4, 6);
    ASSERT_THROWS(rm.allocate({"", 1, 1, 1}));
    ASSERT_EQ(rm.active_job_count(), 0);
    return true;
}

static bool test_request_exceeds_total() {
    ResourceManager rm(3, 4, 6);
    ASSERT_THROWS(rm.allocate({"V1", 4, 1, 1}));   // berths > total
    ASSERT_THROWS(rm.allocate({"V1", 1, 5, 1}));   // cranes > total
    ASSERT_THROWS(rm.allocate({"V1", 1, 1, 7}));   // trucks > total
    ASSERT_EQ(rm.active_job_count(), 0);
    return true;
}

// ─── Test 8: Invalid release / unknown job ID ────────────────────────────────

static bool test_release_unknown_job() {
    ResourceManager rm(3, 4, 6);
    ASSERT_THROWS(rm.release("NONEXISTENT"));
    return true;
}

static bool test_release_empty_job_id() {
    ResourceManager rm(3, 4, 6);
    ASSERT_THROWS(rm.release(""));
    return true;
}

static bool test_double_release() {
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 1, 1, 1}));
    rm.release("V1");
    ASSERT_THROWS(rm.release("V1"));  // already released
    return true;
}

// ─── Test 9: Failed allocation leaves state unchanged ────────────────────────

static bool test_failed_allocation_no_side_effects() {
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 2, 3, 4}));

    auto avail_before = rm.available();
    int count_before = rm.active_job_count();

    // This should fail (insufficient berths: need 2, have 1).
    ASSERT_FALSE(rm.allocate({"V2", 2, 1, 1}));

    // Verify nothing changed.
    auto avail_after = rm.available();
    ASSERT_EQ(avail_after.berths, avail_before.berths);
    ASSERT_EQ(avail_after.cranes, avail_before.cranes);
    ASSERT_EQ(avail_after.trucks, avail_before.trucks);
    ASSERT_EQ(rm.active_job_count(), count_before);
    ASSERT_FALSE(rm.has_active_job("V2"));

    return true;
}

// ─── Test 10: Resource accounting invariants ─────────────────────────────────

static bool test_accounting_invariants() {
    ResourceManager rm(5, 6, 8);

    ASSERT_TRUE(rm.allocate({"V1", 2, 1, 3}));
    ASSERT_TRUE(rm.allocate({"V2", 1, 3, 2}));
    ASSERT_TRUE(rm.allocate({"V3", 1, 1, 1}));

    // Invariant: total = available + allocated
    auto total = rm.total_capacity();
    auto avail = rm.available();
    auto alloc = rm.allocated();

    ASSERT_EQ(total.berths, avail.berths + alloc.berths);
    ASSERT_EQ(total.cranes, avail.cranes + alloc.cranes);
    ASSERT_EQ(total.trucks, avail.trucks + alloc.trucks);

    // Expected allocated: 2+1+1=4 berths, 1+3+1=5 cranes, 3+2+1=6 trucks
    ASSERT_EQ(alloc.berths, 4);
    ASSERT_EQ(alloc.cranes, 5);
    ASSERT_EQ(alloc.trucks, 6);
    ASSERT_EQ(avail.berths, 1);
    ASSERT_EQ(avail.cranes, 1);
    ASSERT_EQ(avail.trucks, 2);

    // Release V2, verify invariant still holds.
    rm.release("V2");

    total = rm.total_capacity();
    avail = rm.available();
    alloc = rm.allocated();

    ASSERT_EQ(total.berths, avail.berths + alloc.berths);
    ASSERT_EQ(total.cranes, avail.cranes + alloc.cranes);
    ASSERT_EQ(total.trucks, avail.trucks + alloc.trucks);

    ASSERT_EQ(alloc.berths, 3);
    ASSERT_EQ(alloc.cranes, 2);
    ASSERT_EQ(alloc.trucks, 4);

    ASSERT_EQ(rm.active_job_count(), 2);

    return true;
}

// ─── Test 11: Zero resource requirements ─────────────────────────────────────
// A job requesting zero of every resource is valid and succeeds.
// It occupies a slot in active allocations (tracked by ID) but consumes nothing.

static bool test_zero_resource_request() {
    ResourceManager rm(3, 4, 6);
    ResourceRequest req{"V0", 0, 0, 0};

    ASSERT_TRUE(rm.can_allocate(req));
    ASSERT_TRUE(rm.allocate(req));

    // Available unchanged.
    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 3);
    ASSERT_EQ(avail.cranes, 4);
    ASSERT_EQ(avail.trucks, 6);

    // But job is tracked.
    ASSERT_EQ(rm.active_job_count(), 1);
    ASSERT_TRUE(rm.has_active_job("V0"));

    auto job_alloc = rm.get_job_allocation("V0");
    ASSERT_EQ(job_alloc.berths, 0);
    ASSERT_EQ(job_alloc.cranes, 0);
    ASSERT_EQ(job_alloc.trucks, 0);

    // Release succeeds.
    rm.release("V0");
    ASSERT_EQ(rm.active_job_count(), 0);

    return true;
}

static bool test_partial_zero_request() {
    // A job can request zero of some resources and positive of others.
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 1, 0, 3}));

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 2);
    ASSERT_EQ(avail.cranes, 4);  // unchanged
    ASSERT_EQ(avail.trucks, 3);

    return true;
}

// ─── Test 12: Negative capacity construction ─────────────────────────────────

static bool test_negative_capacity_construction() {
    ASSERT_THROWS(ResourceManager(-1, 4, 6));
    ASSERT_THROWS(ResourceManager(3, -1, 6));
    ASSERT_THROWS(ResourceManager(3, 4, -1));
    return true;
}

// ─── Test 13: Zero capacity construction ─────────────────────────────────────

static bool test_zero_capacity_construction() {
    // All-zero capacity is valid: no resources to allocate.
    ResourceManager rm(0, 0, 0);

    auto total = rm.total_capacity();
    ASSERT_EQ(total.berths, 0);
    ASSERT_EQ(total.cranes, 0);
    ASSERT_EQ(total.trucks, 0);

    // A zero-resource request still works.
    ASSERT_TRUE(rm.allocate({"V1", 0, 0, 0}));
    ASSERT_EQ(rm.active_job_count(), 1);

    // Any positive request fails validation (exceeds total).
    ASSERT_THROWS(rm.allocate({"V2", 1, 0, 0}));

    return true;
}

// ─── Test 14: can_allocate validation throws ─────────────────────────────────

static bool test_can_allocate_validation() {
    ResourceManager rm(3, 4, 6);

    ASSERT_THROWS(rm.can_allocate({"", 1, 1, 1}));       // empty ID
    ASSERT_THROWS(rm.can_allocate({"V1", -1, 1, 1}));    // negative qty
    ASSERT_THROWS(rm.can_allocate({"V1", 4, 1, 1}));     // exceeds total

    // Valid but insufficient — returns false, does not throw.
    ASSERT_TRUE(rm.allocate({"V1", 3, 4, 6}));  // exhaust all
    ASSERT_FALSE(rm.can_allocate({"V2", 1, 0, 0}));

    return true;
}

// ─── Test 15: get_job_allocation for unknown job ─────────────────────────────

static bool test_get_allocation_unknown() {
    ResourceManager rm(3, 4, 6);
    ASSERT_THROWS(rm.get_job_allocation("GHOST"));
    return true;
}

// ─── Test 16: Many allocations and releases stress test ──────────────────────

static bool test_many_allocations() {
    // Synthetic fixture: 10 berths, 10 cranes, 10 trucks
    ResourceManager rm(10, 10, 10);

    // Allocate 10 jobs, each taking 1 of each resource.
    for (int i = 0; i < 10; ++i) {
        std::string id = "J" + std::to_string(i);
        ASSERT_TRUE(rm.allocate({id, 1, 1, 1}));
    }

    ASSERT_EQ(rm.active_job_count(), 10);
    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 0);
    ASSERT_EQ(avail.cranes, 0);
    ASSERT_EQ(avail.trucks, 0);

    // 11th job fails.
    ASSERT_FALSE(rm.allocate({"J10", 1, 1, 1}));

    // Release odd-numbered jobs.
    for (int i = 1; i < 10; i += 2) {
        rm.release("J" + std::to_string(i));
    }

    ASSERT_EQ(rm.active_job_count(), 5);
    avail = rm.available();
    ASSERT_EQ(avail.berths, 5);
    ASSERT_EQ(avail.cranes, 5);
    ASSERT_EQ(avail.trucks, 5);

    // Now re-allocate those 5 slots.
    for (int i = 0; i < 5; ++i) {
        std::string id = "N" + std::to_string(i);
        ASSERT_TRUE(rm.allocate({id, 1, 1, 1}));
    }

    ASSERT_EQ(rm.active_job_count(), 10);
    avail = rm.available();
    ASSERT_EQ(avail.berths, 0);
    ASSERT_EQ(avail.cranes, 0);
    ASSERT_EQ(avail.trucks, 0);

    // Verify invariant.
    auto total = rm.total_capacity();
    auto alloc = rm.allocated();
    ASSERT_EQ(total.berths, avail.berths + alloc.berths);
    ASSERT_EQ(total.cranes, avail.cranes + alloc.cranes);
    ASSERT_EQ(total.trucks, avail.trucks + alloc.trucks);

    return true;
}

// ─── Test 17: Allocation atomicity across all three resources ────────────────

static bool test_atomicity_all_resources() {
    // Ensure that if even one resource type is insufficient, none are deducted.
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 2, 3, 0}));

    // Remaining: 1 berth, 1 crane, 6 trucks
    // V2 needs: 1 berth, 2 cranes, 1 truck — cranes insufficient
    auto avail_before = rm.available();
    ASSERT_FALSE(rm.allocate({"V2", 1, 2, 1}));

    // Berths should NOT have been deducted.
    auto avail_after = rm.available();
    ASSERT_EQ(avail_after.berths, avail_before.berths);
    ASSERT_EQ(avail_after.cranes, avail_before.cranes);
    ASSERT_EQ(avail_after.trucks, avail_before.trucks);

    return true;
}

// ─── Test 18: Exact capacity allocation ──────────────────────────────────────

static bool test_exact_capacity() {
    ResourceManager rm(3, 4, 6);

    // Allocate exactly the total capacity.
    ASSERT_TRUE(rm.allocate({"V1", 3, 4, 6}));

    auto avail = rm.available();
    ASSERT_EQ(avail.berths, 0);
    ASSERT_EQ(avail.cranes, 0);
    ASSERT_EQ(avail.trucks, 0);

    // Only zero-resource requests can now succeed.
    ASSERT_TRUE(rm.allocate({"V2", 0, 0, 0}));
    ASSERT_FALSE(rm.allocate({"V3", 0, 0, 1}));

    return true;
}

// ─── Test 19: has_active_job before and after ────────────────────────────────

static bool test_has_active_job_lifecycle() {
    ResourceManager rm(3, 4, 6);

    ASSERT_FALSE(rm.has_active_job("V1"));
    ASSERT_TRUE(rm.allocate({"V1", 1, 1, 1}));
    ASSERT_TRUE(rm.has_active_job("V1"));
    rm.release("V1");
    ASSERT_FALSE(rm.has_active_job("V1"));

    return true;
}

// ─── Test 20: can_allocate does not modify state ─────────────────────────────

static bool test_can_allocate_no_side_effects() {
    ResourceManager rm(3, 4, 6);
    ASSERT_TRUE(rm.allocate({"V1", 1, 2, 3}));

    auto avail_before = rm.available();
    int count_before = rm.active_job_count();

    // Call can_allocate several times.
    rm.can_allocate({"V2", 1, 1, 1});
    rm.can_allocate({"V3", 2, 2, 3});
    rm.can_allocate({"V4", 0, 0, 0});

    auto avail_after = rm.available();
    ASSERT_EQ(avail_after.berths, avail_before.berths);
    ASSERT_EQ(avail_after.cranes, avail_before.cranes);
    ASSERT_EQ(avail_after.trucks, avail_before.trucks);
    ASSERT_EQ(rm.active_job_count(), count_before);

    return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// main
// ═════════════════════════════════════════════════════════════════════════════

int main() {
    std::cout << "\n=== Resource Manager Test Suite ===\n\n";

    run_test("Initial capacities and availability",      test_initial_capacity);
    run_test("Successful allocation",                    test_successful_allocation);
    run_test("Insufficient resources",                   test_insufficient_resources);
    run_test("Competing jobs and capacity limits",       test_competing_jobs);
    run_test("Release and subsequent allocation",        test_release_and_reallocate);
    run_test("Duplicate job allocation",                 test_duplicate_allocation);
    run_test("Negative resource request",                test_negative_request);
    run_test("Empty job ID on allocate",                 test_empty_job_id_allocate);
    run_test("Request exceeds total capacity",           test_request_exceeds_total);
    run_test("Release unknown job",                      test_release_unknown_job);
    run_test("Release empty job ID",                     test_release_empty_job_id);
    run_test("Double release",                           test_double_release);
    run_test("Failed allocation no side effects",        test_failed_allocation_no_side_effects);
    run_test("Accounting invariants",                    test_accounting_invariants);
    run_test("Zero resource request",                    test_zero_resource_request);
    run_test("Partial zero request",                     test_partial_zero_request);
    run_test("Negative capacity construction",           test_negative_capacity_construction);
    run_test("Zero capacity construction",               test_zero_capacity_construction);
    run_test("can_allocate validation throws",           test_can_allocate_validation);
    run_test("get_job_allocation unknown job",           test_get_allocation_unknown);
    run_test("Many allocations and releases",            test_many_allocations);
    run_test("Atomicity across all resources",           test_atomicity_all_resources);
    run_test("Exact capacity allocation",                test_exact_capacity);
    run_test("has_active_job lifecycle",                  test_has_active_job_lifecycle);
    run_test("can_allocate no side effects",             test_can_allocate_no_side_effects);

    std::cout << "\n─── Summary ────────────────────────\n";
    std::cout << "  Total:  " << g_total  << "\n";
    std::cout << "  Passed: " << g_passed << "\n";
    std::cout << "  Failed: " << g_failed << "\n";
    std::cout << "────────────────────────────────────\n\n";

    return g_failed == 0 ? 0 : 1;
}
