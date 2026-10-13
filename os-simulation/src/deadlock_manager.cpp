#include "deadlock_manager.h"

#include <set>
#include <numeric>

// ─── ResourceSet helpers (file-local) ────────────────────────────────────────

static bool rs_leq(const ResourceSet& a, const ResourceSet& b) {
    return a.berths <= b.berths &&
           a.cranes <= b.cranes &&
           a.trucks <= b.trucks;
}

static ResourceSet rs_add(const ResourceSet& a, const ResourceSet& b) {
    return {a.berths + b.berths,
            a.cranes + b.cranes,
            a.trucks + b.trucks};
}

static ResourceSet rs_sub(const ResourceSet& a, const ResourceSet& b) {
    return {a.berths - b.berths,
            a.cranes - b.cranes,
            a.trucks - b.trucks};
}

static bool rs_non_negative(const ResourceSet& r) {
    return r.berths >= 0 && r.cranes >= 0 && r.trucks >= 0;
}

// ─── BankerState validation ─────────────────────────────────────────────────

static void validate_banker_state(const BankerState& state) {
    // Check total and available are non-negative.
    if (!rs_non_negative(state.total)) {
        throw std::invalid_argument(
            "Total capacity must be non-negative.");
    }
    if (!rs_non_negative(state.available)) {
        throw std::invalid_argument(
            "Available resources must be non-negative.");
    }

    std::set<std::string> seen_ids;
    ResourceSet sum_alloc = {0, 0, 0};

    for (const auto& job : state.jobs) {
        // Non-empty ID.
        if (job.job_id.empty()) {
            throw std::invalid_argument("Job ID must not be empty.");
        }
        // Unique ID.
        if (!seen_ids.insert(job.job_id).second) {
            throw std::invalid_argument(
                "Duplicate job ID: " + job.job_id);
        }
        // Non-negative allocation.
        if (!rs_non_negative(job.allocation)) {
            throw std::invalid_argument(
                "Allocation must be non-negative for job " + job.job_id);
        }
        // Non-negative max_need.
        if (!rs_non_negative(job.max_need)) {
            throw std::invalid_argument(
                "Max need must be non-negative for job " + job.job_id);
        }
        // Allocation <= max_need.
        if (!rs_leq(job.allocation, job.max_need)) {
            throw std::invalid_argument(
                "Allocation exceeds max need for job " + job.job_id);
        }

        sum_alloc = rs_add(sum_alloc, job.allocation);
    }

    // sum(allocations) + available == total.
    ResourceSet expected_total = rs_add(sum_alloc, state.available);
    if (expected_total.berths != state.total.berths ||
        expected_total.cranes != state.total.cranes ||
        expected_total.trucks != state.total.trucks) {
        throw std::invalid_argument(
            "Inconsistent state: sum(allocations) + available != total.");
    }
}

// ─── DetectionState validation ──────────────────────────────────────────────

