#ifndef DISRUPTION_MANAGER_H
#define DISRUPTION_MANAGER_H

#include "crane_registry.h"   // CraneRegistry, CraneStatus, CraneState

#include <string>
#include <unordered_map>
#include <vector>
#include <stdexcept>

// ─── Job progress snapshot ────────────────────────────────────────────────────
// Captures the state of a job at the moment its assigned crane fails.
// DisruptionManager preserves this before calling mark_failed(), which clears
// the assigned_job field on the CraneState.
//
// Fields:
//   job_id           – the job the failed crane was serving
//   crane_id         – the crane that failed
//   work_completed   – units of work already done by this crane for the job
//   work_remaining   – units of work still needed (must be > 0 on failure)
//
// Invariant: work_completed >= 0 && work_remaining > 0 at the moment of
// failure capture.  The caller supplies these via fail_assigned_crane().

struct JobProgress {
    std::string job_id;
    std::string crane_id;
    int work_completed;    // >= 0
    int work_remaining;    // > 0  (always positive on failure)
};

// ─── Disruption event kind ────────────────────────────────────────────────────

enum class DisruptionEvent {
    CRANE_FAILED,    // crane transitioned to FAILED
    CRANE_RECOVERED  // crane transitioned from FAILED to AVAILABLE
};

// ─── Per-event result ─────────────────────────────────────────────────────────
// Returned by every event handler.

struct DisruptionResult {
    DisruptionEvent event;
    std::string     crane_id;

    // Populated only when event == CRANE_FAILED and the crane was ASSIGNED.
    // Empty (job_id == "") when the crane was AVAILABLE (idle) at failure time.
    JobProgress     affected_job;   // affected_job.job_id is "" when not applicable
};

// ─── Disruption manager ───────────────────────────────────────────────────────
// Handles crane failure and recovery events on top of an existing CraneRegistry.
//
// Responsibilities
// ────────────────
//   1. Idle-crane failure:  mark an AVAILABLE crane FAILED; no job is affected.
//   2. Assigned-crane failure: capture job progress *before* calling
//      mark_failed() (which clears the crane's assigned_job field), then
//      delegate to the registry.
//   3. Crane recovery: delegate recover_crane() to the registry.
//   4. Preserve job-progress records so callers can reschedule interrupted work.
//
// ResourceManager coordination (Part 1 scope)
// ─────────────────────────────────────────────
//   DisruptionManager does NOT call ResourceManager::release() when a single
//   crane fails.  The reason: ResourceManager allocates resources *per job*
//   (berths + cranes + trucks atomically).  Releasing on a single-crane failure
//   would free the berth and trucks for a job that is still occupying them.
//
//   Required caller coordination:
//     - The caller must NOT re-register the job's crane allocation in RM while
//       cranes remain failed.  The job's RM slot stays active until the job
//       is fully cancelled or completes.
//     - If the caller decides to cancel the job entirely, it must call
//       ResourceManager::release(job_id) independently.
//     - A helper, affected_jobs(), exposes all preserved job-progress records
//       so the caller can make that decision.
//
//   This boundary prevents double releases, fabricated capacity, and incorrect
//   accounting without requiring DisruptionManager to own RM state.
//
// Deterministic behaviour for invalid/repeated events
// ────────────────────────────────────────────────────
//   All error conditions throw std::invalid_argument:
//     * Empty crane_id
//     * Unregistered crane_id
//     * Failing a crane that is already FAILED
//     * Recovering a crane that is not FAILED
//     * Passing negative work_completed or non-positive work_remaining
//   These rules are consistent with CraneRegistry and ResourceManager
//   conventions used throughout this project.

class DisruptionManager {
public:
    // Construct with a reference to an existing CraneRegistry.
    // The registry must outlive this DisruptionManager.
    explicit DisruptionManager(CraneRegistry& registry);

    // ── Failure handling ──────────────────────────────────────────────────────

    // Fail an AVAILABLE (idle) crane.
    // Delegates to CraneRegistry::mark_failed().
    // Returns a DisruptionResult with event == CRANE_FAILED and
    //   affected_job.job_id == "" (no job interrupted).
    //
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * crane not registered
    //   * crane is not AVAILABLE (already ASSIGNED or FAILED)
    DisruptionResult fail_idle_crane(const std::string& crane_id);

    // Fail an ASSIGNED crane.
    // Captures job progress BEFORE calling mark_failed() (which clears the
    // assigned_job field on the CraneState).
    // Preserves the JobProgress record internally for later retrieval.
    //
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * crane not registered
    //   * crane is not ASSIGNED (AVAILABLE or FAILED)
    //   * work_completed < 0
    //   * work_remaining <= 0
    DisruptionResult fail_assigned_crane(const std::string& crane_id,
                                         int work_completed,
                                         int work_remaining);

    // ── Recovery handling ─────────────────────────────────────────────────────

    // Recover a FAILED crane to AVAILABLE.
    // Delegates to CraneRegistry::recover_crane().
    // Returns a DisruptionResult with event == CRANE_RECOVERED.
    //
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * crane not registered
    //   * crane is not FAILED
    DisruptionResult recover_crane(const std::string& crane_id);

    // ── Job-progress queries ──────────────────────────────────────────────────

    // Returns true if there is a preserved job-progress record for this crane.
    // (The crane may now be FAILED or have been reassigned after recovery.)
    bool has_job_progress(const std::string& crane_id) const;

    // Returns the preserved job-progress for the given crane.
    // Throws std::invalid_argument if no record exists for this crane.
    JobProgress get_job_progress(const std::string& crane_id) const;

    // Returns all preserved job-progress records (one per failed-assigned crane
    // since this DisruptionManager was constructed, or since the last clear).
    std::vector<JobProgress> affected_jobs() const;

    // Remove the preserved job-progress record for a specific crane.
    // Called when the caller has handled the interrupted job (rescheduled or
    // cancelled) and wants to clean up the record.
    // Throws std::invalid_argument if no record exists for this crane.
    void clear_job_progress(const std::string& crane_id);

    // Remove all preserved job-progress records.
    void clear_all_job_progress();

private:
    CraneRegistry& registry_;

    // job-progress records keyed by crane_id.
    std::unordered_map<std::string, JobProgress> job_progress_;
};

#endif // DISRUPTION_MANAGER_H
