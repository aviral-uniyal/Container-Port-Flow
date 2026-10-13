#ifndef DEADLOCK_MANAGER_H
#define DEADLOCK_MANAGER_H

#include "resource_manager.h"   // reuses ResourceSet

#include <string>
#include <vector>
#include <stdexcept>

// ─── Banker's Job State ──────────────────────────────────────────────────────
// Describes one job's resource position within a Banker's-algorithm snapshot.
//
// Invariant: need = max - allocation, component-wise.
// The module validates this; callers must not violate it.

struct BankerJob {
    std::string job_id;         // unique, non-empty
    ResourceSet allocation;     // currently held resources  (>= 0 each)
    ResourceSet max_need;       // maximum declared need     (>= allocation each)
};

// ─── Banker's System Snapshot ────────────────────────────────────────────────
// An explicit, immutable snapshot of the system state.
//
// The Banker's module operates on snapshots of allocations, maximum needs,
// and available resources.  It does NOT read from or write to a live
// ResourceManager.  Callers are responsible for constructing an accurate
// snapshot from whatever runtime state they maintain.
//
// Invariants (validated on use):
//   • All resource quantities are non-negative.
//   • allocation[i] <= max_need[i] component-wise for every job.
//   • sum(allocation[i]) + available <= total  component-wise.
//   • Job IDs are unique and non-empty.

struct BankerState {
    ResourceSet total;              // total system capacity
    ResourceSet available;          // currently free resources
    std::vector<BankerJob> jobs;    // per-job state
};

// ─── Safety-check result ─────────────────────────────────────────────────────

struct SafetyResult {
    bool is_safe;                               // true  ⇒  safe sequence found
    std::vector<std::string> safe_sequence;     // valid only when is_safe == true
};

// ─── Request-evaluation result ───────────────────────────────────────────────
// Returned by evaluate_request.  Distinguishes three rejection reasons.

enum class RequestStatus {
    GRANTED,                // request can be safely granted
    EXCEEDS_NEED,           // request > remaining need for that job
    EXCEEDS_AVAILABLE,      // request > currently available resources
    UNSAFE                  // granting would produce an unsafe state
};

struct RequestResult {
    RequestStatus status;
    SafetyResult  safety;   // populated when status == GRANTED or UNSAFE
};

// ─── Deadlock-detection snapshot ─────────────────────────────────────────────
// For detection we use *outstanding requests* (not max claims).

struct DetectionJob {
    std::string job_id;         // unique, non-empty
    ResourceSet allocation;     // currently held resources  (>= 0 each)
    ResourceSet request;        // current outstanding request (>= 0 each)
};

struct DetectionState {
    ResourceSet available;              // currently free resources
    std::vector<DetectionJob> jobs;     // per-job state
};

struct DetectionResult {
    bool has_deadlock;                              // true ⇒ deadlocked jobs found
    std::vector<std::string> deadlocked_jobs;       // IDs of deadlocked jobs
};

// ═════════════════════════════════════════════════════════════════════════════
// Public API — free functions
// ═════════════════════════════════════════════════════════════════════════════

// ─── Banker's safety check ───────────────────────────────────────────────────
// Runs the Banker's safety algorithm on the given snapshot.
//
// Algorithm:
//   1. Work = Available.
//   2. Find an unfinished job whose Need <= Work (component-wise).
//   3. Mark it finished, Work += its Allocation.
//   4. Repeat until all finish (safe) or no eligible job remains (unsafe).
//
// Throws std::invalid_argument on inconsistent inputs:
//   • empty or duplicate job IDs
//   • negative resource quantities
//   • allocation > max_need for any job
//   • sum(allocations) + available != total (component-wise)

SafetyResult check_safety(const BankerState& state);

// ─── Banker's resource-request evaluation ────────────────────────────────────
// Side-effect-free.  Does NOT mutate `state`.
//
// Evaluates whether granting `request` (berths/cranes/trucks) to the job
// identified by `job_id` (which must exist in `state.jobs`) would leave the
// system in a safe state.
//
// Returns a RequestResult whose `status` distinguishes:
//   GRANTED           – safe to grant; `safety` contains the resulting
//                       safe sequence.
//   EXCEEDS_NEED      – request > Need[job_id] for some resource.
//   EXCEEDS_AVAILABLE  – request > Available for some resource.
//   UNSAFE            – granting is feasible but leaves the system unsafe.
//
// Throws std::invalid_argument on:
//   • empty job_id
//   • job_id not found in state
//   • negative request quantities
//   • inconsistent base state (same rules as check_safety)

RequestResult evaluate_request(const BankerState& state,
                               const std::string& job_id,
                               const ResourceSet& request);

// ─── Deadlock detection ──────────────────────────────────────────────────────
// Uses the standard detection algorithm on current allocations and outstanding
// requests (NOT maximum claims).
//
// Algorithm:
//   1. Work = Available.
//   2. Find an unfinished job whose outstanding Request <= Work.
//   3. Mark it finished, Work += its Allocation.
//   4. Repeat.  Jobs remaining unfinished are deadlocked.
//
// A job holding zero resources is treated as potentially able to finish
// (following the standard algorithm's Finish[i] initialization rule).
//
// Note: a blocked job is not automatically deadlocked; the result depends
// on the supplied resource-allocation and outstanding-request snapshot.
//
// Throws std::invalid_argument on:
//   • empty or duplicate job IDs
//   • negative resource quantities

DetectionResult detect_deadlock(const DetectionState& state);

#endif // DEADLOCK_MANAGER_H