static void validate_detection_state(const DetectionState& state) {
    if (!rs_non_negative(state.available)) {
        throw std::invalid_argument(
            "Available resources must be non-negative.");
    }

    std::set<std::string> seen_ids;

    for (const auto& job : state.jobs) {
        if (job.job_id.empty()) {
            throw std::invalid_argument("Job ID must not be empty.");
        }
        if (!seen_ids.insert(job.job_id).second) {
            throw std::invalid_argument(
                "Duplicate job ID: " + job.job_id);
        }
        if (!rs_non_negative(job.allocation)) {
            throw std::invalid_argument(
                "Allocation must be non-negative for job " + job.job_id);
        }
        if (!rs_non_negative(job.request)) {
            throw std::invalid_argument(
                "Request must be non-negative for job " + job.job_id);
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Banker's Safety Check
// ═════════════════════════════════════════════════════════════════════════════

SafetyResult check_safety(const BankerState& state) {
    validate_banker_state(state);

    const size_t n = state.jobs.size();
    SafetyResult result;

    // Empty system is trivially safe.
    if (n == 0) {
        result.is_safe = true;
        return result;
    }

    // Compute Need[i] = Max[i] - Allocation[i].
    std::vector<ResourceSet> need(n);
    for (size_t i = 0; i < n; ++i) {
        need[i] = rs_sub(state.jobs[i].max_need, state.jobs[i].allocation);
    }

    // Standard Banker's safety algorithm.
    ResourceSet work = state.available;
    std::vector<bool> finish(n, false);
    std::vector<std::string> sequence;
    sequence.reserve(n);

    for (size_t pass = 0; pass < n; ++pass) {
        bool found = false;
        for (size_t i = 0; i < n; ++i) {
            if (!finish[i] && rs_leq(need[i], work)) {
                // This job can finish; release its allocation.
                work = rs_add(work, state.jobs[i].allocation);
                finish[i] = true;
                sequence.push_back(state.jobs[i].job_id);
                found = true;
                break;  // restart scan from beginning
            }
        }
        if (!found) {
            break;
        }
    }

    result.is_safe = (sequence.size() == n);
    if (result.is_safe) {
        result.safe_sequence = std::move(sequence);
    }
    return result;
}

// ═════════════════════════════════════════════════════════════════════════════
// Banker's Resource-Request Evaluation
// ═════════════════════════════════════════════════════════════════════════════

RequestResult evaluate_request(const BankerState& state,
                               const std::string& job_id,
                               const ResourceSet& request) {
    // Validate base state first.
    validate_banker_state(state);

    // Validate job_id.
    if (job_id.empty()) {
        throw std::invalid_argument("Job ID must not be empty.");
    }
    if (!rs_non_negative(request)) {
        throw std::invalid_argument(
            "Request quantities must be non-negative for job " + job_id);
    }

    // Find the job index.
    size_t job_idx = state.jobs.size();  // sentinel
    for (size_t i = 0; i < state.jobs.size(); ++i) {
        if (state.jobs[i].job_id == job_id) {
            job_idx = i;
            break;
        }
    }
    if (job_idx == state.jobs.size()) {
        throw std::invalid_argument(
            "Job not found in state: " + job_id);
    }

    const BankerJob& job = state.jobs[job_idx];

    // Compute remaining need for this job.
    ResourceSet need = rs_sub(job.max_need, job.allocation);

    // Check: request <= Need?
    if (!rs_leq(request, need)) {
        return {RequestStatus::EXCEEDS_NEED, {}};
    }

    // Check: request <= Available?
    if (!rs_leq(request, state.available)) {
        return {RequestStatus::EXCEEDS_AVAILABLE, {}};
    }

    // Simulate granting: build a temporary state.
    BankerState tentative = state;
    tentative.available = rs_sub(tentative.available, request);
    tentative.jobs[job_idx].allocation = rs_add(
        tentative.jobs[job_idx].allocation, request);
    // max_need and total stay the same.

    SafetyResult safety = check_safety(tentative);

    if (safety.is_safe) {
        return {RequestStatus::GRANTED, std::move(safety)};
    } else {
        return {RequestStatus::UNSAFE, std::move(safety)};
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// Deadlock Detection
// ═════════════════════════════════════════════════════════════════════════════

DetectionResult detect_deadlock(const DetectionState& state) {
    validate_detection_state(state);

    const size_t n = state.jobs.size();
    DetectionResult result;

    if (n == 0) {
        result.has_deadlock = false;
        return result;
    }

    // Standard detection algorithm:
    //   1. Work = Available.
    //   2. Finish[i] = true if Allocation[i] == (0,0,0), else false.
    //      (Following the standard algorithm: a job holding nothing can
    //       finish without needing any resource release.)
    //   3. Find unfinished job whose Request[i] <= Work.
    //   4. Mark finished, Work += Allocation[i].
    //   5. Repeat.  Remaining unfinished jobs are deadlocked.

    ResourceSet work = state.available;
    std::vector<bool> finish(n, false);

    // Initialize: jobs holding no resources are treated as able to finish.
    for (size_t i = 0; i < n; ++i) {
        const auto& a = state.jobs[i].allocation;
        if (a.berths == 0 && a.cranes == 0 && a.trucks == 0) {
            finish[i] = true;
        }
    }

    // Iterative scan.
    bool progress = true;
    while (progress) {
        progress = false;
        for (size_t i = 0; i < n; ++i) {
            if (!finish[i] && rs_leq(state.jobs[i].request, work)) {
                work = rs_add(work, state.jobs[i].allocation);
                finish[i] = true;
                progress = true;
            }
        }
    }

    // Collect deadlocked jobs.
    for (size_t i = 0; i < n; ++i) {
        if (!finish[i]) {
            result.deadlocked_jobs.push_back(state.jobs[i].job_id);
        }
    }

    result.has_deadlock = !result.deadlocked_jobs.empty();
    return result;
}
